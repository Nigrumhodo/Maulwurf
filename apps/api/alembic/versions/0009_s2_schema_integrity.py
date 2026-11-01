"""A2.18: schema integrity follow-up (FK indexes, version invariants, text/dimension checks).

This revision follows the current head (`0008`), not A2.20's `0004`: after A2.20 landed,
A2.17 (RLS), J3.3/J3.7/J3.8 (Calendar) added `0005`-`0008` to the same linear chain. A2.18's
migration must therefore stay after the real head so the graph keeps a single upgrade path.

What it enforces (gap list of `docs/plan/S2.md` §A2.18):

- A non-partial supporting index for every tenant/composite FK that had only a partial index
  or none (`outbox_events.user_id`, `audios(user_id, subject_id)`, the variant token
  subject/reservation keys, transcripts/segments/chunks by their parent, processing runs by
  audio, conversations, messages and message sources). PostgreSQL does not index the
  referencing side; class and account purges walk these keys.
- `chunks.index_version` references `index_generations(user_id, version)`, so a chunk cannot
  outlive its generation.
- `chunks.transcript_version` and `message_sources.transcript_version` reference the exact
  transcript version (`transcripts(user_id, id, version)`), instead of only the transcript id.
- `embeddings(user_id, chunk_id, index_version)` references
  `chunks(user_id, id, index_version)`, so a vector cannot sit under another generation of the
  same chunk (DISENO §2.5).
- `embeddings.dimension` and `index_generations.dimension` are fixed to the vector column's
  1536 dimensions instead of accepting any integer.
- A transcript's `text` must have non-blank content (ESPECIFICACION §M2: silence ends
  `rejected`, never as a stored transcript).

Adding the composite FKs requires their parent-side unique keys; the new constraints are
redundant with `(user_id, id)` for identity purposes but PostgreSQL needs the exact column
tuple for the reference. Existing rows that violate an invariant fail the migration instead
of being silently deleted: durable citation evidence (`message_sources`) must not be
rewritten by a schema migration.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_INDEXES = (
    ("outbox_events_by_user", "outbox_events", ["user_id"]),
    ("audios_by_subject", "audios", ["user_id", "subject_id"]),
    ("variant_tokens_by_subject", "variant_confirmation_tokens", ["user_id", "subject_id"]),
    (
        "variant_tokens_by_reserved_audio",
        "variant_confirmation_tokens",
        ["user_id", "reserved_audio_id"],
    ),
    ("transcripts_by_audio", "transcripts", ["user_id", "audio_id"]),
    ("segments_by_transcript", "segments", ["user_id", "transcript_id"]),
    (
        "chunks_by_transcript",
        "chunks",
        ["user_id", "transcript_id", "transcript_version"],
    ),
    ("chunks_by_generation", "chunks", ["user_id", "index_version"]),
    ("processing_runs_by_audio", "processing_runs", ["user_id", "audio_id"]),
    ("conversations_by_subject", "conversations", ["user_id", "subject_id"]),
    ("conversations_by_audio", "conversations", ["user_id", "audio_id"]),
    ("messages_by_conversation", "messages", ["user_id", "conversation_id"]),
    (
        "message_sources_by_transcript",
        "message_sources",
        ["user_id", "transcript_id", "transcript_version"],
    ),
    ("message_sources_by_chunk", "message_sources", ["user_id", "chunk_id"]),
    ("message_sources_by_segment", "message_sources", ["user_id", "segment_id"]),
)


def upgrade() -> None:
    for index_name, table, columns in FK_INDEXES:
        op.create_index(index_name, table, columns)

    # Parent keys for the 3-column FKs. They also serve as a covering index for the
    # existing (user_id, resource_id) FKs on transcripts/chunks.
    op.create_unique_constraint(
        "uq_transcripts_user_id_id_version",
        "transcripts",
        ["user_id", "id", "version"],
    )
    op.create_unique_constraint(
        "uq_chunks_user_id_id_index_version",
        "chunks",
        ["user_id", "id", "index_version"],
    )

    op.drop_constraint(
        "fk_chunks_user_id_transcript_id_transcripts", "chunks", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_chunks_user_id_transcript_id_transcript_version_transcripts",
        "chunks",
        "transcripts",
        ["user_id", "transcript_id", "transcript_version"],
        ["user_id", "id", "version"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chunks_user_id_index_version_index_generations",
        "chunks",
        "index_generations",
        ["user_id", "index_version"],
        ["user_id", "version"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_embeddings_user_id_chunk_id_chunks", "embeddings", type_="foreignkey")
    op.create_foreign_key(
        "fk_embeddings_user_id_chunk_id_index_version_chunks",
        "embeddings",
        "chunks",
        ["user_id", "chunk_id", "index_version"],
        ["user_id", "id", "index_version"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "fk_message_sources_user_id_transcript_id_transcripts",
        "message_sources",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_message_sources_transcript_version",
        "message_sources",
        "transcripts",
        ["user_id", "transcript_id", "transcript_version"],
        ["user_id", "id", "version"],
    )

    # `create_check_constraint` runs the `ck_%(table)s_%(constraint_name)s` convention, so the
    # short names below become the same names the models declare.
    op.create_check_constraint(
        "nonblank_text", "transcripts", sa.text("btrim(text) <> ''")
    )
    op.create_check_constraint(
        "dimension_fixed", "embeddings", sa.text("dimension = 1536")
    )
    op.create_check_constraint(
        "dimension_fixed",
        "index_generations",
        sa.text("dimension = 1536"),
    )


def downgrade() -> None:
    # Short names: the `ck` convention expands them to the constraint names created above.
    op.drop_constraint("dimension_fixed", "index_generations", type_="check")
    op.drop_constraint("dimension_fixed", "embeddings", type_="check")
    op.drop_constraint("nonblank_text", "transcripts", type_="check")

    op.drop_constraint(
        "fk_message_sources_transcript_version", "message_sources", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_message_sources_user_id_transcript_id_transcripts",
        "message_sources",
        "transcripts",
        ["user_id", "transcript_id"],
        ["user_id", "id"],
    )
    op.drop_constraint(
        "fk_embeddings_user_id_chunk_id_index_version_chunks", "embeddings", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_embeddings_user_id_chunk_id_chunks",
        "embeddings",
        "chunks",
        ["user_id", "chunk_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "fk_chunks_user_id_index_version_index_generations", "chunks", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_chunks_user_id_transcript_id_transcript_version_transcripts",
        "chunks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_chunks_user_id_transcript_id_transcripts",
        "chunks",
        "transcripts",
        ["user_id", "transcript_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("uq_chunks_user_id_id_index_version", "chunks", type_="unique")
    op.drop_constraint("uq_transcripts_user_id_id_version", "transcripts", type_="unique")
    for index_name, table, _columns in reversed(FK_INDEXES):
        op.drop_index(index_name, table_name=table)
