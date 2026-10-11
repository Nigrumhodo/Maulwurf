"""I-S1-AN-04 (A1.7): Alembic desde vacío, ida y vuelta y ejecución concurrente.

Usa la BD aislada de la sesión: cada test la baja a `base` (esquema vacío) y la deja en
`head` al terminar, así el resto de la suite sigue encontrando el esquema completo.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.integration.conftest import API_ROOT, alembic_config

pytestmark = pytest.mark.integration

S1_TABLES = {
    "audios",
    "google_credentials",
    "ingestion_attempts",
    "outbox_events",
    "sessions",
    "subjects",
    "users",
}

S2_TABLES = S1_TABLES | {
    "variant_confirmation_tokens",
    "transcripts",
    "segments",
    "chunks",
    "chunk_segments",
    "embeddings",
    "index_generations",
    "processing_runs",
    "conversations",
    "messages",
    "message_sources",
}

S2_COMPOSITE_FOREIGN_KEYS = {
    "variant_confirmation_tokens": {
        "FOREIGN KEY (user_id, canonical_audio_id) REFERENCES audios(user_id, id)",
        "FOREIGN KEY (user_id, reserved_audio_id) REFERENCES audios(user_id, id)",
        "FOREIGN KEY (user_id, subject_id) REFERENCES subjects(user_id, id)",
    },
    "transcripts": {"FOREIGN KEY (user_id, audio_id) REFERENCES audios(user_id, id)"},
    "segments": {"FOREIGN KEY (user_id, transcript_id) REFERENCES transcripts(user_id, id)"},
    "chunks": {
        "FOREIGN KEY (user_id, audio_id) REFERENCES audios(user_id, id)",
        "FOREIGN KEY (user_id, transcript_id) REFERENCES transcripts(user_id, id)",
    },
    "chunk_segments": {
        "FOREIGN KEY (user_id, chunk_id) REFERENCES chunks(user_id, id)",
        "FOREIGN KEY (user_id, segment_id) REFERENCES segments(user_id, id)",
    },
    "embeddings": {"FOREIGN KEY (user_id, chunk_id) REFERENCES chunks(user_id, id)"},
    "processing_runs": {"FOREIGN KEY (user_id, audio_id) REFERENCES audios(user_id, id)"},
    "conversations": {
        "FOREIGN KEY (user_id, subject_id) REFERENCES subjects(user_id, id)",
        "FOREIGN KEY (user_id, audio_id) REFERENCES audios(user_id, id)",
    },
    "messages": {"FOREIGN KEY (user_id, conversation_id) REFERENCES conversations(user_id, id)"},
    "message_sources": {
        "FOREIGN KEY (user_id, message_id) REFERENCES messages(user_id, id)",
        "FOREIGN KEY (user_id, audio_id) REFERENCES audios(user_id, id)",
        "FOREIGN KEY (user_id, transcript_id) REFERENCES transcripts(user_id, id)",
        "FOREIGN KEY (user_id, chunk_id) REFERENCES chunks(user_id, id)",
        "FOREIGN KEY (user_id, segment_id) REFERENCES segments(user_id, id)",
    },
}

S2_INDEX_FRAGMENTS = {
    "chunks_fts": "USING gin (tsv)",
    "embeddings_hnsw": "USING hnsw (vector vector_cosine_ops)",
    "variant_tokens_expiry": "WHERE (consumed_at IS NULL)",
    "index_generations_one_active": "WHERE (status = 'active'::text)",
    "messages_client_idem": "WHERE (client_message_id IS NOT NULL)",
}


def _normalized_constraint(definition: str) -> str:
    """Devuelve la firma de una FK, independiente del formato y acciones del servidor."""
    normalized = " ".join(definition.split()).replace(" (", "(")
    for action in (" ON DELETE ", " ON UPDATE "):
        normalized = normalized.split(action, maxsplit=1)[0]
    return normalized


def _head(url: str) -> str:
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    assert head is not None
    return head


def _state(url: str) -> tuple[set[str], list[str]]:
    """Tablas de la app y filas de `alembic_version`."""

    async def read() -> tuple[set[str], list[str]]:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
            versions: list[str] = []
            if "alembic_version" in tables:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                versions = list(result.scalars())
        await engine.dispose()
        return tables - {"alembic_version"}, versions

    return asyncio.run(read())


def _s2_catalog(url: str) -> tuple[dict[str, set[str]], dict[str, str], set[str]]:
    """Constraints, índices y columnas reales; no depende de metadata ORM."""

    async def read() -> tuple[dict[str, set[str]], dict[str, str], set[str]]:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            constraints = await conn.execute(
                text(
                    "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
                )
            )
            indexes = await conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            )
            audio_columns = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'audios'"
                )
            )
        await engine.dispose()
        foreign_keys: dict[str, set[str]] = {}
        for table_name, definition in constraints:
            if str(definition).startswith("FOREIGN KEY"):
                foreign_keys.setdefault(str(table_name), set()).add(str(definition))
        return (
            foreign_keys,
            {str(name): str(definition) for name, definition in indexes},
            set(audio_columns.scalars()),
        )

    return asyncio.run(read())


@pytest.fixture
def empty_schema(migrated_database_url: str) -> Iterator[str]:
    """La BD de la sesión en `base`; al terminar vuelve a `head` pase lo que pase."""
    cfg = alembic_config(migrated_database_url)
    command.downgrade(cfg, "base")
    try:
        yield migrated_database_url
    finally:
        command.upgrade(cfg, "head")


def test_upgrade_from_empty_reaches_head(empty_schema: str) -> None:
    assert _state(empty_schema) == (set(), [])

    command.upgrade(alembic_config(empty_schema), "head")

    assert _state(empty_schema) == (S2_TABLES, [_head(empty_schema)])


def test_downgrade_and_upgrade_again(empty_schema: str) -> None:
    cfg = alembic_config(empty_schema)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "-1")
    assert _state(empty_schema) == (S1_TABLES, ["0001"])

    command.upgrade(cfg, "head")
    assert _state(empty_schema)[0] == S2_TABLES


def test_upgrade_is_idempotent(empty_schema: str) -> None:
    cfg = alembic_config(empty_schema)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")

    assert _state(empty_schema)[1] == [_head(empty_schema)]


def test_s2_catalog_has_composite_fks_indexes_and_no_audio_storage_key(
    migrated_database_url: str,
) -> None:
    foreign_keys, indexes, audio_columns = _s2_catalog(migrated_database_url)

    for table_name, expected in S2_COMPOSITE_FOREIGN_KEYS.items():
        normalized_expected = {_normalized_constraint(definition) for definition in expected}
        normalized_actual = {
            _normalized_constraint(definition)
            for definition in foreign_keys.get(table_name, set())
        }
        assert normalized_expected <= normalized_actual
    for index_name, fragment in S2_INDEX_FRAGMENTS.items():
        assert fragment in indexes[index_name]
    assert "storage_key" not in audio_columns


def test_concurrent_upgrades_serialize_without_errors(empty_schema: str) -> None:
    # Varias réplicas de la API migran al arrancar; el advisory lock las serializa. Se usan
    # procesos, no hilos: el `context` de Alembic es global al proceso y dos hilos se lo
    # pisan (así una réplica queda con el lock tomado y las demás esperan para siempre).
    env = {**os.environ, "MAULWURF_DATABASE_URL": empty_schema}
    replicas = [
        subprocess.Popen(  # noqa: S603 - argumentos fijos, sin entrada externa
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    deadline = time.monotonic() + 90  # presupuesto TOTAL, no 90 s por réplica
    for replica in replicas:
        try:
            _, stderr = replica.communicate(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            # Sin matarlas, una réplica colgada retendría el advisory lock y el `finally`
            # del fixture (`upgrade head`) esperaría hasta el lock_timeout.
            for pending in replicas:
                pending.kill()
                pending.communicate()
            raise
        assert replica.returncode == 0, stderr.decode()[-500:]

    assert _state(empty_schema) == (S2_TABLES, [_head(empty_schema)])
