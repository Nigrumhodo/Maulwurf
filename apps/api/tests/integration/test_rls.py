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
- the runtime roles cannot rewrite ``alembic_version`` (write grants are revoked);
- the request transaction commits with ``app.user_id`` already bound;
- the bootstrap policies let login/session resolution run before the tenant is known.
"""
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import db
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME
from app.main import app
from app.models import Base, GoogleCredential
from app.models.base import TenantOwned
from app.services import oauth_google, sessions

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
# `settings` keys its tenant by `user_id` without the `TenantOwned.id` shape used by
# `get_owned`; it still gets forced RLS and is asserted here explicitly (J3.7).
RLS_TABLES = TENANT_TABLES | {"users", "settings"}
JOBS_TABLES = (TENANT_TABLES | {"users", "settings"}) - {"sessions", "google_credentials"}


@pytest.fixture(autouse=True)
def _clear_tenant_context() -> Iterator[None]:
    """No test may inherit a tenant or the cross-tenant switch from another one."""
    db.clear_user()
    db.clear_cross_tenant()
    yield
    db.clear_user()
    db.clear_cross_tenant()


@asynccontextmanager
async def _role_connection(
    url: str,
    *,
    role: str | None = None,
    user_id: uuid.UUID | None = None,
    cross_tenant: bool = False,
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
                if cross_tenant:
                    await connection.execute(
                        text("SELECT set_config('app.cross_tenant', 'on', true)")
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
    for table in ("sessions", "ingestion_attempts", "google_credentials", "settings"):
        assert tenant_roles[table] == {APP_ROLE}, table
    for table in ("subjects", "users"):
        assert tenant_roles[table] == {APP_ROLE, INGEST_ROLE}, table
    assert "auth_session_lookup" in by_table_policies["sessions"]
    assert {"auth_user_lookup", "auth_user_insert"} <= by_table_policies["users"]
    assert "ingest_attempt_queue" in by_table_policies["ingestion_attempts"]
    ingest_queue_roles: set[str] = set()
    for row in policies:
        if str(row.policyname) == "ingest_attempt_queue":
            ingest_queue_roles = {str(role) for role in row.roles}
    # ADR-0006 records this cross-tenant queue policy as an explicit accepted risk; the
    # catalog locks it to the ingest role instead of silently widening app/jobs.
    assert ingest_queue_roles == {INGEST_ROLE}
    for table in JOBS_TABLES:
        assert "jobs_cross_tenant" in by_table_policies.get(table, set()), table
        assert "jobs_tenant" in by_table_policies.get(table, set()), table

    by_role = {str(row.rolname): row for row in roles if str(row.rolname) in RUNTIME_ROLES}
    assert set(by_role) == set(RUNTIME_ROLES)
    for role, row in by_role.items():
        assert not row.rolsuper, f"{role} is a superuser"
        assert not row.rolbypassrls, f"{role} has BYPASSRLS"
        assert not row.rolcanlogin, f"{role} can log in"


async def test_runtime_roles_cannot_write_the_alembic_version(
    migrated_database_url: str,
) -> None:
    """`alembic_version` is migration bookkeeping, never runtime data.

    The blanket grant on every public table also covered it. A runtime role able to rewrite
    the recorded revision could hide that the real schema diverged, so 0005 revokes
    INSERT/UPDATE/DELETE and keeps SELECT for diagnostics.
    """
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT r.rolname, "
                        "has_table_privilege(r.rolname, 'public.alembic_version', "
                        "'SELECT') AS can_select, "
                        "has_table_privilege(r.rolname, 'public.alembic_version', "
                        "'INSERT') AS can_insert, "
                        "has_table_privilege(r.rolname, 'public.alembic_version', "
                        "'UPDATE') AS can_update, "
                        "has_table_privilege(r.rolname, 'public.alembic_version', "
                        "'DELETE') AS can_delete "
                        "FROM pg_roles r WHERE r.rolname = ANY(:roles) "
                        "ORDER BY r.rolname"
                    ),
                    {"roles": list(RUNTIME_ROLES)},
                )
            ).all()
    finally:
        await engine.dispose()

    by_role = {str(row.rolname): row for row in rows}
    assert set(by_role) == set(RUNTIME_ROLES)
    # The API may read the revision (health/diagnostics); no runtime role may change it.
    assert by_role[APP_ROLE].can_select is True
    for role in RUNTIME_ROLES:
        row = by_role[role]
        assert not row.can_insert, f"{role} can INSERT into alembic_version"
        assert not row.can_update, f"{role} can UPDATE alembic_version"
        assert not row.can_delete, f"{role} can DELETE rows of alembic_version"

    # Catalog flags and the real privilege must agree: a write is rejected by PostgreSQL.
    async with _role_connection(migrated_database_url, role=APP_ROLE) as connection:
        with pytest.raises(DBAPIError) as excinfo:
            await connection.execute(text("UPDATE alembic_version SET version_num = 'forged'"))
    assert "permission denied" in str(excinfo.value.orig).lower()


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


async def test_jobs_role_only_widens_with_the_explicit_switch(
    migrated_database_url: str,
) -> None:
    alice, _bob, name = await _seed_two_tenants(migrated_database_url)
    query = text("SELECT count(*) FROM subjects WHERE name = :name")

    # No switch and no app.user_id: the jobs role sees nothing (not an error).
    async with _role_connection(migrated_database_url, role=JOBS_ROLE) as connection:
        assert (await connection.execute(query, {"name": name})).scalar_one() == 0

    # The index job binds the owner after the claim.
    async with _role_connection(
        migrated_database_url, role=JOBS_ROLE, user_id=alice
    ) as connection:
        assert (await connection.execute(query, {"name": name})).scalar_one() == 1

    # Dispatcher/reconciler/index claim: the documented cross-tenant switch.
    async with _role_connection(
        migrated_database_url, role=JOBS_ROLE, cross_tenant=True
    ) as connection:
        assert (await connection.execute(query, {"name": name})).scalar_one() == 2


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
                # PostgreSQL may keep the custom GUC defined as '' after a local set;
                # what matters is that it is not a user id and the role was reset.
                assert leaked in (None, "")
                assert str(current_role) != APP_ROLE

        # The safe bootstrap policies must still answer "no rows" for the empty value
        # instead of casting '' to uuid.
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text(f'SET LOCAL ROLE "{APP_ROLE}"'))
                for table in ("sessions", "users"):
                    count = await connection.scalar(
                        text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
                    )
                    assert count == 0, table
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


async def test_authenticated_reads_run_under_the_app_role(
    migrated_database_url: str,
) -> None:
    """A1.9-style reads through the real routers, not only raw SQL.

    The engine installs the production `begin` listener, so every request transaction
    downgrades to the app role; `resolve_session` binds the tenant exactly as it does in
    `app/core/db.py`. `/subjects` and `/me` both have to work with RLS on.
    """
    user_id = uuid.uuid4()
    token = f"token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    name = f"rls-http-{uuid.uuid4().hex[:10]}"

    seed = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with seed.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, google_sub, email) "
                    "VALUES (:id, :sub, :email)"
                ),
                {"id": user_id, "sub": f"sub-{user_id}", "email": "http@example.test"},
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, csrf_hash, expires_at) "
                    "VALUES (:user_id, :token_hash, :csrf_hash, now() + interval '1 hour')"
                ),
                {"user_id": user_id, "token_hash": token_hash, "csrf_hash": token_hash},
            )
            await connection.execute(
                text("INSERT INTO subjects (user_id, name) VALUES (:id, :name)"),
                {"id": user_id, "name": name},
            )
    finally:
        await seed.dispose()

    engine = create_async_engine(migrated_database_url)
    event.listen(engine.sync_engine, "begin", db._apply_tenant_settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            subjects = await client.get("/subjects", cookies={COOKIE_NAME: token})
            me = await client.get("/me", cookies={COOKIE_NAME: token})
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert subjects.status_code == 200
    assert [item["name"] for item in subjects.json()["items"]] == [name]
    assert me.status_code == 200
    assert me.json()["id"] == str(user_id)


async def test_commit_emits_with_the_tenant_already_bound(
    migrated_database_url: str,
) -> None:
    """The committing transaction is the one where `resolve_session` bound the tenant.

    `bind_user()` stores the tenant in a `ContextVar`, so the security property depends on
    session resolution and the request-scoped session teardown running in the same
    asyncio context. The connection-level `commit` listener reads the GUC from PostgreSQL
    while the transaction is still open (before COMMIT is sent), so this is server-side
    evidence, not a check of the Python variable.
    """
    user_id = uuid.uuid4()
    token = f"token-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    csrf_token = sessions.csrf_token_for(token)
    csrf_hash = hashlib.sha256(csrf_token.encode("utf-8")).hexdigest()
    name = f"rls-commit-{uuid.uuid4().hex[:10]}"

    seed = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with seed.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, google_sub, email) "
                    "VALUES (:id, :sub, :email)"
                ),
                {"id": user_id, "sub": f"sub-{user_id}", "email": "commit@example.test"},
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, csrf_hash, expires_at) "
                    "VALUES (:user_id, :token_hash, :csrf_hash, now() + interval '1 hour')"
                ),
                {"user_id": user_id, "token_hash": token_hash, "csrf_hash": csrf_hash},
            )
    finally:
        await seed.dispose()

    engine = create_async_engine(migrated_database_url)
    event.listen(engine.sync_engine, "begin", db._apply_tenant_settings)
    committed_tenants: list[str | None] = []

    def _record_committed_tenant(connection: Connection) -> None:
        committed_tenants.append(
            connection.exec_driver_sql("SELECT current_setting('app.user_id', true)").scalar()
        )

    event.listen(engine.sync_engine, "commit", _record_committed_tenant)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    teardown_tenants: list[uuid.UUID | None] = []

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                # The dependency teardown must run in the same asyncio context as session
                # resolution, or the ContextVar (and any later transaction) loses the tenant.
                teardown_tenants.append(db._current_user.get())

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            # POST /subjects resolves the session (binding the tenant) and commits the new
            # row in the same request-scoped session; `get_session` then closes it.
            response = await client.post(
                "/subjects",
                json={"name": name},
                cookies={COOKIE_NAME: token},
                headers={
                    "Origin": settings.allowed_origin,
                    "X-CSRF-Token": csrf_token,
                },
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert response.status_code == 201
    assert committed_tenants == [str(user_id)]
    assert teardown_tenants == [user_id]


async def test_real_google_upsert_bootstraps_without_a_tenant(
    migrated_database_url: str,
) -> None:
    """The production first-login path also works before `app.user_id` exists.

    `test_app_role_bootstraps_a_login...` proves the policy with raw SQL; this test runs
    `upsert_google_identity`, the code the OAuth callback actually calls, under
    `maulwurf_app` and without inheriting a tenant. It uses `bind_google_sub` exactly like
    the E2E seed, so the two sanctioned bootstrap callers cannot drift apart.
    """
    sub = f"sub-{uuid.uuid4()}"
    async with _role_connection(migrated_database_url, role=APP_ROLE) as connection:
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
        try:
            user = await oauth_google.upsert_google_identity(
                session,
                {
                    "sub": sub,
                    "email": "first-login@example.test",
                    "email_verified": True,
                    "name": "First Login",
                },
                {"access_token": "ya29.test-first-login", "expires_in": 3600},
            )
            assert user.google_sub == sub
            assert db._current_user.get() == user.id
            credential = await session.get(GoogleCredential, user.id)
            assert credential is not None
            assert credential.status == "connected"
        finally:
            await session.close()


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
