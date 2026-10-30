"""`/internal/metrics` endpoint (J2.5): Prometheus text for the internal network only.

Caddy answers 404 to `/internal/*` (A2.13 and `infra/Caddyfile`), so the route is not
public. The endpoint does not authenticate: it relies on that network boundary, not on a
secret.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.db import session_scope
from app.core.prometheus import PROMETHEUS_CONTENT_TYPE, render
from app.services.outbox_metrics import collect_outbox_samples

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Outbox metrics in the Prometheus exposition format."""
    async with session_scope() as db:
        samples = await collect_outbox_samples(db)
    return PlainTextResponse(render(samples), media_type=PROMETHEUS_CONTENT_TYPE)
