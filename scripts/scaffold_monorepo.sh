#!/usr/bin/env bash
#
# scaffold_monorepo.sh — Maulwurf
#
# Tarea L1.1 del plan de sprints (docs/sprints/jose-leonardo.md):
# genera la estructura base del monorepo según docs/ESPECIFICACION.md §3.3.
#
#   - Crea carpetas de apps/api, apps/web, apps/ingest, infra, scripts.
#   - Escribe archivos esenciales (sin instalar dependencias ni levantar servicios).
#   - Idempotente: nunca sobrescribe un archivo existente (usa -n).
#
# Uso:
#   bash scripts/scaffold_monorepo.sh            # estructura + archivos esenciales
#   bash scripts/scaffold_monorepo.sh --with-env # además escribe .env (local, sin secretos reales)
#   bash scripts/scaffold_monorepo.sh --dry-run  # solo muestra qué crearía
#
# Fuera de alcance (deliberado):
#   - No instala paquetes (pip/npm) ni descarga imágenes: eso es de Jefferson en S1 (J1.1–J1.4).
#   - No crea secretos: NVIDIA_API_KEY y credenciales OAuth se inyectan solo por entorno.
#   - No genera Dockerfiles/Compose: propiedad de Jefferson (J1.1).
#   - No genera migrate/init de Alembic ni npx de Next: los dueños los ejecutan (A1.x / D1.x).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Flags en cualquier orden: --dry-run (no crear nada) y --with-env (escribir .env).
DRY=0
WITH_ENV=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --with-env) WITH_ENV=1 ;;
    *)
      echo "flag desconocido: $arg" >&2
      echo "uso: $0 [--dry-run] [--with-env]" >&2
      exit 2
      ;;
  esac
done

if [[ $DRY -eq 1 ]]; then
  echo "[dry-run] listaré la estructura sin crear nada (raíz: $ROOT)"
fi

# ---------------------------------------------------------------- helpers ----

mkdirs() {
  for d in "$@"; do
    if [[ $DRY -eq 1 ]]; then
      echo "  mkdir -p $d"
    elif [[ ! -d "$d" ]]; then
      mkdir -p "$d"
      echo "  + dir   $d"
    fi
  done
}

write_file() {
  # write_file <ruta> <<'EOF' ... EOF
  local path="$1"
  if [[ $DRY -eq 1 ]]; then
    echo "  write  $path"
  elif [[ -e "$path" ]]; then
    echo "  = skip $path (ya existe)"
  else
    mkdir -p "$(dirname "$path")"
    cat > "$path"
    echo "  + file  $path"
  fi
}

# ---------------------------------------------------------------- carpetas ----

echo "==> Carpetas (estructura §3.3 + servicio de ingesta del diagrama §3.1)"

mkdirs \
  apps/api/app/routers \
  apps/api/app/services \
  apps/api/app/workers \
  apps/api/app/models \
  apps/api/app/core \
  apps/api/prompts/v1 \
  apps/api/alembic/versions \
  apps/api/tests/unit \
  apps/api/tests/integration \
  apps/web/app \
  apps/web/components \
  apps/web/lib \
  apps/web/tests/unit \
  apps/web/tests/e2e \
  apps/ingest/maulwurf_ingest \
  apps/ingest/tests \
  infra \
  scripts/load \
  scripts/provider

# .gitkeep para que las carpetas vacías se versionen
echo "==> Marcadores .gitkeep"
for d in \
  apps/api/app/routers apps/api/app/services apps/api/app/workers \
  apps/api/app/models apps/api/app/core apps/api/prompts/v1 \
  apps/api/alembic/versions apps/api/tests/unit apps/api/tests/integration \
  apps/web/app apps/web/components apps/web/lib apps/web/tests/unit apps/web/tests/e2e \
  apps/ingest/maulwurf_ingest apps/ingest/tests \
  scripts/load scripts/provider; do
  if [[ $DRY -eq 1 ]]; then
    echo "  write  $d/.gitkeep"
  elif [[ ! -e "$d/.gitkeep" ]]; then
    : > "$d/.gitkeep"
  fi
done

# ---------------------------------------------------------------- raíz ----

echo "==> Archivos de raíz"

write_file .gitignore <<'EOF'
# --- Python ---
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/

# --- Node / Next.js ---
node_modules/
.next/
out/
npm-debug.log*
*.tsbuildinfo
.eslintcache

# --- Entornos y secretos (NUNCA commitear) ---
.env
.env.local
.env.*.local
*.pem
*.key

# --- Datos locales ---
*.db
*.sqlite3
*.log

# --- Audio: prohibido persistirlo (contrato del proyecto, AGENTS.md) ---
*.wav
*.mp3
*.m4a
*.ogg
*.opus
*.flac
*.webm
*.aac

# --- Cobertura / dumps ---
.coverage
coverage/
htmlcov/
pgdata/
*.dump

# --- SO / editor ---
.DS_Store
Thumbs.db
.idea/
.vscode/
EOF

write_file README.scaffold.md <<'EOF'
# Scaffold Maulwurf — qué generó este script y qué falta

Este árbol fue creado por `scripts/scaffold_monorepo.sh` (tarea L1.1 del líder).
Corresponde a la estructura §3.3 de `docs/ESPECIFICACION.md`.

## Propiedad de carpetas (plan de sprints, §5a del plan maestro)

| Ruta | Dueño |
|---|---|
| `apps/api/` | Andres (excepto `prompts/` → José, `app/workers/` → Jefferson) |
| `apps/ingest/` | Santiago |
| `apps/web/` | Daniel |
| `infra/` | Jefferson |
| `scripts/` | José (coordinación) |
| `apps/api/prompts/` | José |

## Próximos pasos por dueño

1. **Andres (A1.x):** `pip install fastapi "uvicorn[standard]" "pydantic-settings"` etc.;
   `alembic init alembic`; auth Google + sesión opaca.
2. **Daniel (D1.1):** `npx create-next-app@latest apps/web --typescript --tailwind --app`;
   luego `npx shadcn@latest init`.
3. **Santiago (S1):** spike F0 con `requirements-riva.txt`; prototipo efímero.
4. **Jefferson (J1.x):** `infra/docker-compose.yml` + `Caddyfile` + CI.
5. **José (L1.2):** ADRs en `docs/`.

> Regla: nada aquí está implementado. Los TODOs marcan trabajo pendiente de cada dueño.
EOF

write_file .env.example <<'EOF'
# Copiar a .env y completar con valores locales de prueba.
# NUNCA commitear .env. Los secretos reales van solo en el mecanismo de secretos del backend.
# En Docker Compose, MAULWURF_DATABASE_URL / MAULWURF_REDIS_URL usan los nombres de
# servicio (postgres, redis) en vez de localhost.

# --- Backend / API ---
MAULWURF_ENV=local
MAULWURF_SECRET_KEY=change-me-openssl-rand-hex-32
MAULWURF_ENCRYPTION_KEY=change-me-openssl-rand-hex-32   # AES-GCM, clave versionada (M1)
MAULWURF_DATABASE_URL=postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf
MAULWURF_REDIS_URL=redis://localhost:6379/0
MAULWURF_OAUTH_STATE_SECRET=change-me

# --- Google OAuth (credenciales de prueba, backend únicamente) ---
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# --- NVIDIA Riva (solo backend, ver docs/NVIDIA_RIVA.md) ---
NVIDIA_API_KEY=
RIVA_FUNCTION_ID=b702f636-f60c-4a3d-a6f4-f3568c13bd7d
RIVA_SERVER=grpc.nvcf.nvidia.com:443
RIVA_USE_SSL=true
RIVA_MAX_MESSAGE_LENGTH=1073741824

# --- Ingesta (límites provisorios; reemplazar por los medidos en el spike F0) ---
INGEST_MAX_UPLOAD_BYTES=209715200      # 200 MiB provisional, no aprobado hasta F0 (Santiago)
INGEST_SLOTS=2
INGEST_TMPFS_BYTES=268435456           # 256 MiB provisional por slot
EOF

# ---------------------------------------------------------------- apps/api ----

echo "==> apps/api (FastAPI — dueño: Andres)"

write_file apps/api/pyproject.toml <<'EOF'
[project]
name = "maulwurf-api"
version = "0.1.0"
description = "Maulwurf API — FastAPI + Pydantic v2 + SQLAlchemy 2 async (§3.2)"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "arq>=0.26",
  "redis>=5.0",
  "pgvector>=0.3",
  "httpx>=0.27",
  "authlib>=1.3",
  "cryptography>=42.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "ruff>=0.4",
  "mypy>=1.10",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S"]

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
markers = [
  "integration: pruebas de integración contra servicios reales de compose",
  "provider: pruebas provider-contract protegidas (nunca en CI de MR)",
  "load: pruebas de carga (local/protegida)",
]
EOF

write_file apps/api/app/__init__.py <<'EOF'
"""Maulwurf API."""
EOF

write_file apps/api/app/core/__init__.py <<'EOF'
"""Configuración, seguridad y logging (dueño: Andres)."""
EOF

write_file apps/api/app/core/config.py <<'EOF'
"""Configuración por entorno — nada sensible en código (A1.2).

TODO(Andres): completar campos según M1; los límites de ingesta se reemplazan
por los medidos en el spike F0 (ver docs/sprints/santiago.md S1).
"""
from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAULWURF_", env_file=".env", extra="forbid")
    # extra="forbid": una MAULWURF_* mal escrita falla en vez de ignorarse en silencio.

    env: str = "local"
    secret_key: SecretStr  # sin default: obliga a definirlo por entorno
    encryption_key: SecretStr  # AES-GCM versionada (M1); nunca loguear .get_secret_value()
    oauth_state_secret: SecretStr  # .env.example sí lo define; antes se descartaba en silencio
    database_url: PostgresDsn = "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    redis_url: str = "redis://localhost:6379/0"

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> "Settings":
        # Fail-fast: si un entorno no-local arranca con "change-me", que falle la app.
        if self.env != "local":
            for name in ("secret_key", "encryption_key", "oauth_state_secret"):
                if "change-me" in getattr(self, name).get_secret_value():
                    raise ValueError(f"{name} inseguro en env={self.env}")
        return self


settings = Settings()
EOF

write_file apps/api/app/main.py <<'EOF'
"""Punto de entrada FastAPI — esqueleto mínimo (A1.1).

TODO(Andres): routers de subjects/audios/me (M1, M2), CORS restrictivo,
CSRF/Origin en mutaciones.
"""
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

try:
    __version__ = version("maulwurf-api")
except PackageNotFoundError:
    # Dev sin instalar el paquete: no debe romper el import.
    __version__ = "0.1.0"

app = FastAPI(title="Maulwurf API", version=__version__)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # Liveness: solo prueba que el proceso responde; no toca dependencias.
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    # TODO(Andres): ping real a Postgres+pgvector y Redis; 503 si no listos (J1.5).
    return {"status": "ok"}
EOF

write_file apps/api/app/routers/__init__.py <<'EOF'
"""Routers — audios, chat, tasks, calendar, subjects (§3.3)."""
EOF

write_file apps/api/app/services/__init__.py <<'EOF'
"""Servicios — transcripcion, extraccion, rag, calendar, mail (§3.3)."""
EOF

write_file apps/api/app/workers/__init__.py <<'EOF'
"""Jobs ARQ — dueño: Jefferson (J1.5, J2.x). Solo IDs/config, nunca audio."""
EOF

write_file apps/api/app/models/__init__.py <<'EOF'
"""Modelos SQLAlchemy — dueño: Andres (§6)."""
EOF

write_file apps/api/README.md <<'EOF'
# apps/api — FastAPI (dueño: Andres)

- Stack: FastAPI + Pydantic v2 + SQLAlchemy 2 async (§3.2). Versiones fijadas en `pyproject.toml`.
- Contratos de sesión/cookie/CSRF: congelados S1 (§5 de PLAN_SPRINTS.md) — todos consumen.
- `prompts/` es propiedad de José (L3.1); `app/workers/` de Jefferson (J1.5).

## Comandos (una vez existan)

```bash
# TODO(A1.x): definir uvicorn, alembic, pytest aquí cuando Andres inicialice.
```
EOF

# ---------------------------------------------------------------- apps/web ----

echo "==> apps/web (Next.js — dueño: Daniel)"

write_file apps/web/package.json <<'EOF'
{
  "name": "maulwurf-web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint ."
  },
  "dependencies": {
    "next": "^15",
    "react": "^19",
    "react-dom": "^19"
  },
  "devDependencies": {
    "@types/node": "^22",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "typescript": "^5",
    "tailwindcss": "^4",
    "vitest": "^3",
    "eslint": "^9",
    "eslint-config-next": "^15.1.3"
  }
}
EOF

write_file apps/web/eslint.config.mjs <<'EOF'
import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  { ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"] },
];

export default eslintConfig;
EOF

write_file apps/web/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["**/*.ts", "**/*.tsx", "next-env.d.ts", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
EOF

write_file apps/web/app/layout.tsx <<'EOF'
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Maulwurf",
  description: "De la grabación de clase al estudio accionable.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
EOF

write_file apps/web/app/page.tsx <<'EOF'
export default function Home() {
  // TODO(D1.2): rutas /biblioteca /chat /materias /settings + navegación.
  return (
    <main>
      <h1>Maulwurf</h1>
      <p>Scaffold inicial — implementación pendiente (D1.x).</p>
    </main>
  );
}
EOF

write_file apps/web/README.md <<'EOF'
# apps/web — Next.js (dueño: Daniel)

- Next.js App Router + TypeScript + Tailwind + shadcn/ui (§3.2).
- El navegador nunca ve secretos ni audio (regla transversal de la ficha de Daniel).
- `npx create-next-app` NO es necesario: este scaffold ya incluye `package.json`,
  `tsconfig.json`, `app/layout.tsx` y `app/page.tsx`. Solo faltan:

```bash
cd apps/web
npm install                # genera package-lock.json (lockfile exigido en L1.1)
npx shadcn@latest init     # D1.1
```
EOF

# ---------------------------------------------------------------- apps/ingest ----

echo "==> apps/ingest (servicio de ingesta — dueño: Santiago)"

write_file apps/ingest/pyproject.toml <<'EOF'
[project]
name = "maulwurf-ingest"
version = "0.1.0"
description = "Maulwurf ingestion — ffmpeg/ffprobe + Riva + tmpfs efímero (sin audio durable)"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "nvidia-riva-client==2.27.0",   # pin de requirements-riva.txt
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "pytest-asyncio>=0.23", "ruff>=0.4", "mypy>=1.10"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
markers = [
  "integration: servicios reales de compose",
  "provider: protegida, presupuesto autorizado (spike F0.1)",
  "load: local/protegida",
]
EOF

write_file apps/ingest/maulwurf_ingest/__init__.py <<'EOF'
"""Servicio de ingesta — dueño: Santiago (S1 spike, S2 pipeline).

Regla de oro: cero audio durable (disco, Redis, logs, cachés). Si algo persiste
audio, se bloquea la entrega entera (F0.2/G1).
"""
EOF

write_file apps/ingest/README.md <<'EOF'
# apps/ingest — Ingesta efímera (dueño: Santiago)

- Contrato medido primero: el spike F0.1 (S1) fija idiomas, límites, timestamps
  (`docs/NVIDIA_RIVA.md`). Nada de constantes duras hasta tener el informe.
- tmpfs acotado por intento, cleanup en `finally`, lease/fencing, reconciliador post-commit.
- `GET /ingestion/capabilities` expone solo los límites medidos (U-S2-SG-01).
EOF

# ---------------------------------------------------------------- infra ----

echo "==> infra (placeholders — dueño: Jefferson)"

write_file infra/README.md <<'EOF'
# infra/ — dueño: Jefferson (J1.x)

Pendiente (placeholders intencionales, no implementación):

- `docker-compose.yml` — 8 servicios: web, api, ingest, worker, scheduler,
  postgres (`pgvector/pgvector:pg16` fijada), redis, caddy. Sin MinIO/S3 ni volumen de audio.
- `Caddyfile` — proxy sin buffering/caché para uploads.
- CI (GitLab): ruff+ESLint, mypy+tsc, pytest+Vitest, builds.
EOF

# ---------------------------------------------------------------- .env ----

if [[ $WITH_ENV -eq 1 && $DRY -eq 0 ]]; then
  if [[ ! -e .env ]]; then
    cp .env.example .env
    echo "  + file  .env (desde .env.example; completar secretos localmente)"
  else
    echo "  = skip .env (ya existe)"
  fi
elif [[ $WITH_ENV -eq 1 && $DRY -eq 1 ]]; then
  echo "  write  .env (desde .env.example)"
fi

# ---------------------------------------------------------------- fin ----

echo
echo "==> Scaffold listo en $ROOT"
echo "    Siguiente paso por dueño: ver README.scaffold.md"
if [[ $DRY -eq 1 ]]; then
  echo "    (dry-run: no se creó nada)"
fi
