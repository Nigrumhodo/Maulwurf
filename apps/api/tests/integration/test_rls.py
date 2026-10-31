"""I-S2-AN-16 (A2.17): RLS enabled and forced, with a per-transaction tenant.

The suite runs against the migrated session database. The migration creates three
NOLOGIN roles without ``BYPASSRLS``; the tests downgrade a connection to
``maulwurf_app`` with ``SET LOCAL ROLE`` and bind a tenant with
``set_config('app.user_id', ..., true)``, exactly like ``app/core/db.py`` does.

Covered:
- every tenant table has ``ENABLE`` and ``FORCE ROW LEVEL SECURITY`` and a policy;
- the role catalog has no superuser, ``BYPASSRLS`` or login runtime role;
- a query with no application filter only returns the bound tenant's row (the second
  barrier, with the ``user_id`` WHERE clause deliberately removed);
- a query without ``app.user_id`` fails closed;
- ``SET LOCAL`` does not survive the transaction and a pooled connection does not leak it;
- ``FORCE`` also restricts the table owner;
- the bootstrap policies let login/session resolution run before the tenant is known.
"""
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.models.base import TenantOwned
from app.services import sessions

pytestmark = pytest.mark.integration

APP_ROLE = "maulwurf_app"
JOBS_ROLE = "maulwurf_jobs"
INGEST_ROLE = "maulwurf_ingest"
RUNTIME_ROLES = (APP_ROLE, JOBS_ROLE, INGEST_ROLE)

TENANT_MODELS: list[type[TenantOwned]] = sorted(
    (mapper.class_ for mapper in Base.registry.mappers if issubclass(mapper.class_, TenantOwned)),
    key=lambda model: model.__name__,
)
TENANT_TABLES = {str(cast(Any, model).__tablename__) for model in TENANT_MODELS}
RLS_TABLES = TENANT_TABLES | {"users"}
JOBS_TABLES = (TENANT_TABLES | {"users"}) - {"sessions", "google_credentials"}


@asynccontextmanager
async def _role_connection(
    url: str,
    *,
    role: str | None = None,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[Any]:
    """A short connection running as ``role`` with the tenant bound, rolled back."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                if role is not None:
                    await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                if user_id is not None:
                    await connection.execute(
                        text("SELECT set_config('app.user_id', :user_id, true)"),
                        {"user_id": str(user_id)},
                    )
                yield connection
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


async def _seed_two_tenants(url: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Two users with a same-named subject each; returns ids and the shared name."""
    alice, bob, name = uuid.uuid4(), uuid.uuid4(), f"rls-{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            for user_id, sub in ((alice, f"sub-{alice}"), (bob, f"sub-{bob}")):
                await connection.execute(
                    text(
                        "INSERT INTO users (id, google_sub, email) "
                        "VALUES (:id, :sub, :email)"
                    ),
                    {"id": user_id, "sub": sub, "email": f"{sub}@example.test"},
                )
                await connection.execute(
                    text("INSERT INTO subjects (user_id, name) VALUES (:id, :name)"),
                    {"id": user_id, "name": name},
                )
    finally:
        await engine.dispose()
    return alice, bob, name


async def test_every_tenant_table_has_forced_rls_and_a_policy(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            relations = (
                await connection.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "pg_get_userbyid(c.relowner) AS owner "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                )
            ).all()
            policies = (
                await connection.execute(
                    text("SELECT tablename, policyname, roles FROM pg_policies "
                         "WHERE schemaname = 'public'")
                )
            ).all()
            roles = (
                await connection.execute(
                    text("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles")
                )
            ).all()
    finally:
        await engine.dispose()

    by_table = {str(row.relname): row for row in relations if str(row.relname) in RLS_TABLES}
    assert set(by_table) == RLS_TABLES
    for table, row in by_table.items():
        assert row.relrowsecurity, f"{table} does not ENABLE RLS"
        assert row.relforcerowsecurity, f"{table} does not FORCE RLS"
        assert str(row.owner) not in RUNTIME_ROLES, f"{table} is owned by a runtime role"

    by_table_policies: dict[str, set[str]] = {}
    tenant_roles: dict[str, set[str]] = {}
    for row in policies:
        table = str(row.tablename)
        policy = str(row.policyname)
        by_table_policies.setdefault(table, set()).add(policy)
        if policy == "tenant_isolation":
            tenant_roles[table] = {str(role) for role in row.roles}
    for table in RLS_TABLES:
        assert "tenant_isolation" in by_table_policies.get(table, set()), table
    # The strict tenant expression is evaluated only for the bound roles; the jobs role
    # must not see it, or its cross-tenant OR would still raise on a missing app.user_id.
    for table in ("sessions", "ingestion_attempts", "google_credentials"):
        assert tenant_roles[table] == {APP_ROLE}, table
    for table in ("subjects", "users"):
        assert tenant_roles[table] == {APP_ROLE, INGEST_ROLE}, table
    assert "auth_session_lookup" in by_table_policies["sessions"]
    assert {"auth_user_lookup", "auth_user_insert"} <= by_table_policies["users"]
    assert "ingest_attempt_queue" in by_table_policies["ingestion_attempts"]
    for table in JOBS_TABLES:
        assert "jobs_cross_tenant" in by_table_policies.get(table, set()), table
        assert "jobs_tenant" in by_table_policies.get(table, set()), table

    by_role = {str(row.rolname): row for row in roles if str(row.rolname) in RUNTIME_ROLES}
    assert set(by_role) == set(RUNTIME_ROLES)
    for role, row in by_role.items():
        assert not row.rolsuper, f"{role} is a superuser"
        assert not row.rolbypassrls, f"{role} has BYPASSRLS"
        assert not row.rolcanlogin, f"{role} can log in"


async def test_app_role_filters_without_the_application_filter(
    migrated_database_url: str,
) -> None:
    alice, bob, name = await _seed_two_tenants(migrated_database_url)
    async with _role_connection(
        migrated_database_url, role=APP_ROLE, user_id=alice
    ) as connection:
        # No `user_id` in the WHERE clause: the policy is the only barrier.
        rows = (
            await connection.execute(
                text("SELECT user_id FROM subjects WHERE name = :name"), {"name": name}
            )
        ).scalars()
        assert list(rows) == [alice]

        # A filter that guesses bob's id returns nothing, exactly like a missing row.
        guessed = (
            await connection.execute(
                text("SELECT count(*) FROM subjects WHERE name = :name AND user_id = :bob"),
                {"name": name, "bob": bob},
            )
        ).scalar_one()
        assert guessed == 0

        users = (await connection.execute(text("SELECT id FROM users"))).scalars()
        assert list(users) == [alice]


async def test_query_without_app_user_id_fails_closed(migrated_database_url: str) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        async with _role_connection(migrated_database_url, role=APP_ROLE) as connection:
            await connection.execute(text("SELECT count(*) FROM subjects"))
    assert "app.user_id" in str(excinfo.value.orig)

    # `sessions` and `users` are the pre-auth tables: the safe setting yields no rows
    # instead of an error, which is still fail-closed.
    async with _role_connection(migrated_database_url, role=APP_ROLE) as connection:
        for table in ("sessions", "users"):
            count = (
                await connection.execute(
                    text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
                )
            ).scalar_one()
            assert count == 0, f"{table} exposed rows without app.user_id"


async def test_pooled_connection_does_not_leak_the_tenant(migrated_database_url: str) -> None:
    alice, _bob, _name = await _seed_two_tenants(migrated_database_url)
    engine = create_async_engine(
        migrated_database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text(f'SET LOCAL ROLE "{APP_ROLE}"'))
                await connection.execute(
                    text("SELECT set_config('app.user_id', :user_id, true)"),
                    {"user_id": str(alice)},
                )
        # The pool has exactly one connection, so this checkout reuses that one.
        async with engine.connect() as connection:
            async with connection.begin():
                leaked = await connection.scalar(
                    text("SELECT current_setting('app.user_id', true)")
                )
                current_role = await connection.scalar(text("SELECT current_user"))
                assert leaked is None
                assert str(current_role) != APP_ROLE
    finally:
        await engine.dispose()


async def test_force_rls_also_restricts_the_table_owner(
    migrated_database_url: str,
) -> None:
    alice = uuid.uuid4()
    schema = f"rls_probe_{uuid.uuid4().hex[:10]}"
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{APP_ROLE}"')
            )
            await connection.execute(
                text(f'CREATE TABLE "{schema}".probe (id integer, owner_id uuid)')
            )
            await connection.execute(
                text(f'ALTER TABLE "{schema}".probe OWNER TO "{APP_ROLE}"')  # noqa: S608
            )
            await connection.execute(
                text(
                    f'INSERT INTO "{schema}".probe VALUES (1, :alice), (2, :bob)'  # noqa: S608
                ),
                {"alice": alice, "bob": uuid.uuid4()},
            )
            await connection.execute(
                text(f'ALTER TABLE "{schema}".probe ENABLE ROW LEVEL SECURITY')
            )
            await connection.execute(
                text(f'ALTER TABLE "{schema}".probe FORCE ROW LEVEL SECURITY')
            )
            await connection.execute(
                text(
                    f'CREATE POLICY tenant_isolation ON "{schema}".probe FOR ALL '
                    "USING (owner_id = current_setting('app.user_id', true)::uuid) "
                    "WITH CHECK (owner_id = current_setting('app.user_id', true)::uuid)"
                )
            )

        async with _role_connection(
            migrated_database_url, role=APP_ROLE, user_id=alice
        ) as connection:
            visible = await connection.scalar(
                text(f'SELECT count(*) FROM "{schema}".probe')  # noqa: S608
            )
            assert visible == 1, "FORCE must restrict the owner too"

        # Control: without FORCE the owner (the app role) sees every row.
        async with engine.begin() as connection:
            await connection.execute(
                text(f'ALTER TABLE "{schema}".probe NO FORCE ROW LEVEL SECURITY')
            )
        async with _role_connection(
            migrated_database_url, role=APP_ROLE, user_id=alice
        ) as connection:
            visible = await connection.scalar(
                text(f'SELECT count(*) FROM "{schema}".probe')  # noqa: S608
            )
            assert visible == 2
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_app_role_resolves_a_session_before_the_tenant_is_known(
    migrated_database_url: str,
) -> None:
    user_id = uuid.uuid4()
    token = f"token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, google_sub, email) "
                    "VALUES (:id, :sub, :email)"
                ),
                {"id": user_id, "sub": f"sub-{user_id}", "email": "session@example.test"},
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, csrf_hash, expires_at) "
                    "VALUES (:user_id, :token_hash, :csrf_hash, now() + interval '1 hour')"
                ),
                {"user_id": user_id, "token_hash": token_hash, "csrf_hash": token_hash},
            )

        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'SET LOCAL ROLE "{APP_ROLE}"'))
                db = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
                try:
                    resolved = await sessions.resolve_session(db, token)
                    assert resolved is not None
                    assert resolved.user_id == user_id
                    bound = await connection.scalar(
                        text("SELECT current_setting('app.user_id')")
                    )
                    assert str(bound) == str(user_id)
                finally:
                    await db.close()
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


async def test_app_role_bootstraps_a_login_and_then_binds_the_new_user(
    migrated_database_url: str,
) -> None:
    sub = f"sub-{uuid.uuid4().hex}"
    async with _role_connection(migrated_database_url, role=APP_ROLE) as connection:
        await connection.execute(
            text("SELECT set_config('app.google_sub', :sub, true)"), {"sub": sub}
        )
        new_id = await connection.scalar(
            text(
                "INSERT INTO users (google_sub, email) VALUES (:sub, :email) RETURNING id"
            ),
            {"sub": sub, "email": "login@example.test"},
        )
        assert isinstance(new_id, uuid.UUID)
        await connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(new_id)},
        )
        visible = (
            await connection.execute(
                text("SELECT id FROM users WHERE google_sub = :sub"), {"sub": sub}
            )
        ).scalars()
        assert list(visible) == [new_id]
