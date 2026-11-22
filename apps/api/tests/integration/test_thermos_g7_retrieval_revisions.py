"""G7 review fixes against PostgreSQL: deleted-subject candidates and user-edit revisions."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskRevision
from app.services.retrieval import lexical_candidates_statement, vector_candidates_statement
from app.services.revisions import record_pending_revision
from tests.integration import actors
from tests.integration.test_search import (
    QUERY_VECTOR,
    _indexed_class,
    _make_generation,
)

pytestmark = pytest.mark.integration


async def test_deleted_subject_chunks_do_not_consume_candidate_slots(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    generation = await _make_generation(db_session, actor)
    gone = await actors.make_subject(db_session, actor)
    live = await actors.make_subject(db_session, actor)
    _, _, hidden, _ = await _indexed_class(
        db_session,
        actor,
        generation=generation,
        content="alpha alpha alpha alpha",
        subject=gone,
    )
    _, _, kept, _ = await _indexed_class(
        db_session, actor, generation=generation, content="alpha", subject=live
    )
    gone.deleted_at = datetime.now(UTC)
    await db_session.flush()

    lexical = (
        await db_session.execute(
            lexical_candidates_statement(
                user_id=actor.user_id, version=generation.version, query_text="alpha", limit=1
            )
        )
    ).all()
    vector = (
        await db_session.execute(
            vector_candidates_statement(
                user_id=actor.user_id,
                version=generation.version,
                query_vector=QUERY_VECTOR,
                limit=2,
            )
        )
    ).all()

    assert [row.chunk_id for row in lexical] == [kept.id]
    assert hidden.id not in [row.chunk_id for row in vector]


async def test_reanalysis_does_not_discard_a_pending_user_edit(
    db_session: AsyncSession,
) -> None:
    actor = await actors.make_actor(db_session)
    edit = await actors.make_task_revision(db_session, actor)

    result = await record_pending_revision(
        db_session,
        user_id=actor.user_id,
        task_id=edit.task_id,
        proposed_fields={"titulo": "Machine proposal"},
        revision_type="reanalysis",
    )

    assert result is not None and result.id == edit.id
    rows = (
        await db_session.scalars(select(TaskRevision).where(TaskRevision.task_id == edit.task_id))
    ).all()
    assert [(r.revision_type, r.status) for r in rows] == [
        ("edit_of_confirmed", "pending_confirmation")
    ]
