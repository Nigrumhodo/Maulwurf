"""Redacted corpus for `U-S2-JL-03` (L2.3).

No real class text and no PII: the content is invented and obviously synthetic, allowed while
D3-Texto is open (S2 §0.6, guard rails 4/5). The chunk ids are fixed so the fused order and
the `chunk_id` tie-break are stable and reviewable by hand.
"""

import uuid

# Fixed ids: the last group orders them (…01 < …02 < …05) so a tie on `score_rrf` has a
# predictable winner (ascending `chunk_id`).
CHUNK_ES_DEFINITE = uuid.UUID("00000000-0000-4000-8000-000000000001")
CHUNK_ES_CHAIN = uuid.UUID("00000000-0000-4000-8000-000000000002")
CHUNK_EN_TRIG = uuid.UUID("00000000-0000-4000-8000-000000000003")
CHUNK_ES_DEFINITE_DUP = uuid.UUID("00000000-0000-4000-8000-000000000004")
CHUNK_EN_EIGEN = uuid.UUID("00000000-0000-4000-8000-000000000005")

AUDIO_CALCULUS = uuid.UUID("11111111-1111-4111-8111-111111111111")  # spanish
AUDIO_LINEAR_ALGEBRA = uuid.UUID("22222222-2222-4222-8222-222222222222")  # english

# chunk_id -> (audio_id, content, language). CHUNK_ES_DEFINITE_DUP overlaps CHUNK_ES_DEFINITE
# in the same class: the dedupe case of `merge`.
REDACTED_CHUNKS: dict[uuid.UUID, tuple[uuid.UUID, str, str]] = {
    CHUNK_ES_DEFINITE: (AUDIO_CALCULUS, "La integral definida y el teorema fundamental", "spanish"),
    CHUNK_ES_CHAIN: (AUDIO_CALCULUS, "Derivadas parciales y regla de la cadena", "spanish"),
    CHUNK_EN_TRIG: (
        AUDIO_LINEAR_ALGEBRA,
        "Trigonometric substitution in definite integrals",
        "english",
    ),
    CHUNK_ES_DEFINITE_DUP: (
        AUDIO_CALCULUS,
        "La integral definida y el teorema fundamental",
        "spanish",
    ),
    CHUNK_EN_EIGEN: (AUDIO_LINEAR_ALGEBRA, "Eigenvalues and eigenvectors of a matrix", "english"),
}

# Per-branch rankings (best first) over the redacted corpus. One chunk
# (CHUNK_ES_DEFINITE) is retrieved by both branches, which is the fusion case.
LEXICAL_RANKING: tuple[uuid.UUID, ...] = (CHUNK_ES_DEFINITE, CHUNK_ES_CHAIN, CHUNK_EN_TRIG)
VECTOR_RANKING: tuple[uuid.UUID, ...] = (CHUNK_EN_TRIG, CHUNK_ES_DEFINITE, CHUNK_EN_EIGEN)


def audio_of(chunk_id: uuid.UUID) -> uuid.UUID:
    return REDACTED_CHUNKS[chunk_id][0]


def content_of(chunk_id: uuid.UUID) -> str:
    return REDACTED_CHUNKS[chunk_id][1]
