"""L3.6 tools against PostgreSQL: proposals stay in the inbox and inside the tenant."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Segment, Task, TaskSource, Transcript
from app.services.tools import ToolCall, ToolOrchestrator
from tests.integration import actors

pytestmark = pytest.mark.integration

QUOTE = "bring the lab report on Friday"
NOW = datetime(2026, 11, 1, 16, tzinfo=UTC)


async def _class(db: AsyncSession, actor: actors.Actor) -> tuple[object, Segment]:
    audio = await actors.make_audio(db, actor)
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="none",
        text=QUOTE,
        char_count=len(QUOTE),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=QUOTE,
        char_start=0,
        char_end=len(QUOTE),
    )
    db.add(segment)
    audio.active_transcript_version = 1
    await db.flush()
    return audio, segment


async def test_propose_task_never_writes_the_calendar_and_stays_in_the_tenant(
    db_session: AsyncSession,
) -> None:
    owner = await actors.make_actor(db_session)
    other = await actors.make_actor(db_session)
    audio, segment = await _class(db_session, owner)
    tools = ToolOrchestrator(db_session)

    suggestion = await tools.execute(
        (
            ToolCall(
                "propose_task",
                {
                    "titulo": "Review notes",
                    "tipo": "recomendacion",
                    "timezone": "America/Bogota",
                    "mencion": {"kind": "absent"},
                },
            ),
        ),
        user_id=owner.user_id,
        now=NOW,
    )
    assert suggestion[0].ok is True
    assert suggestion[0].payload["evidence"] == "general_suggestion"
    assert suggestion[0].payload["source"] == "chat"
    assert suggestion[0].payload["calendar_sync_status"] == "not_requested"
    assert suggestion[0].payload["status"] == "needs_review"

    cited = await tools.execute(
        (
            ToolCall(
                "propose_task",
                {
                    "titulo": "Lab report",
                    "tipo": "entrega",
                    "timezone": "America/Bogota",
                    "mencion": {"kind": "all_day", "due_date": "2026-11-06"},
                    "sources": [
                        {
                            "audio_id": str(audio.id),
                            "segment_id": str(segment.id),
                            "inicio_char": 0,
                            "fin_char": len(QUOTE),
                            "cita_textual": QUOTE,
                        }
                    ],
                },
            ),
        ),
        user_id=owner.user_id,
        now=NOW,
    )
    assert cited[0].ok is True
    assert cited[0].payload["evidence"] == "corpus"
    assert cited[0].payload["source"] == "chat"
    sources = (
        await db_session.execute(select(func.count()).select_from(TaskSource))
    ).scalar_one()
    assert sources == 1

    rejected = await tools.execute(
        (
            ToolCall(
                "propose_task",
                {
                    "titulo": "Invented",
                    "tipo": "tarea",
                    "timezone": "America/Bogota",
                    "mencion": {"kind": "absent"},
                    "sources": [
                        {
                            "audio_id": str(audio.id),
                            "segment_id": str(segment.id),
                            "inicio_char": 0,
                            "fin_char": len(QUOTE),
                            "cita_textual": "x" * len(QUOTE),
                        }
                    ],
                },
            ),
        ),
        user_id=owner.user_id,
        now=NOW,
    )
    assert rejected[0].ok is False
    assert rejected[0].payload["code"] == "evidence_rejected"

    own = await tools.execute(
        (ToolCall("get_pending_tasks", {}),),
        user_id=owner.user_id,
        now=NOW,
    )
    foreign = await tools.execute(
        (ToolCall("get_pending_tasks", {}),),
        user_id=other.user_id,
        now=NOW,
    )
    assert {task["source"] for task in own[0].payload["tasks"]} == {"chat"}
    assert len(own[0].payload["tasks"]) == 2
    assert foreign[0].payload["tasks"] == []

    calendar = await tools.execute(
        (ToolCall("get_calendar_events", {"from": "2026-12-01", "to": "2026-11-01"}),),
        user_id=owner.user_id,
        now=NOW,
    )
    assert calendar[0].ok is False
    assert calendar[0].payload["code"] == "window_invalid"
    stored = (await db_session.execute(select(Task))).scalars().all()
    assert all(task.calendar_event_id is None for task in stored)
    assert all(task.calendar_sync_status == "not_requested" for task in stored)
