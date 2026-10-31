"""E-S2-JF-06 regression: the smoke seeds its first user under RLS (A2.17).

The driver runs inside the `api` container and builds its user with the application
engine, so the transaction is downgraded to `maulwurf_app`. `users` fails closed when
neither `app.user_id` nor a verified Google `sub` is bound; the integration fixtures hide
that because they seed through the owner/superuser connection. This test calls the
driver's real `_seed()` against the migrated database, exactly as the smoke does, and
reproduces the develop failure: `new row violates row-level security policy for table
"users"` in step E-S2-JF-06.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db

pytestmark = pytest.mark.integration

SMOKE_PATH = Path(__file__).resolve().parents[4] / "scripts" / "e2e" / "smoke_pipeline.py"


def _load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location("e2e_smoke_pipeline_rls", SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_tenant_context() -> Iterator[None]:
    db.clear_user()
    yield
    db.clear_user()


async def test_smoke_seed_creates_the_first_user_under_the_app_role(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke = _load_smoke()
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    event.listen(engine.sync_engine, "begin", db._apply_tenant_settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # `_seed` imports the factory at call time; the real one points at the compose database.
    monkeypatch.setattr(db, "SessionFactory", factory)
    try:
        cookie_token, csrf_token, conversation_id = await smoke._seed()
        assert cookie_token
        assert csrf_token
        assert conversation_id

        # `_seed` used the `google_sub` bootstrap policy for the insert and then bound the
        # new tenant; the account, session and conversation are visible under that user.
        async with factory() as session:
            counts = (
                await session.execute(
                    text(
                        "SELECT (SELECT count(*) FROM users), "
                        "(SELECT count(*) FROM sessions), "
                        "(SELECT count(*) FROM conversations)"
                    )
                )
            ).one()
        assert tuple(counts) == (1, 1, 1)
    finally:
        await engine.dispose()
