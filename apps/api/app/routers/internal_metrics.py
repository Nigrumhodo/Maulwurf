"""`/internal/metrics` endpoint (J2.5): Prometheus text for the internal network only.

Caddy answers 404 to `/internal/*` (A2.13 and `infra/Caddyfile`), so the route is not
public. The endpoint does not authenticate: it relies on that network boundary, not on a
secret.

A2.17: the outbox aggregate is cross-tenant by definition. The read runs in one transaction
as the jobs role with the explicit `app.cross_tenant` switch (the same documented policy the
dispatcher/reconciler use), because the API's tenant policy would hide other users' rows.
It is read-only, internal-only and returns aggregate counts, never rows or identifiers.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.db import session_scope
from app.core.prometheus import PROMETHEUS_CONTENT_TYPE, render
from app.services.outbox_metrics import collect_outbox_samples
from app.services.provider_budget import collect_budget_samples

router = APIRouter(prefix="/internal", tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Outbox and provider-budget metrics in the Prometheus exposition format."""
    async with session_scope() as db:
        samples = await collect_outbox_samples(db, cross_tenant=True)
        samples.extend(await collect_budget_samples(db, cross_tenant=True))
    return PlainTextResponse(render(samples), media_type=PROMETHEUS_CONTENT_TYPE)
