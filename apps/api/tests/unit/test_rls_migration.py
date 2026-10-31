"""A2.17: the RLS migration covers every tenant model (no database required).

I-S2-AN-16 reads the real catalog; this guard catches the cheaper mistake of adding a
`TenantOwned` model without extending `0005` (or a later migration) while the integration
suite cannot run.
"""
import importlib.util
from pathlib import Path
from types import ModuleType

from app.models import Base

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0005_s2_row_level_security.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rls_0005", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_tenant_model_has_a_policy_in_the_migration() -> None:
    migration = _migration()
    # `TenantOwned` is a mixin, but a few tenant tables (chunk_segments, embeddings,
    # google_credentials) declare `user_id` directly; the `user_id` column is the
    # contract this migration follows.
    models = {name for name, table in Base.metadata.tables.items() if "user_id" in table.c}
    covered = set(migration.TENANT_TABLES)
    assert models == covered
    assert set(migration.RLS_TABLES) == covered | {"users"}


def test_role_scoped_exceptions_stay_narrow() -> None:
    migration = _migration()
    assert set(migration.INGEST_TABLES) <= set(migration.TENANT_TABLES) | {"users"}
    assert set(migration.APP_ONLY_TABLES) <= set(migration.RLS_TABLES)
    # The dispatcher/reconciler revalidate the owner's status, so `users` needs the
    # jobs policy; credentials never do.
    assert "users" in migration.JOBS_TABLES
    assert "google_credentials" not in migration.JOBS_TABLES
    assert "sessions" not in migration.JOBS_TABLES
