# apps/api — FastAPI (area `A-`)

- Stack: FastAPI + Pydantic v2 + SQLAlchemy 2 async (§3.2). Versions pinned in
  `pyproject.toml` and `uv.lock`.
- Per-ticket ownership in [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md): the
  letter marks the area, not a person (`prompts/` → `L-`, `app/workers/` → `J-`).
- Session/CSRF and ingestion contracts frozen in `S1-v1` (§1 of
  [docs/plan/S1.md](../../docs/plan/S1.md)).

## Commands

```bash
uv sync --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"
uv run uvicorn app.main:app --reload   # /healthz 200; /readyz needs PostgreSQL and Redis
```

Integration (PostgreSQL/Redis) and migrations (`alembic`): sections 5 and 6 of
[docs/RUNBOOK-dev.md](../../docs/RUNBOOK-dev.md).
