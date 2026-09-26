# syntax=docker/dockerfile:1

# J1.8 — target `base`: imagen de ingesta con ffmpeg/ffprobe fijados.
# J1.2 agrega el target `runtime` endurecido; `base` queda para tareas sin hardening.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    TMPDIR=/work/tmp

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /work/tmp && chmod 1777 /work/tmp

COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY maulwurf_ingest ./maulwurf_ingest

ENV PATH="/opt/venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "maulwurf_ingest.main:app", "--host", "0.0.0.0", "--port", "8000"]
