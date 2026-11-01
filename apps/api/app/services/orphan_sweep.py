"""Read-only sweep for class-derived rows that outlived their class (A4.7).

A class (``audios`` row) is the root of the durable text tree: transcripts, segments,
chunks, chunk links, embeddings, validated message sources, tasks and their provenance,
pending revisions and reminders. ``DELETE /audios/{id}`` purges those rows in one
transaction and account deletion removes every tenant row. This module is the independent
check that no derivative survived its class, whether the class is tombstoned
(``audios.deleted_at``) or physically gone.

The sweep only counts rows: it never deletes or repairs data. Operational history that the
contract deliberately keeps after a class tombstone (ingestion attempts, cancelled
processing runs, skipped outbox rows) is not a text derivative and is not counted here.

Run it as the cross-tenant jobs role so row-level security does not hide another tenant;
``tools.sweep_orphans`` enforces that role and binds ``app.cross_tenant``.
"""

# The catalog below is composed only from literal SELECTs and a fixed alias; no value or
# identifier comes from input. Ruff cannot see that through the interpolation.
# ruff: noqa: S608

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every query is a literal SELECT; the only bind parameters are the optional tenant scope
# and the fixed aliases. No identifier is ever built from input.
_SCOPE = (
    "(CAST(:user_ids AS uuid[]) IS NULL "
    "OR {alias}.user_id = ANY(CAST(:user_ids AS uuid[])))"
)


def _scope(alias: str) -> str:
    return _SCOPE.format(alias=alias)


@dataclass(frozen=True)
class OrphanCheck:
    name: str
    description: str
    sql: str


@dataclass(frozen=True)
class OrphanFinding:
    name: str
    description: str
    count: int


@dataclass(frozen=True)
class OrphanReport:
    findings: tuple[OrphanFinding, ...]

    @property
    def total(self) -> int:
        return sum(finding.count for finding in self.findings)

    @property
    def clean(self) -> bool:
        return self.total == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "clean": self.clean,
            "total": self.total,
            "findings": [
                {
                    "name": finding.name,
                    "description": finding.description,
                    "count": finding.count,
                }
                for finding in self.findings
            ],
        }


ORPHAN_CHECKS: tuple[OrphanCheck, ...] = (
    OrphanCheck(
        "tombstoned_class_with_active_transcript",
        "Tombstoned class still points at an active transcript version",
        f"""
        SELECT count(*) FROM audios a
        WHERE a.deleted_at IS NOT NULL
          AND a.active_transcript_version IS NOT NULL
          AND {_scope("a")}
        """,
    ),
    OrphanCheck(
        "transcripts_without_live_class",
        "Transcript whose class is missing or tombstoned",
        f"""
        SELECT count(*) FROM transcripts t
        LEFT JOIN audios a ON a.user_id = t.user_id AND a.id = t.audio_id
        WHERE (a.id IS NULL OR a.deleted_at IS NOT NULL)
          AND {_scope("t")}
        """,
    ),
    OrphanCheck(
        "segments_without_live_transcript",
        "Segment whose transcript or class is missing or tombstoned",
        f"""
        SELECT count(*) FROM segments s
        LEFT JOIN transcripts t ON t.user_id = s.user_id AND t.id = s.transcript_id
        LEFT JOIN audios a ON a.user_id = t.user_id AND a.id = t.audio_id
        WHERE (t.id IS NULL OR a.id IS NULL OR a.deleted_at IS NOT NULL)
          AND {_scope("s")}
        """,
    ),
    OrphanCheck(
        "chunks_without_live_class",
        "Chunk whose class or exact transcript version is missing or tombstoned",
        f"""
        SELECT count(*) FROM chunks c
        LEFT JOIN audios a ON a.user_id = c.user_id AND a.id = c.audio_id
        LEFT JOIN transcripts t
          ON t.user_id = c.user_id
         AND t.id = c.transcript_id
         AND t.version = c.transcript_version
        WHERE (a.id IS NULL OR a.deleted_at IS NOT NULL OR t.id IS NULL
               OR t.audio_id <> c.audio_id)
          AND {_scope("c")}
        """,
    ),
    OrphanCheck(
        "embeddings_without_live_chunk",
        "Embedding whose chunk, generation or class is missing or tombstoned",
        f"""
        SELECT count(*) FROM embeddings e
        LEFT JOIN chunks c
          ON c.user_id = e.user_id
         AND c.id = e.chunk_id
         AND c.index_version = e.index_version
        LEFT JOIN audios a ON a.user_id = c.user_id AND a.id = c.audio_id
        WHERE (c.id IS NULL OR a.id IS NULL OR a.deleted_at IS NOT NULL)
          AND {_scope("e")}
        """,
    ),
    OrphanCheck(
        "chunk_segments_without_live_chunk_or_segment",
        "Chunk link whose chunk or segment is missing, foreign or tombstoned",
        f"""
        SELECT count(*) FROM chunk_segments cs
        LEFT JOIN chunks c ON c.user_id = cs.user_id AND c.id = cs.chunk_id
        LEFT JOIN audios a ON a.user_id = c.user_id AND a.id = c.audio_id
        LEFT JOIN segments s ON s.user_id = cs.user_id AND s.id = cs.segment_id
        LEFT JOIN transcripts t ON t.user_id = s.user_id AND t.id = s.transcript_id
        WHERE (c.id IS NULL OR a.id IS NULL OR a.deleted_at IS NOT NULL
               OR s.id IS NULL OR t.id IS NULL
               OR s.transcript_id <> c.transcript_id)
          AND {_scope("cs")}
        """,
    ),
    OrphanCheck(
        "message_sources_without_live_source",
        "Validated transcript citation whose message or source is missing or tombstoned",
        f"""
        SELECT count(*) FROM message_sources ms
        LEFT JOIN messages m ON m.user_id = ms.user_id AND m.id = ms.message_id
        LEFT JOIN audios a ON a.user_id = ms.user_id AND a.id = ms.audio_id
        LEFT JOIN transcripts t
          ON t.user_id = ms.user_id
         AND t.id = ms.transcript_id
         AND t.version = ms.transcript_version
        LEFT JOIN chunks c ON c.user_id = ms.user_id AND c.id = ms.chunk_id
        LEFT JOIN segments s ON s.user_id = ms.user_id AND s.id = ms.segment_id
        WHERE ms.kind = 'transcript'
          AND (m.id IS NULL OR a.id IS NULL OR a.deleted_at IS NOT NULL
               OR t.id IS NULL OR c.id IS NULL OR s.id IS NULL
               OR t.audio_id <> ms.audio_id
               OR c.audio_id <> ms.audio_id
               OR s.transcript_id <> ms.transcript_id)
          AND {_scope("ms")}
        """,
    ),
    OrphanCheck(
        "tasks_without_live_class",
        "Class-derived task whose class is missing or tombstoned",
        f"""
        SELECT count(*) FROM tasks t
        LEFT JOIN audios a ON a.user_id = t.user_id AND a.id = t.audio_id
        WHERE t.audio_id IS NOT NULL
          AND (a.id IS NULL OR a.deleted_at IS NOT NULL)
          AND {_scope("t")}
        """,
    ),
    OrphanCheck(
        "task_sources_without_live_class",
        "Task provenance whose task, class, transcript or segment is missing or tombstoned",
        f"""
        SELECT count(*) FROM task_sources ts
        LEFT JOIN tasks t ON t.user_id = ts.user_id AND t.id = ts.task_id
        LEFT JOIN audios a ON a.user_id = ts.user_id AND a.id = ts.audio_id
        LEFT JOIN transcripts tr
          ON tr.user_id = ts.user_id
         AND tr.id = ts.transcript_id
         AND tr.version = ts.transcript_version
        LEFT JOIN segments s ON s.user_id = ts.user_id AND s.id = ts.segment_id
        WHERE (t.id IS NULL OR a.id IS NULL OR a.deleted_at IS NOT NULL
               OR tr.id IS NULL OR s.id IS NULL
               OR tr.audio_id <> ts.audio_id
               OR s.transcript_id <> ts.transcript_id)
          AND {_scope("ts")}
        """,
    ),
    OrphanCheck(
        "task_revisions_without_live_task",
        "Pending task revision whose task or class is missing or tombstoned",
        f"""
        SELECT count(*) FROM task_revisions r
        LEFT JOIN tasks t ON t.user_id = r.user_id AND t.id = r.task_id
        LEFT JOIN audios a ON a.user_id = t.user_id AND a.id = t.audio_id
        WHERE (t.id IS NULL
               OR (t.audio_id IS NOT NULL
                   AND (a.id IS NULL OR a.deleted_at IS NOT NULL)))
          AND {_scope("r")}
        """,
    ),
    OrphanCheck(
        "notifications_without_live_task",
        "Reminder whose task or class is missing or tombstoned",
        f"""
        SELECT count(*) FROM notifications n
        LEFT JOIN tasks t ON t.user_id = n.user_id AND t.id = n.task_id
        LEFT JOIN audios a ON a.user_id = t.user_id AND a.id = t.audio_id
        WHERE n.task_id IS NOT NULL
          AND (t.id IS NULL
               OR (t.audio_id IS NOT NULL
                   AND (a.id IS NULL OR a.deleted_at IS NOT NULL)))
          AND {_scope("n")}
        """,
    ),
)


async def sweep_orphans(
    db: AsyncSession, *, user_ids: Sequence[uuid.UUID] | None = None
) -> OrphanReport:
    """Count every orphan class-derived row, optionally scoped to some tenants."""
    if user_ids is not None and not user_ids:
        raise ValueError("user_ids must be omitted or contain at least one tenant")
    scope: list[uuid.UUID] | None = None if user_ids is None else list(user_ids)
    findings: list[OrphanFinding] = []
    for check in ORPHAN_CHECKS:
        value = await db.scalar(text(check.sql), {"user_ids": scope})
        findings.append(
            OrphanFinding(
                name=check.name,
                description=check.description,
                count=int(value or 0),
            )
        )
    return OrphanReport(findings=tuple(findings))
