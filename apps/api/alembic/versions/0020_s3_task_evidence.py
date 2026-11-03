"""A3.2: the literal quote behind each task source span.

`task_sources` (A3.1/0012) already proves *where* a proposal came from: class, transcript
version, segment and Unicode span, with a self-consistent composite reference. It does not
store the words. This revision adds `task_evidence`, the literal quote the review inbox
shows and the citation must be able to rebuild from the database alone (ESPECIFICACION §M4,
I-S3-AN-05).

The row is anchored to the exact source, not merely to the same tenant:

- `(user_id, task_id, segment_id, char_start, char_end)` references the `task_sources`
  primary key, so a quote cannot exist without its provenance and deleting the source
  deletes the quote.
- `task_version` versions the evidence without versioning `task_sources`: the active task
  version's quote is the one read; a later version may repeat the same span.
- `char_length(quote) = char_end - char_start` and `btrim(quote) <> ''` are the cheap
  database checks; the service compares the quote with the persisted segment text on write
  and on read because only it can see the transcript row.

`task_evidence_by_task` serves version reads; `task_evidence_by_source` is the non-partial
covering index the composite FK needs (A2.18). RLS follows A2.17/0005 and 0011: `maulwurf_app`
is tenant-bound and `maulwurf_jobs` may work on one bound tenant or with the explicit
`app.cross_tenant='on'` switch. The ingest role has no access.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("task_evidence",)
APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CROSS_TENANT = "NULLIF(current_setting('app.cross_tenant', true), '') = 'on'"


def _create_task_evidence() -> None:
    op.create_table(
        "task_evidence",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "task_version >= 1", name=op.f("ck_task_evidence_task_version")
        ),
        sa.CheckConstraint(
            "char_start >= 0 AND char_start < char_end",
            name=op.f("ck_task_evidence_char_range"),
        ),
        sa.CheckConstraint(
            "char_length(quote) = char_end - char_start",
            name=op.f("ck_task_evidence_quote_length"),
        ),
        sa.CheckConstraint(
            "btrim(quote) <> ''", name=op.f("ck_task_evidence_quote_non_blank")
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "task_id", "segment_id", "char_start", "char_end"],
            [
                "task_sources.user_id",
                "task_sources.task_id",
                "task_sources.segment_id",
                "task_sources.char_start",
                "task_sources.char_end",
            ],
            ondelete="CASCADE",
            name=op.f(
                "fk_task_evidence_user_id_task_id_segment_id_char_start_char_end_task_sources"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_task_evidence_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_evidence")),
        sa.UniqueConstraint(
            "user_id",
            "task_id",
            "task_version",
            "segment_id",
            "char_start",
            "char_end",
            name=op.f(
                "uq_task_evidence_user_id_task_id_task_version_segment_id_char_start_char_end"
            ),
        ),
    )
    op.create_index(
        "task_evidence_by_task",
        "task_evidence",
        ["user_id", "task_id", "task_version"],
    )
    op.create_index(
        "task_evidence_by_source",
        "task_evidence",
        ["user_id", "task_id", "segment_id", "char_start", "char_end"],
    )


def _apply_tenant_policies(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON "{table}"
          FOR ALL TO "{APP_ROLE}"
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )
    # The analysis worker may be bound to one user or run with the explicit cross-tenant
    # switch the dispatcher/reconciler already use.
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = {SAFE_USER_SETTING})
          WITH CHECK (user_id = {SAFE_USER_SETTING})
        """
    )
    op.execute(
        f"""
        CREATE POLICY jobs_cross_tenant ON "{table}"
          FOR ALL TO "{JOBS_ROLE}"
          USING ({CROSS_TENANT})
          WITH CHECK ({CROSS_TENANT})
        """
    )
    # 0005's ALTER DEFAULT PRIVILEGES already covered tables created afterwards; the
    # explicit grant keeps this migration self-contained on a restored cluster.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" '
        f'TO "{APP_ROLE}", "{JOBS_ROLE}"'
    )


def upgrade() -> None:
    _create_task_evidence()
    for table in TABLES:
        _apply_tenant_policies(table)


def downgrade() -> None:
    op.drop_table("task_evidence")
