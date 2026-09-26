"""I-S1-JF-03 (J1.6): Redis solo IDs/estado y sin persistencia duradera.

Evidencia de G1: ninguna clave con payload mayor que el umbral documentado en
ADR-0005 (4 KiB) tras ejercitar los flujos de S1. No imprime valores de claves:
solo tamaños y booleanos.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable

import pytest
import redis.asyncio as redis

pytestmark = pytest.mark.integration

VALUE_THRESHOLD_BYTES = 4096  # ADR-0005
ALLOWED_KEY_PREFIXES = ("arq:", "mw:")
FORBIDDEN_MARKERS = ("RIFF", "Bearer ", "NVIDIA_API_KEY", "data:audio")
STATE_KEY = "mw:test:redis-policy-state"


def _redis_url() -> str:
    return os.environ.get("MAULWURF_REDIS_URL", "redis://localhost:6379/0")


async def _iter_keys(client: redis.Redis) -> AsyncIterator[str]:
    async for key in client.scan_iter(match="*"):
        yield key


async def _int_result(value: Awaitable[int] | int) -> int:
    # redis-py tipa algunas operaciones como sync|async; en asyncio siempre es awaitable.
    return int(value) if isinstance(value, int) else int(await value)


async def _value_size(client: redis.Redis, key: str, kind: str) -> int | None:
    if kind == "string":
        return await _int_result(client.strlen(key))
    if kind == "list":
        return await _int_result(client.llen(key))
    if kind == "hash":
        return await _int_result(client.hlen(key))
    if kind == "set":
        return await _int_result(client.scard(key))
    if kind == "zset":
        return await _int_result(client.zcard(key))
    if kind == "stream":
        return await _int_result(client.xlen(key))
    return None


async def test_redis_holds_only_small_id_state_values() -> None:
    """ADR-0005: umbral de 4 KiB, claves `arq:`/`mw:` y nada de audio/tokens."""
    client: redis.Redis = redis.from_url(  # type: ignore[no-untyped-call]
        _redis_url(), decode_responses=True
    )
    try:
        await client.ping()

        # Flujo representativo disponible en S1 (los health keys de ARQ llegan en J1.5).
        await client.set(STATE_KEY, "audio_id=00000000-0000-0000-0000-000000000000")

        keys = [key async for key in _iter_keys(client)]
        assert keys, "sin claves en Redis no hay evidencia que barrer"

        max_size = 0
        for key in keys:
            assert key.startswith(ALLOWED_KEY_PREFIXES), f"clave fuera del catálogo: {key}"

            kind = str(await client.type(key))
            size = await _value_size(client, key, kind)
            if size is not None:
                max_size = max(max_size, size)

            if kind == "string":
                head = str(await client.getrange(key, 0, 64))
                assert not any(marker in head for marker in FORBIDDEN_MARKERS), (
                    f"marcador prohibido en {key}"
                )

        assert max_size <= VALUE_THRESHOLD_BYTES, (
            f"valor de {max_size} B supera el umbral de {VALUE_THRESHOLD_BYTES} B (ADR-0005)"
        )
    finally:
        await client.delete(STATE_KEY)
        await client.aclose()


async def test_redis_persistence_is_disabled() -> None:
    """ADR-0005: persistencia duradera solo en PostgreSQL."""
    client: redis.Redis = redis.from_url(  # type: ignore[no-untyped-call]
        _redis_url(), decode_responses=True
    )
    try:
        save = await client.config_get("save")
        policy = await client.config_get("maxmemory-policy")
        info = await client.info("persistence")
    finally:
        await client.aclose()

    assert save.get("save", None) == "", "Redis no debe guardar RDB (ADR-0005)"
    assert policy.get("maxmemory-policy") == "noeviction", "Redis no debe evacuar silenciosamente"
    assert info.get("aof_enabled") in (0, False), "Redis no debe usar AOF (ADR-0005)"
