"""A2.17: the RLS migrations cover every tenant model (no database required).

I-S2-AN-16 reads the real catalog; this guard catches the cheaper mistake of adding a
`TenantOwned` model without extending `0005` (or a later migration) while the integration
suite cannot run. `0006` (J3.7) adds `settings` and `calendar_events` with their own policies.
"""
import importlib.util
from pathlib import Path
from types import ModuleType

from app.models import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION_0005 = VERSIONS / "0005_s2_row_level_security.py"
MIGRATION_0006 = VERSIONS / "0006_s3_calendar_mirror.py"


def _migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_tenant_model_has_a_policy_in_the_migrations() -> None:
    base = _migration(MIGRATION_0005, "rls_0005")
    calendar = _migration(MIGRATION_0006, "rls_0006")
    # `TenantOwned` is a mixin, but a few tenant tables (chunk_segments, embeddings,
    # google_credentials, settings) declare `user_id` directly; the `user_id` column is the
    # contract the migrations follow.
    models = {name for name, table in Base.metadata.tables.items() if "user_id" in table.c}
    covered = set(base.TENANT_TABLES) | set(calendar.TABLES)
    assert models == covered
    assert set(base.RLS_TABLES) == set(base.TENANT_TABLES) | {"users"}
    assert set(calendar.TABLES) == {"settings", "calendar_events"}


def test_role_scoped_exceptions_stay_narrow() -> None:
    migration = _migration(MIGRATION_0005, "rls_0005")
    assert set(migration.INGEST_TABLES) <= set(migration.TENANT_TABLES) | {"users"}
    assert set(migration.APP_ONLY_TABLES) <= set(migration.RLS_TABLES)
    # The dispatcher/reconciler revalidate the owner's status, so `users` needs the
    # jobs policy; credentials never do.
    assert "users" in migration.JOBS_TABLES
    assert "google_credentials" not in migration.JOBS_TABLES
    assert "sessions" not in migration.JOBS_TABLES
