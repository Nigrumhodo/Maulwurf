"""U-S2-JL-03: deterministic RRF; dedupe/diversity before the top-k (L2.3, M6).

No database and no network: the fusion is pure (`app.services.search`), so the ticket promise
is tested directly — same input gives the same output, `RRF_K` is documented, ties break by
`chunk_id`, and the top-k is cut only after dedupe and diversity. The corpus is redacted
(`tests/fixtures/search_corpus.py`), allowed while D3-Texto is open.
"""

import uuid
from collections.abc import Sequence

from app.services.search import (
    CANDIDATES_PER_BRANCH,
    DEFAULT_TOP_K,
    MAX_CHUNKS_PER_AUDIO,
    RRF_K,
    Candidate,
    dedupe,
    diversify,
    merge,
    rrf_fuse,
)
from tests.fixtures import search_corpus as corpus


def _ranking(chunk_ids: Sequence[uuid.UUID]) -> list[Candidate]:
    return [
        Candidate(
            chunk_id=chunk_id,
            audio_id=corpus.audio_of(chunk_id),
            rank=rank,
            branch_score=0.0,
        )
        for rank, chunk_id in enumerate(chunk_ids, start=1)
    ]


def _ids(hits: Sequence[object]) -> list[uuid.UUID]:
    return [hit.chunk_id for hit in hits]  # type: ignore[attr-defined]


# --- 1. Determinism ------------------------------------------------------------------


def test_rrf_fusion_is_deterministic_for_same_input() -> None:
    """Same branches and same candidates → byte-for-byte identical order and scores."""
    rankings = [_ranking(corpus.LEXICAL_RANKING), _ranking(corpus.VECTOR_RANKING)]

    first = merge(rankings)
    second = merge(rankings)

    assert first == second
    assert _ids(first) == _ids(second)


# --- 2. Documented k -----------------------------------------------------------------


def test_rrf_uses_documented_k() -> None:
    """`RRF_K` is the documented contract constant and the score is `1 / (k + rank)`."""
    assert RRF_K == 60
    assert CANDIDATES_PER_BRANCH == 30
    assert DEFAULT_TOP_K == 8

    fused = rrf_fuse([_ranking((corpus.CHUNK_ES_DEFINITE, corpus.CHUNK_ES_CHAIN))])

    by_id = {hit.chunk_id: hit.score_rrf for hit in fused}
    assert by_id[corpus.CHUNK_ES_DEFINITE] == 1 / (RRF_K + 1)
    assert by_id[corpus.CHUNK_ES_CHAIN] == 1 / (RRF_K + 2)


# --- 3. Tie-break by chunk_id --------------------------------------------------------


def test_rrf_tie_break_by_chunk_id() -> None:
    """Equal scores are ordered by ascending `chunk_id` (stable, total order)."""
    # Each branch contributes its single candidate at rank 1: both score 1 / (k + 1).
    lower = corpus.CHUNK_ES_DEFINITE
    higher = corpus.CHUNK_EN_EIGEN
    assert lower < higher

    fused = rrf_fuse([_ranking((higher,)), _ranking((lower,))])

    assert _ids(fused) == [lower, higher]


# --- 4. Empty branch -----------------------------------------------------------------


def test_rrf_empty_branch_contributes_nothing() -> None:
    """An empty branch does not change the order of the other one."""
    only = corpus.CHUNK_ES_CHAIN

    fused = rrf_fuse([_ranking((only,)), []])

    assert _ids(fused) == [only]
    assert fused[0].score_rrf == 1 / (RRF_K + 1)


# --- 5. Chunk in both branches -------------------------------------------------------


def test_rrf_chunk_in_both_branches_sums_both_terms() -> None:
    """A chunk retrieved by both branches counts once and sums both reciprocal terms."""
    shared = corpus.CHUNK_ES_DEFINITE

    fused = rrf_fuse([_ranking((shared,)), _ranking((shared,))])

    assert _ids(fused) == [shared]
    assert fused[0].score_rrf == 2 / (RRF_K + 1)


# --- 6. Dedupe of overlapping content ------------------------------------------------


def test_dedupe_removes_overlapping_duplicate_content() -> None:
    """Same class + same content → one survivor (the best-ranked; here the first)."""
    fused = rrf_fuse(
        [_ranking((corpus.CHUNK_ES_DEFINITE, corpus.CHUNK_ES_DEFINITE_DUP))]
    )

    survivors = dedupe(fused, content_key=corpus.content_of)

    assert _ids(survivors) == [corpus.CHUNK_ES_DEFINITE]


# --- 7. Diversity --------------------------------------------------------------------


def test_diversity_soft_caps_chunks_per_audio() -> None:
    """Soft cap: at most `max_per_audio` per class in front; overflow is appended, never dropped."""
    a1, a2, a3 = (
        corpus.CHUNK_ES_DEFINITE,
        corpus.CHUNK_ES_CHAIN,
        corpus.CHUNK_ES_DEFINITE_DUP,
    )
    other = corpus.CHUNK_EN_EIGEN

    hits = rrf_fuse([_ranking((a1, a2, a3, other))])
    assert MAX_CHUNKS_PER_AUDIO == 2

    diverse = diversify(hits, max_per_audio=2)

    # First pass keeps two chunks of the dominant class plus the other class; the overflow
    # (a3) is appended afterwards so the list is not shortened.
    assert _ids(diverse) == [a1, a2, other, a3]
    assert _ids(diverse[:3]).count(a3) == 0
    # Documented contract: the cap is on the front of the list, so the class may still reappear
    # in the overflow (`merge` slices to `top_k` afterwards). A hard cap would shorten top-k.
    assert _ids(diverse).count(a3) == 1


# --- 8. Top-k after dedupe and diversity ---------------------------------------------


def test_top_k_is_applied_after_dedupe_and_diversity() -> None:
    """The cut is the last step: dedupe/diversity never leave the result short."""
    rankings = [_ranking(corpus.LEXICAL_RANKING), _ranking(corpus.VECTOR_RANKING)]

    merged = merge(rankings, top_k=3, content_key=corpus.content_of)
    assert len(merged) == 3
    assert len(set(_ids(merged))) == 3

    assert merge(rankings, top_k=0) == []


# --- 9. Redacted corpus, stable fused order ------------------------------------------


def test_redacted_corpus_produces_stable_fused_order() -> None:
    """End-to-end fusion over the redacted corpus: exact order, no branch overflow drift."""
    merged = merge(
        [_ranking(corpus.LEXICAL_RANKING), _ranking(corpus.VECTOR_RANKING)],
        top_k=10,
    )

    # c1 = 1/61 + 1/62 > c3 = 1/63 + 1/61 > (c2 = 1/62, c5 = 1/62 → tie by chunk_id).
    assert _ids(merged) == [
        corpus.CHUNK_ES_DEFINITE,
        corpus.CHUNK_EN_TRIG,
        corpus.CHUNK_ES_CHAIN,
        corpus.CHUNK_EN_EIGEN,
    ]
