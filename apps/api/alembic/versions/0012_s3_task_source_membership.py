"""A3.1 review fix: a `task_source` cites one transcript's segment, audio and version.

`0011` gave `task_sources` independent tenant-aware FKs to `transcripts` and `segments`.
Because `segments` only exposed `(user_id, id)`, a row could cite a segment that belongs to
a *different* transcript of the same user, and neither `audio_id` nor `transcript_version`
was tied to the cited transcript. That is exactly the corruption the provenance table must
make impossible (ESPECIFICACION §M4, DISENO §2.8).

A new revision rather than an edit of `0002` or `0011`: `0002` is already applied in every
deployed chain and `0011` may already be applied in the databases used to test this branch;
rewriting either would leave an environment whose schema does not match its recorded
revision.
`0009` set the same precedent when it added parent unique keys and replaced composite FKs.

What this revision enforces:

- `segments (user_id, transcript_id, id)` is unique, so a source can be pinned to the
  transcript *and* the segment in one FK: `(user_id, transcript_id, segment_id)`.
- `transcripts (user_id, audio_id, id)` is unique, so `(user_id, audio_id, transcript_id)`
  ties the cited class to the cited transcript.
- `transcripts (user_id, id, version)` already comes from `0009`; the new
  `(user_id, transcript_id, transcript_version)` FK ties the cited version to that same
  transcript. Together the three FKs make every citation self-consistent: the segment
  belongs to the transcript, the transcript belongs to the audio and the version is the
  transcript's version. A wrong audio cannot be hidden behind a valid transcript pair.
- `tasks (user_id, status)` gives the unresolved tray (`ambiguous`/`missing`, no due
  column) a usable index; the two partial due indexes from `0011` cannot serve those rows.
- The three 2-column `task_sources` indexes grow by the column each new FK needs, so every
  composite FK still has a non-partial covering index (A2.18's guard) while the previous
  audio/transcript/segment lookups keep their prefix.

`task_revisions` is intentionally untouched. `(task_id, base_task_version, revision_type,
run_id)` keeps PostgreSQL's NULL-distinct semantics: `run_id` is NULL for
`edit_of_confirmed`, and several pending edits of the same base version are legitimate
proposals until one is confirmed. The decision is documented in ESPECIFICACION §M4; a
future "one edit per base version" contract would need `NULLS NOT DISTINCT` (PG 15+) and a
new revision of that contract, not a silent constraint change here.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_segments_user_id_transcript_id_id"),
        "segments",
        ["user_id", "transcript_id", "id"],
    )
    op.create_unique_constraint(
        op.f("uq_transcripts_user_id_audio_id_id"),
        "transcripts",
        ["user_id", "audio_id", "id"],
    )
    op.drop_constraint(
        "fk_task_sources_user_id_transcript_id_transcripts",
        "task_sources",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_task_sources_user_id_segment_id_segments",
        "task_sources",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_task_sources_user_id_audio_id_transcript_id_transcripts"),
        "task_sources",
        "transcripts",
        ["user_id", "audio_id", "transcript_id"],
        ["user_id", "audio_id", "id"],
    )
    op.create_foreign_key(
        op.f("fk_task_sources_user_id_transcript_id_transcript_version_transcripts"),
        "task_sources",
        "transcripts",
        ["user_id", "transcript_id", "transcript_version"],
        ["user_id", "id", "version"],
    )
    op.create_foreign_key(
        op.f("fk_task_sources_user_id_transcript_id_segment_id_segments"),
        "task_sources",
        "segments",
        ["user_id", "transcript_id", "segment_id"],
        ["user_id", "transcript_id", "id"],
    )
    # One supporting index per new FK (A2.18 guard): the 2-column indexes from `0011` are
    # prefixes, not covers, of these keys. The same indexes still serve the audio,
    # transcript and segment lookups they served before.
    op.drop_index("task_sources_by_audio", table_name="task_sources")
    op.create_index(
        "task_sources_by_audio",
        "task_sources",
        ["user_id", "audio_id", "transcript_id"],
    )
    op.drop_index("task_sources_by_transcript", table_name="task_sources")
    op.create_index(
        "task_sources_by_transcript",
        "task_sources",
        ["user_id", "transcript_id", "transcript_version"],
    )
    op.drop_index("task_sources_by_segment", table_name="task_sources")
    op.create_index(
        "task_sources_by_segment",
        "task_sources",
        ["user_id", "transcript_id", "segment_id"],
    )
    op.create_index("tasks_inbox_status", "tasks", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("tasks_inbox_status", table_name="tasks")
    op.drop_index("task_sources_by_segment", table_name="task_sources")
    op.create_index("task_sources_by_segment", "task_sources", ["user_id", "segment_id"])
    op.drop_index("task_sources_by_transcript", table_name="task_sources")
    op.create_index("task_sources_by_transcript", "task_sources", ["user_id", "transcript_id"])
    op.drop_index("task_sources_by_audio", table_name="task_sources")
    op.create_index("task_sources_by_audio", "task_sources", ["user_id", "audio_id"])
    op.drop_constraint(
        op.f("fk_task_sources_user_id_transcript_id_segment_id_segments"),
        "task_sources",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_task_sources_user_id_transcript_id_transcript_version_transcripts"),
        "task_sources",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_task_sources_user_id_audio_id_transcript_id_transcripts"),
        "task_sources",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_task_sources_user_id_transcript_id_transcripts",
        "task_sources",
        "transcripts",
        ["user_id", "transcript_id"],
        ["user_id", "id"],
    )
    op.create_foreign_key(
        "fk_task_sources_user_id_segment_id_segments",
        "task_sources",
        "segments",
        ["user_id", "segment_id"],
        ["user_id", "id"],
    )
    op.drop_constraint(
        op.f("uq_transcripts_user_id_audio_id_id"), "transcripts", type_="unique"
    )
    op.drop_constraint(
        op.f("uq_segments_user_id_transcript_id_id"), "segments", type_="unique"
    )
