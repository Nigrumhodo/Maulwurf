"""U-S2-JL-04: tenant filters in both branches before the top-k (L2.3, G3).

No database: the branch statements are compiled and inspected (filters and `LIMIT` in the
same statement), and `hybrid_search` runs against a stub session that records the executed
statements — which is how we prove there is **no query after the fusion** and that the
`hnsw.iterative_scan` GUC is set before the vector `SELECT`.
"""

import uuid
from collections import namedtuple
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy import Select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import search

USER_ID = uuid.uuid4()
SUBJECT_ID = uuid.uuid4()
VERSION = "idx-1"

_Row = namedtuple("_Row", "chunk_id audio_id score")
_ContentRow = namedtuple("_ContentRow", "id content")


def _chunk_id(suffix: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{suffix:012d}")


def _sql(statement: Select[Any]) -> str:
    return str(statement.compile())


def _lexical(
    *,
    limit: int = search.CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    return _sql(
        search.lexical_candidates_statement(
            user_id=USER_ID,
            version=VERSION,
            query_text="integral definida",
            limit=limit,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        )
    )


def _vector(
    *,
    limit: int = search.CANDIDATES_PER_BRANCH,
    subject_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    return _sql(
        search.vector_candidates_statement(
            user_id=USER_ID,
            version=VERSION,
            query_vector=[0.1, 0.2, 0.3],
            limit=limit,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
        )
    )


class _Rows:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Nested:
    """Async-context-manager stand-in for `AsyncSession.begin_nested()` (a SAVEPOINT)."""

    async def __aenter__(self) -> "_Nested":
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


class StubSession:
    """Fake `AsyncSession`: routes by statement shape, never touches network or DB."""

    def __init__(
        self,
        *,
        lexical: list[Any] | None = None,
        vector: list[Any] | None = None,
        content: list[Any] | None = None,
        generation: Any = None,
    ) -> None:
        self._lexical = lexical or []
        self._vector = vector or []
        self._content = content or []
        self._generation = generation
        self.executed: list[str] = []
        self.savepoints = 0

    def begin_nested(self) -> _Nested:
        self.savepoints += 1
        return _Nested()

    async def execute(self, statement: Any, *_: Any, **__: Any) -> _Rows:
        sql = str(statement)
        self.executed.append(sql)
        if "SET LOCAL hnsw.iterative_scan" in sql:
            return _Rows()
        if "websearch_to_tsquery" in sql:
            return _Rows(self._lexical)
        if "<=>" in sql:
            return _Rows(self._vector)
        if "chunks.content" in sql:
            return _Rows(self._content)
        if "FROM index_generations" in sql:
            return _Rows(scalar=self._generation)
        raise AssertionError(f"consulta no preparada en el stub: {sql}")


class UnsupportedScanSession(StubSession):
    """pgvector < 0.8: `SET LOCAL hnsw.iterative_scan` raises a driver error."""

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Rows:
        if "SET LOCAL hnsw.iterative_scan" in str(statement):
            raise ProgrammingError(
                str(statement), {}, Exception("unrecognized configuration parameter")
            )
        return await super().execute(statement, *args, **kwargs)


def _db(session: StubSession) -> AsyncSession:
    return cast(AsyncSession, session)


# --- 1-2. Tenant filter on the user id in both branches ------------------------------


def test_lexical_branch_filters_user_id() -> None:
    sql = _lexical()
    assert "chunks.user_id =" in sql
    assert "audios.user_id =" in sql
    assert "transcripts.user_id =" in sql


def test_vector_branch_filters_user_id() -> None:
    sql = _vector()
    assert "embeddings.user_id =" in sql
    assert "chunks.user_id =" in sql
    assert "audios.user_id =" in sql


# --- 3. Tombstone excluded -----------------------------------------------------------


def test_both_branches_exclude_tombstoned_audio() -> None:
    assert "audios.deleted_at IS NULL" in _lexical()
    assert "audios.deleted_at IS NULL" in _vector()


# --- 4. Active generation ------------------------------------------------------------


def test_both_branches_pin_active_generation() -> None:
    assert "chunks.index_version =" in _lexical()
    assert "chunks.index_version =" in _vector()


# --- 5. Old transcript version excluded ----------------------------------------------


def test_both_branches_exclude_old_transcript_version() -> None:
    expected = "transcripts.version = audios.active_transcript_version"
    assert expected in _lexical()
    assert expected in _vector()


# --- 6. Subject and date filters -----------------------------------------------------


def test_subject_and_date_filters_in_both_branches() -> None:
    for sql in (
        _lexical(subject_id=SUBJECT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)),
        _vector(subject_id=SUBJECT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)),
    ):
        assert "audios.subject_id =" in sql
        assert "audios.class_date >=" in sql
        assert "audios.class_date <=" in sql


def test_filters_are_absent_when_not_requested() -> None:
    assert "audios.subject_id =" not in _lexical()
    assert "audios.class_date" not in _vector()


# --- 7. LIMIT inside each branch, after the filters ----------------------------------


def test_limit_is_inside_each_branch_subquery() -> None:
    for sql in (_lexical(limit=7), _vector(limit=7)):
        assert sql.count("LIMIT") == 1
        # The tenant filters come before the LIMIT in the same statement: there is no outer
        # SELECT that could cut a global top-k before filtering.
        assert sql.index("audios.deleted_at IS NULL") < sql.index("LIMIT")


# --- 8. No filter after fusion (statement sequence) ----------------------------------


async def test_hybrid_search_runs_only_filtered_statements() -> None:
    """Two filtered SELECTs + content lookup + GUC: no query after the fusion."""
    db = StubSession(
        lexical=[_Row(_chunk_id(1), uuid.uuid4(), 0.9)],
        vector=[_Row(_chunk_id(2), uuid.uuid4(), 0.1)],
        content=[_ContentRow(_chunk_id(1), "uno"), _ContentRow(_chunk_id(2), "dos")],
    )

    hits = await search.hybrid_search(
        _db(db),
        user_id=USER_ID,
        query_text="integral",
        query_vector=[0.1, 0.2, 0.3],
        version=VERSION,
    )

    assert len(db.executed) == 4
    assert "websearch_to_tsquery" in db.executed[0]
    assert db.executed[1].startswith("SET LOCAL")
    assert "<=>" in db.executed[2]
    # The content lookup is still tenant-scoped and happens before the (pure) fusion.
    assert "chunks.content" in db.executed[3]
    assert "chunks.user_id =" in db.executed[3]
    assert [hit.chunk_id for hit in hits] == [_chunk_id(1), _chunk_id(2)]


async def test_hybrid_search_dedupes_overlapping_content() -> None:
    """Overlapping content read in production collapses: only the best-ranked survives."""
    first, duplicate = _chunk_id(1), _chunk_id(2)
    shared_audio = uuid.uuid4()
    db = StubSession(
        lexical=[_Row(first, shared_audio, 0.9), _Row(duplicate, shared_audio, 0.5)],
        content=[
            _ContentRow(first, "La integral   definida"),
            _ContentRow(duplicate, "la integral definida"),
        ],
    )

    hits = await search.hybrid_search(
        _db(db),
        user_id=USER_ID,
        query_text="integral",
        query_vector=[0.1, 0.2, 0.3],
        version=VERSION,
    )

    assert [hit.chunk_id for hit in hits] == [first]


# --- 9. GUC before the vector SELECT --------------------------------------------------


async def test_vector_branch_sets_iterative_scan_before_select() -> None:
    db = StubSession()

    await search.hybrid_search(
        _db(db),
        user_id=USER_ID,
        query_text="integral",
        query_vector=[0.1, 0.2, 0.3],
        version=VERSION,
    )

    scan_index = next(i for i, sql in enumerate(db.executed) if sql.startswith("SET LOCAL"))
    vector_index = next(i for i, sql in enumerate(db.executed) if "<=>" in sql)
    assert scan_index < vector_index
    # The GUC is issued inside a SAVEPOINT so a driver error cannot poison the transaction.
    assert db.savepoints == 1


async def test_iterative_scan_unsupported_is_a_typed_error() -> None:
    """pgvector < 0.8 → `SearchHnswUnsupported`, not a raw driver error."""
    db = UnsupportedScanSession()

    with pytest.raises(search.SearchHnswUnsupported) as exc:
        await search.hybrid_search(
            _db(db),
            user_id=USER_ID,
            query_text="integral",
            query_vector=[0.1, 0.2, 0.3],
            version=VERSION,
        )

    assert exc.value.code == "search_hnsw_unsupported"
    assert exc.value.details["reason"] == "iterative_scan_unavailable"
    # The savepoint was used, so the failure did not run the vector branch or roll back the
    # caller's session (no `rollback()` call is issued by the service).
    assert db.savepoints == 1
    assert not any("<=>" in sql for sql in db.executed)


# --- 10. Per-document FTS configuration ----------------------------------------------


def test_query_uses_document_fts_config() -> None:
    sql = _lexical()
    assert "transcripts.fts_config" in sql
    assert "websearch_to_tsquery" in sql
    # The language config is a column of the joined transcript, never a literal.
    assert "regconfig" in sql.lower()


# --- 11. Query/vector guards and active-generation resolution ------------------------


async def test_hybrid_search_rejects_empty_query() -> None:
    db = StubSession()

    with pytest.raises(search.SearchInvalidQuery):
        await search.hybrid_search(
            _db(db),
            user_id=USER_ID,
            query_text="   ",
            query_vector=[0.1, 0.2, 0.3],
            version=VERSION,
        )
    assert db.executed == []


async def test_hybrid_search_requires_query_vector() -> None:
    db = StubSession()

    with pytest.raises(search.SearchVectorRequired):
        await search.hybrid_search(
            _db(db),
            user_id=USER_ID,
            query_text="integral",
            query_vector=None,
            version=VERSION,
        )
    assert db.executed == []


async def test_hybrid_search_resolves_active_generation() -> None:
    generation = SimpleGeneration(VERSION)
    db = StubSession(generation=generation)

    await search.hybrid_search(
        _db(db),
        user_id=USER_ID,
        query_text="integral",
        query_vector=[0.1, 0.2, 0.3],
    )

    assert any("FROM index_generations" in sql for sql in db.executed)


async def test_hybrid_search_without_active_generation_fails_closed() -> None:
    db = StubSession(generation=None)

    with pytest.raises(search.SearchNotConfigured):
        await search.hybrid_search(
            _db(db),
            user_id=USER_ID,
            query_text="integral",
            query_vector=[0.1, 0.2, 0.3],
        )


class SimpleGeneration:
    """Minimal stand-in for `IndexGeneration` (only `.version` is read)."""

    def __init__(self, version: str) -> None:
        self.version = version
