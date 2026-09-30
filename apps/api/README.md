# apps/api — FastAPI (área `A-`)

- Stack: FastAPI + Pydantic v2 + SQLAlchemy 2 async (§3.2). Versiones fijadas en
  `pyproject.toml` y `uv.lock`.
- Ownership por ticket en [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md): la
  letra marca el área, no a una persona (`prompts/` → `L-`, `app/workers/` → `J-`).
- Contratos de sesión/CSRF e ingesta congelados en `S1-v1` (§1 de
  [docs/plan/S1.md](../../docs/plan/S1.md)).

## Comandos

```bash
uv sync --extra dev
uv run ruff check . && uv run mypy .
uv run pytest -m "not integration and not provider and not load"
uv run uvicorn app.main:app --reload   # /healthz 200; /readyz necesita PostgreSQL y Redis
```

Integración (PostgreSQL/Redis) y migraciones (`alembic`): secciones 5 y 6 de
[docs/RUNBOOK-dev.md](../../docs/RUNBOOK-dev.md).
