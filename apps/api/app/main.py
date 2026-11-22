"""Punto de entrada FastAPI (A1.1): ensambla routers; la lógica vive en `routers/` y `services/`."""
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from app.core.cache_control import NoStoreMiddleware
from app.core.config import settings
from app.core.db import engine, runtime_privilege_check_enabled, verify_db_role_least_privilege
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.request_context import RequestIdMiddleware
from app.routers import (
    audios,
    auth,
    calendar,
    chat,
    health,
    ingestion,
    integrations,
    internal_metrics,
    me,
    notifications,
    search,
    subjects,
    tasks,
)
from app.services.ingest_sink import close_ingest_client
from app.services.llm import close_llm_http_client

try:
    __version__ = version("maulwurf-api")
except PackageNotFoundError:
    # Dev sin instalar el paquete: no debe romper el import.
    __version__ = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Staging and production must not boot on a login role that ignores RLS.
    # Local keeps the owner DSN and only logs; pytest skips the catalog query.
    if runtime_privilege_check_enabled():
        await verify_db_role_least_privilege(engine)
    yield
    # Cierra el pool al parar (compose stop / SIGTERM) en vez de abandonar conexiones.
    await close_llm_http_client()
    await close_ingest_client()
    await engine.dispose()


_expose_docs = settings.env == "local"
app = FastAPI(
    title="Maulwurf API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _expose_docs else None,
    redoc_url="/redoc" if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
)
install_error_handlers(app)
app.add_middleware(RequestIdMiddleware)
# Added last so it is the outermost user middleware: it sees redirects, SSE starts and the
# framework's own 404/405, not only the routes that remembered the header.
app.add_middleware(NoStoreMiddleware)
app.include_router(health.router)
app.include_router(ingestion.router)
app.include_router(me.router)
app.include_router(notifications.router)
app.include_router(auth.router)
app.include_router(audios.router)
app.include_router(integrations.router)
app.include_router(subjects.router)
app.include_router(tasks.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(calendar.router)
app.include_router(internal_metrics.router)

# These routes do not use the session cookie. `/internal/metrics` has its own bearer.
_PUBLIC_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/auth/google/start",
        "/auth/google/callback",
        "/internal/metrics",
    }
)
_SESSION_CALLS = frozenset(
    {
        "current_session",
        "require_mutation",
        "deletion_session",
        "require_mutation_any",
        "resolve_authenticated_session",
        "resolve_mutation_session",
    }
)
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})


def _dependency_calls(dependant: Any) -> set[str]:
    names: set[str] = set()
    call = getattr(dependant, "call", None)
    if callable(call):
        names.add(getattr(call, "__name__", ""))
    for child in getattr(dependant, "dependencies", ()) or ():
        names.update(_dependency_calls(child))
    return names


def _endpoint_calls(endpoint: Any) -> set[str]:
    """Names the endpoint body references, including short-lived session helpers.

    SSE and search authenticate inside the handler so a request-scoped session is
    not held for the whole response. Those calls never show up as dependencies.
    """
    names: set[str] = set()
    seen: set[int] = set()
    current = endpoint
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "__code__", None)
        if code is not None:
            names.update(code.co_names)
        current = getattr(current, "__wrapped__", None)
    return names


def _operations(path_item: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for method, operation in path_item.items():
        if method in _HTTP_METHODS and isinstance(operation, dict):
            yield method, operation


def _openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["sessionCookie"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": "mw_session",
        "description": (
            "Opaque session cookie. Mutations also require the X-CSRF-Token header "
            "and a same-origin Origin."
        ),
    }
    schemes["csrfHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-CSRF-Token",
        "description": (
            "CSRF token derived from the session cookie. Required on POST, PUT, PATCH and DELETE."
        ),
    }
    # FastAPI stores included routers as `_IncludedRouter`, not as flat APIRoutes.
    # `iter_route_contexts` is the same walk `get_openapi` uses to build paths.
    authenticated: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        dependant = getattr(context, "dependant", None)
        endpoint = context.endpoint
        path = context.path_format
        if dependant is None or endpoint is None or not path:
            continue
        calls = _dependency_calls(dependant) | _endpoint_calls(endpoint)
        if not calls & _SESSION_CALLS:
            continue
        for method in context.methods or ():
            authenticated.add((path, method.lower()))
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict) or path in _PUBLIC_PATHS:
            continue
        for method, operation in _operations(path_item):
            if (path, method) not in authenticated:
                continue
            mutating = method in _MUTATING_METHODS
            operation["security"] = (
                [{"sessionCookie": [], "csrfHeader": []}]
                if mutating
                else [{"sessionCookie": []}]
            )
            responses = operation.setdefault("responses", {})
            responses.setdefault(
                "401", {"description": "auth_required: sesión ausente o vencida"}
            )
            if mutating:
                responses.setdefault(
                    "403",
                    {"description": "csrf_invalid: Origin o X-CSRF-Token rechazados"},
                )
    app.openapi_schema = schema
    return schema


app.openapi = _openapi  # type: ignore[method-assign]
