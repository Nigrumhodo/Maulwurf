"""A2.17: the RLS migrations cover every tenant model (no database required).

I-S2-AN-16 reads the real catalog; this guard catches the cheaper mistake of adding a
`TenantOwned` model without extending `0005` (or a later migration) while the integration
suite cannot run. `0006` (J3.3) adds `settings` and `0007` (J3.7) adds `calendar_events`,
each with their own policies; `0011` (A3.1) adds the three M4 task tables and `0014`
(A4.4) adds the residual deletion and remote-operation tables.
"""
import importlib.util
from pathlib import Path
from types import ModuleType

from app.models import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION_0005 = VERSIONS / "0005_s2_row_level_security.py"
MIGRATION_0006 = VERSIONS / "0006_s3_calendar_settings.py"
MIGRATION_0007 = VERSIONS / "0007_s3_calendar_mirror.py"
MIGRATION_0011 = VERSIONS / "0011_s3_tasks.py"
MIGRATION_0014 = VERSIONS / "0014_s4_account_deletions.py"


def _migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_tenant_model_has_a_policy_in_the_migrations() -> None:
    base = _migration(MIGRATION_0005, "rls_0005")
    settings = _migration(MIGRATION_0006, "rls_0006")
    calendar = _migration(MIGRATION_0007, "rls_0007")
    tasks = _migration(MIGRATION_0011, "rls_0011")
    deletion = _migration(MIGRATION_0014, "rls_0014")
    # `TenantOwned` is a mixin, but a few tenant tables (chunk_segments, embeddings,
    # google_credentials, settings) declare `user_id` directly; the `user_id` column is the
    # contract every RLS migration follows. A4.4's `account_deletions` keys on
    # `former_user_id` because it must survive the account purge.
    models = {
        name
        for name, table in Base.metadata.tables.items()
        if "user_id" in table.c or "former_user_id" in table.c
    }
    covered = (
        set(base.TENANT_TABLES)
        | set(settings.TENANT_TABLES)
        | set(calendar.TABLES)
        | set(tasks.TABLES)
        | set(deletion.TABLES)
    )
    assert models == covered
    assert set(base.RLS_TABLES) == set(base.TENANT_TABLES) | {"users"}
    assert set(settings.TENANT_TABLES) == {"settings"}
    assert set(calendar.TABLES) == {"calendar_events"}
    assert set(tasks.TABLES) == {"tasks", "task_sources", "task_revisions"}
    assert set(deletion.TABLES) == {"account_deletions", "google_remote_operations"}


def test_role_scoped_exceptions_stay_narrow() -> None:
    migration = _migration(MIGRATION_0005, "rls_0005")
    assert set(migration.INGEST_TABLES) <= set(migration.TENANT_TABLES) | {"users"}
    assert set(migration.APP_ONLY_TABLES) <= set(migration.RLS_TABLES)
    # The dispatcher/reconciler revalidate the owner's status, so `users` needs the
    # jobs policy; credentials never do.
    assert "users" in migration.JOBS_TABLES
    assert "google_credentials" not in migration.JOBS_TABLES
    assert "sessions" not in migration.JOBS_TABLES
