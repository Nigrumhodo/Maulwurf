"""A2.17: row-level security, forced, with per-transaction tenant binding (ADR-0006).

Every tenant table that exists today (S3/S4 tables add their own policies) gets
``ENABLE`` and ``FORCE ROW LEVEL SECURITY`` plus a ``FOR ALL`` policy with ``USING`` and
``WITH CHECK``. ``FORCE`` makes the table owner subject to the policy too; a superuser
still bypasses it, which is why the runtime never connects as one.

Three runtime roles are created as ``NOLOGIN`` groups without ``BYPASSRLS`` and granted
to the migration user, which runs the migrations and therefore keeps the owner
privileges needed for DDL and data migrations:

- ``maulwurf_app``: the API. Every transaction sets ``app.user_id`` before touching
  tenant rows (``app/core/db.py``). Login and session resolution run before that value
  exists, so ``users`` and ``sessions`` carry narrow bootstrap policies keyed by the
  verified Google ``sub`` or the presented opaque token hash.
- ``maulwurf_jobs``: worker and scheduler. They are cross-tenant by design (outbox
  dispatcher, reconciler, index job claims an audio before knowing its owner). Their
  policy is gated by ``app.cross_tenant='on'``, set only by those processes.
- ``maulwurf_ingest``: the ingest service. It owns the attempt queue and sweeps it
  across tenants, so ``ingestion_attempts`` has an explicit permissive policy for it;
  the transcript commit and the cleanup gate set ``app.user_id`` and remain bound.

The roles are cluster-wide and are intentionally not dropped on ``downgrade``: other
databases of the same cluster may reference them. Downgrading removes every policy,
disables RLS and revokes this database's grants.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
INGEST_ROLE = "maulwurf_ingest"
ROLES = (APP_ROLE, JOBS_ROLE, INGEST_ROLE)

# Tables whose tenant column is `user_id` (models/base.TenantOwned).
TENANT_TABLES = (
    "sessions",
    "google_credentials",
    "subjects",
    "audios",
    "ingestion_attempts",
    "variant_confirmation_tokens",
    "outbox_events",
    "transcripts",
    "segments",
    "chunks",
    "chunk_segments",
    "embeddings",
    "index_generations",
    "processing_runs",
    "conversations",
    "messages",
    "message_sources",
)
# `users` is tenant-owned by `id`; its policy is written separately.
RLS_TABLES = (*TENANT_TABLES, "users")
# The bootstrap policies are the only paths that may read without `app.user_id`.
BOOTSTRAP_POLICIES = {
    "sessions": ("auth_session_lookup",),
    "users": ("auth_user_lookup", "auth_user_insert"),
}
# Ingest only needs the tenant-bound tables of the transcript commit and its cleanup
# gate. The attempt queue is deliberately cross-tenant for this role, and credentials
# stay API-only.
INGEST_TABLES = (
    "users",
    "subjects",
    "audios",
    "ingestion_attempts",
    "transcripts",
    "segments",
    "chunks",
    "chunk_segments",
    "outbox_events",
)
# These tables only ever see the API's authenticated tenant. `ingestion_attempts` is
# excluded from the ingest role because its queue policy below is intentionally
# permissive; `sessions`/`google_credentials` are API-only.
APP_ONLY_TABLES = frozenset({"sessions", "ingestion_attempts", "google_credentials"})
# The worker/scheduler processes are cross-tenant by design; credentials and sessions
# stay out of that policy because no job needs them.
JOBS_TABLES = tuple(
    t for t in (*TENANT_TABLES, "users") if t not in ("sessions", "google_credentials")
)


def _quote(name: str) -> str:
    return f'"{name}"'


def _create_role(name: str) -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name}
    ).scalar_one_or_none()
    attributes = "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
    if exists is None:
        op.execute(f"CREATE ROLE {_quote(name)} {attributes}")
    else:
        # Idempotent on an existing cluster: never inherit a stray BYPASSRLS or LOGIN.
        op.execute(f"ALTER ROLE {_quote(name)} {attributes}")


def _grant_membership() -> None:
    # The DSN user keeps the owner privileges; SET LOCAL ROLE downgrades each runtime
    # transaction to the constrained role instead of authenticating as it.
    current = op.get_bind().execute(sa.text("SELECT current_user")).scalar_one()
    for role in ROLES:
        op.execute(f"GRANT {_quote(role)} TO {_quote(str(current))}")


def _tenant_policy(table: str, *, safe_user: bool) -> None:
    # `sessions`/`users` are queried before the request knows its user_id, so a missing
    # GUC must mean "no rows", not a database error there. Every other tenant table
    # fails closed: current_setting without missing_ok raises when app.user_id is unset.
    user_setting = (
        "current_setting('app.user_id', true)::uuid"
        if safe_user
        else "current_setting('app.user_id')::uuid"
    )
    column = "id" if table == "users" else "user_id"
    # Policy role lists keep the evaluator from combining a strict setting with the
    # cross-tenant jobs/metrics policies: a role with no applicable strict policy does
    # not evaluate `current_setting('app.user_id')` at all.
    roles = _quote(APP_ROLE) if table in APP_ONLY_TABLES else (
        f"{_quote(APP_ROLE)}, {_quote(INGEST_ROLE)}"
    )
    op.execute(f"ALTER TABLE {_quote(table)} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_quote(table)} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {_quote(table)}
          FOR ALL TO {roles}
          USING ({column} = {user_setting})
          WITH CHECK ({column} = {user_setting})
        """
    )


def _bootstrap_policies() -> None:
    op.execute(
        f"""
        CREATE POLICY auth_session_lookup ON sessions
          FOR SELECT TO {_quote(APP_ROLE)}
          USING (token_hash = current_setting('app.session_token_hash', true))
        """
    )
    op.execute(
        f"""
        CREATE POLICY auth_user_lookup ON users
          FOR SELECT TO {_quote(APP_ROLE)}
          USING (google_sub = current_setting('app.google_sub', true))
        """
    )
    op.execute(
        f"""
        CREATE POLICY auth_user_insert ON users
          FOR INSERT TO {_quote(APP_ROLE)}
          WITH CHECK (google_sub = current_setting('app.google_sub', true))
        """
    )


def _cross_tenant_policies() -> None:
    for table in JOBS_TABLES:
        column = "id" if table == "users" else "user_id"
        # The index job binds app.user_id after it claims the audio; the dispatcher and
        # reconciler keep the explicit switch on. The safe setting keeps the OR from
        # raising while the switch is the active branch.
        op.execute(
            f"""
            CREATE POLICY jobs_tenant ON {_quote(table)}
              FOR ALL TO {_quote(JOBS_ROLE)}
              USING ({column} = current_setting('app.user_id', true)::uuid)
              WITH CHECK ({column} = current_setting('app.user_id', true)::uuid)
            """
        )
        op.execute(
            f"""
            CREATE POLICY jobs_cross_tenant ON {_quote(table)}
              FOR ALL TO {_quote(JOBS_ROLE)}
              USING (current_setting('app.cross_tenant', true) = 'on')
              WITH CHECK (current_setting('app.cross_tenant', true) = 'on')
            """
        )
    op.execute(
        f"""
        CREATE POLICY ingest_attempt_queue ON ingestion_attempts
          FOR ALL TO {_quote(INGEST_ROLE)}
          USING (true)
          WITH CHECK (true)
        """
    )


def _grant_privileges() -> None:
    roles = ", ".join(_quote(role) for role in (APP_ROLE, JOBS_ROLE))
    ingest = _quote(INGEST_ROLE)
    tables = ", ".join(_quote(table) for table in INGEST_TABLES)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {roles}, {ingest}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {roles}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {ingest}")
    # Future S3/S4 tables created by the migration role get runtime grants; each of
    # those migrations must still add its own policies (A2.17 only covers today's tables).
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {roles}"
    )


def _drop_policies() -> None:
    for table in RLS_TABLES:
        names = ["tenant_isolation", *BOOTSTRAP_POLICIES.get(table, ())]
        if table in JOBS_TABLES:
            names.extend(("jobs_cross_tenant", "jobs_tenant"))
        if table == "ingestion_attempts":
            names.append("ingest_attempt_queue")
        for name in names:
            op.execute(f"DROP POLICY IF EXISTS {_quote(name)} ON {_quote(table)}")


def upgrade() -> None:
    for role in ROLES:
        _create_role(role)
    _grant_membership()
    for table in TENANT_TABLES:
        _tenant_policy(table, safe_user=table == "sessions")
    _tenant_policy("users", safe_user=True)
    _bootstrap_policies()
    _cross_tenant_policies()
    _grant_privileges()


def downgrade() -> None:
    _drop_policies()
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {_quote(table)} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_quote(table)} DISABLE ROW LEVEL SECURITY")
    roles = ", ".join(_quote(role) for role in (APP_ROLE, JOBS_ROLE))
    ingest = _quote(INGEST_ROLE)
    tables = ", ".join(_quote(table) for table in INGEST_TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {roles}")
    op.execute(f"REVOKE ALL PRIVILEGES ON {tables} FROM {ingest}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {roles}, {ingest}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {roles}"
    )
    # Roles stay: they are cluster-wide and other databases may hold grants on them.
