#!/usr/bin/env python3
"""E-S2-JF-06: full pipeline E2E smoke against the compose stack (J2.6).

Stages: synthetic audio in RAM -> `POST /audios` -> streaming `PUT` -> `ingest` (ffmpeg +
Riva transport double) -> atomic transcript commit -> verified cleanup -> outbox ->
`index(audio_id)` in the worker -> hybrid search -> chat SSE with a validated citation.

The driver runs inside the `api` container (``docker compose exec -T api python -``) so it
can seed one user + opaque session directly, exactly like the integration fixtures: Google
OAuth is not exercised and no credential is needed. The audio is a synthetic tone generated
at execution time; it is never written to disk by this script and never becomes an artifact.

Gate/contract: G5-F1 and the frozen interfaces of S2 §1.1/§1.2/§1.3. This is an API-level
E2E smoke over the compose network; the browser/Caddy leg belongs to D2.x and stays out of
scope (apps/web is not touched).

Exit codes: 0 on success, 1 on a failed assertion or timeout.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import struct
import sys
import time
import uuid
import wave
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the adapter is imported lazily so the helper tests need no httpx
    import httpx

DEFAULT_EXPECTED_TEXT = (
    "La fotosíntesis convierte la luz del sol en energía química. "
    "Las plantas usan clorofila."
)
QUESTION = "¿Qué convierte la luz del sol en energía química?"
WAV_SECONDS = 2.4
WAV_RATE = 16_000
TRANSCRIPT_TIMEOUT_S = float(os.environ.get("SMOKE_TRANSCRIPT_TIMEOUT_S", "120"))
INDEX_TIMEOUT_S = float(os.environ.get("SMOKE_INDEX_TIMEOUT_S", "180"))
CHAT_TIMEOUT_S = float(os.environ.get("SMOKE_CHAT_TIMEOUT_S", "120"))
TERMINAL_ATTEMPT_STATES = frozenset({"rejected", "requires_reupload", "cancelled"})


class SmokeFailure(RuntimeError):
    """One stage did not reach the expected state."""


@dataclass(frozen=True)
class SseEvent:
    name: str
    data: dict[str, Any]
    event_id: int | None


def synthetic_wav(seconds: float = WAV_SECONDS, rate: int = WAV_RATE) -> bytes:
    """Mono PCM s16 WAV generated in RAM. A tone, not a voice: no person is recorded."""
    frames = int(seconds * rate)
    samples = bytearray()
    for index in range(frames):
        value = int(6000 * math.sin(2 * math.pi * 220 * index / rate))
        samples += struct.pack("<h", value)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(bytes(samples))
    return buffer.getvalue()


def parse_sse(lines: Iterable[str]) -> list[SseEvent]:
    """Collect named SSE events; comment lines and unknown fields are ignored."""
    events: list[SseEvent] = []
    name = ""
    event_id: int | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal name, event_id, data_lines
        if data_lines:
            raw = "\n".join(data_lines)
            try:
                payload: object = json.loads(raw)
            except ValueError:
                payload = {"raw": raw}
            events.append(
                SseEvent(
                    name=name or "message",
                    data=payload if isinstance(payload, dict) else {"value": payload},
                    event_id=event_id,
                )
            )
        name = ""
        event_id = None
        data_lines = []

    for line in lines:
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            name = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = None
    flush()
    return events


async def _iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    name = ""
    event_id: int | None = None
    data_lines: list[str] = []
    async for line in lines:
        if not line:
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    payload: object = json.loads(raw)
                except ValueError:
                    payload = {"raw": raw}
                yield SseEvent(
                    name=name or "message",
                    data=payload if isinstance(payload, dict) else {"value": payload},
                    event_id=event_id,
                )
            name = ""
            event_id = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            name = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = None
    if data_lines:
        raw = "\n".join(data_lines)
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        yield SseEvent(
            name=name or "message",
            data=payload if isinstance(payload, dict) else {"value": payload},
            event_id=event_id,
        )


def _require_json(response: httpx.Response, *, step: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise SmokeFailure(
            f"{step}: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        body: object = response.json()
    except ValueError:
        raise SmokeFailure(f"{step}: non-JSON response") from None
    if not isinstance(body, dict):
        raise SmokeFailure(f"{step}: unexpected JSON shape")
    return body


def _stage(detail: Mapping[str, Any], name: str) -> str:
    stages = detail.get("stages")
    if not isinstance(stages, Mapping):
        return "missing"
    value = stages.get(name)
    return value if isinstance(value, str) else "missing"


def _attempt(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    attempt = detail.get("attempt")
    if not isinstance(attempt, Mapping):
        raise SmokeFailure("audio detail has no attempt yet")
    return attempt


async def _seed() -> tuple[str, str, uuid.UUID]:
    """Create one user, one opaque session and one conversation; return cookie, csrf, id."""
    from app.core.db import SessionFactory
    from app.models import User
    from app.models.chat import Conversation
    from app.services import sessions as session_service

    async with SessionFactory() as db:
        user = User(
            google_sub=f"e2e-{uuid.uuid4().hex}",
            email=f"e2e-{uuid.uuid4().hex[:8]}@maulwurf.invalid",
            email_verified=True,
        )
        db.add(user)
        await db.flush()
        issued = await session_service.create_session(db, user.id)
        conversation = Conversation(user_id=user.id, mode="all", title="E-S2-JF-06")
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id
        return issued.cookie_token, issued.csrf_token, conversation_id


async def _wait_for(
    fetch: Any,
    *,
    step: str,
    timeout_s: float,
    predicate: Any,
    interval_s: float = 1.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await fetch()
        if predicate(last):
            return last
        attempt = last.get("attempt")
        if isinstance(attempt, Mapping) and attempt.get("status") in TERMINAL_ATTEMPT_STATES:
            raise SmokeFailure(
                f"{step}: attempt ended as {attempt.get('status')} "
                f"({attempt.get('cleanup_status')})"
            )
        await asyncio.sleep(interval_s)
    raise SmokeFailure(
        f"{step}: timeout after {timeout_s:.0f}s; stages={last.get('stages')} "
        f"attempt={last.get('attempt')}"
    )


def _mutating_headers(csrf: str, origin: str) -> dict[str, str]:
    return {"Origin": origin, "X-CSRF-Token": csrf}


async def _run() -> dict[str, Any]:
    import httpx
    from app.core.config import settings
    from app.core.db import SessionFactory

    origin = settings.allowed_origin
    expected_text = os.environ.get("SMOKE_EXPECTED_TEXT", DEFAULT_EXPECTED_TEXT).strip()
    wav = synthetic_wav()
    digest = hashlib.sha256(wav).hexdigest()
    cookie, csrf, conversation_id = await _seed()
    # Explicit cookie header instead of a cookie jar: the smoke talks plain HTTP to the API
    # container's loopback, and the production cookie carries `Secure`.
    headers = {"Cookie": f"mw_session={cookie}", **_mutating_headers(csrf, origin)}
    summary: dict[str, Any] = {"stages": {}}

    async with httpx.AsyncClient(
        base_url=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"),
        headers=headers,
        timeout=httpx.Timeout(CHAT_TIMEOUT_S, connect=10.0),
    ) as client:
        subject = _require_json(
            await client.post(
                "/subjects",
                json={"name": f"E2E {uuid.uuid4().hex[:8]}"},
            ),
            step="POST /subjects",
        )

        created = _require_json(
            await client.post(
                "/audios",
                json={
                    "subject_id": subject["id"],
                    "class_date": "2026-01-15",
                    "class_timezone": "America/Bogota",
                    "language_code": "es",
                    "title": "E-S2-JF-06 synthetic class",
                    "privacy_notice_version": settings.privacy_notice_version,
                    "cloud_processing_accepted": True,
                    "third_party_voice_acknowledged": True,
                },
            ),
            step="POST /audios",
        )
        audio_id = str(created["audio_id"])
        attempt_id = str(created["attempt_id"])
        summary["audio_id"] = audio_id

        upload = await client.put(
            f"/audios/{audio_id}/content",
            params={"attempt_id": attempt_id},
            content=wav,
            headers={**headers, "Content-Type": "audio/wav"},
        )
        _require_json(upload, step="PUT content")
        summary["upload_status"] = upload.status_code

        async def detail() -> dict[str, Any]:
            return _require_json(
                await client.get(f"/audios/{audio_id}"), step="GET /audios/{id}"
            )

        def transcript_ready(current: Mapping[str, Any]) -> bool:
            attempt = current.get("attempt")
            transcript = current.get("transcript")
            return (
                isinstance(attempt, Mapping)
                and attempt.get("cleanup_status") == "verified"
                and attempt.get("status") == "succeeded"
                and isinstance(transcript, Mapping)
            )

        current = await _wait_for(
            detail,
            step="transcript+cleanup",
            timeout_s=TRANSCRIPT_TIMEOUT_S,
            predicate=transcript_ready,
        )
        summary["stages"]["transcript"] = "available"
        summary["stages"]["cleanup"] = _attempt(current)["cleanup_status"]
        summary["transcript_version"] = current["transcript"]["version"]

        text_response = await client.get(f"/audios/{audio_id}/transcript.txt")
        if text_response.status_code != 200:
            raise SmokeFailure(
                f"GET transcript.txt: HTTP {text_response.status_code}"
            )
        transcript_text = text_response.text.strip()
        summary["transcript_matches_fixture"] = transcript_text == expected_text
        if not summary["transcript_matches_fixture"]:
            raise SmokeFailure("transcript.txt does not match the synthetic fixture")

        def indexed(current_detail: Mapping[str, Any]) -> bool:
            return _stage(current_detail, "index") == "succeeded"

        current = await _wait_for(
            detail,
            step="index",
            timeout_s=INDEX_TIMEOUT_S,
            predicate=indexed,
            interval_s=2.0,
        )
        summary["stages"]["index"] = _stage(current, "index")
        if _attempt(current).get("cleanup_status") != "verified":
            raise SmokeFailure("index succeeded without verified cleanup")

        search = _require_json(
            await client.get("/search", params={"q": QUESTION, "limit": 5}),
            step="GET /search",
        )
        results = search.get("results")
        if not isinstance(results, list) or not results:
            raise SmokeFailure("search returned no results after indexing")
        if not any(item.get("audio_id") == audio_id for item in results if isinstance(item, dict)):
            raise SmokeFailure("search did not return the smoke class")
        summary["search_results"] = len(results)

        events: list[SseEvent] = []
        async with client.stream(
            "POST",
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": QUESTION,
                "client_message_id": f"e2e-{uuid.uuid4().hex}",
            },
            headers=headers,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise SmokeFailure(f"POST chat: HTTP {response.status_code} {body[:300]}")
            async for event in _iter_sse(response.aiter_lines()):
                events.append(event)
                if event.name in {"done", "error"}:
                    break

        names = [event.name for event in events]
        if not names or names[0] != "snapshot":
            raise SmokeFailure(f"chat SSE did not start with snapshot: {names[:4]}")
        if "delta" not in names:
            raise SmokeFailure("chat SSE emitted no delta")
        terminal = [event for event in events if event.name in {"done", "error"}]
        if not terminal:
            raise SmokeFailure("chat SSE has no terminal event")
        if terminal[-1].name == "error":
            raise SmokeFailure(f"chat SSE error: {terminal[-1].data}")
        if terminal[-1].data.get("status") != "completed":
            raise SmokeFailure(f"chat did not complete: {terminal[-1].data}")
        citations = [event for event in events if event.name == "citation"]
        if not citations:
            raise SmokeFailure("chat SSE emitted no citation")
        citation = citations[0].data
        if citation.get("audio_id") != audio_id or citation.get("index") != 1:
            raise SmokeFailure(f"citation does not reference the smoke class: {citation}")
        summary["citation_index"] = citation["index"]
        summary["chat_events"] = names

    async with SessionFactory() as db:
        from sqlalchemy import text

        row = (
            await db.execute(
                text(
                    "SELECT sha256, original_bytes FROM audios "
                    "WHERE id = :audio_id"
                ),
                {"audio_id": uuid.UUID(audio_id)},
            )
        ).one()
        if row.sha256 != digest or row.original_bytes != len(wav):
            raise SmokeFailure(
                "durable audio row does not hold the expected hash/size metadata"
            )
        # G1 at the schema level: the class row cannot store audio bytes.
        bytea_columns = (
            await db.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'audios' "
                    "AND data_type = 'bytea'"
                )
            )
        ).scalar_one()
        if bytea_columns:
            raise SmokeFailure("the audios table has a binary column for audio")
        summary["audio_metadata_only"] = True

    summary["ok"] = True
    return summary


def main() -> int:
    started = time.monotonic()
    try:
        summary = asyncio.run(_run())
    except SmokeFailure as exc:
        print(f"E-S2-JF-06 FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - one typed line, never a traceback with content
        print(f"E-S2-JF-06 FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    summary["duration_s"] = round(time.monotonic() - started, 1)
    print(json.dumps({"test": "E-S2-JF-06", **summary}, ensure_ascii=False))
    print("E-S2-JF-06 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
