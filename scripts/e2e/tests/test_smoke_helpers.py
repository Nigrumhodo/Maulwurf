"""Fast checks for the E-S2-JF-06 helpers (J2.6).

They run with the standard library only, before compose starts, so a broken fixture or SSE
parser fails in seconds instead of after a 10-minute stack. The pipeline test itself is
`scripts/e2e/smoke_pipeline.py`; this file never talks to a service, a provider or a file
outside the repository.

Run: python3 -m unittest discover -s scripts/e2e/tests -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import sys
import unittest
import wave
from collections.abc import AsyncIterator, Iterable
from io import BytesIO
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load("e2e_smoke_pipeline", E2E_DIR / "smoke_pipeline.py")
provider = _load("e2e_provider_double", E2E_DIR / "e2e_stubs" / "provider_double.py")


class SyntheticAudioTest(unittest.TestCase):
    def test_wav_is_mono_16k_pcm_s16_and_lives_in_ram(self) -> None:
        data = smoke.synthetic_wav(seconds=0.5)
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertEqual(data[8:12], b"WAVE")
        with wave.open(BytesIO(data), "rb") as reader:
            self.assertEqual(reader.getnchannels(), 1)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertEqual(reader.getframerate(), 16000)
            self.assertAlmostEqual(
                reader.getnframes() / reader.getframerate(), 0.5, places=3
            )

    def test_two_calls_are_deterministic(self) -> None:
        self.assertEqual(smoke.synthetic_wav(seconds=0.2), smoke.synthetic_wav(seconds=0.2))


class SseParserTest(unittest.TestCase):
    def test_named_events_keep_order_payload_and_id(self) -> None:
        lines = [
            "event: snapshot",
            'id: 0',
            'data: {"message_id":"m1","status":"streaming"}',
            "",
            ": ping",
            "",
            "event: delta",
            "id: 12",
            'data: {"text":"hola"}',
            "",
            "event: done",
            "id: 13",
            'data: {"status":"completed"}',
            "",
        ]
        events = smoke.parse_sse(lines)
        self.assertEqual([event.name for event in events], ["snapshot", "delta", "done"])
        self.assertEqual(events[0].event_id, 0)
        self.assertEqual(events[1].data, {"text": "hola"})
        self.assertEqual(events[2].data["status"], "completed")

    def test_multiline_data_is_joined(self) -> None:
        events = smoke.parse_sse(
            ["event: delta", 'data: {"a":', 'data: 1}', ""]
        )
        self.assertEqual(events[0].data, {"a": 1})


async def _aiter(lines: Iterable[str]) -> AsyncIterator[str]:
    for line in lines:
        yield line


class AsyncSseParserTest(unittest.TestCase):
    """The production path (`_iter_sse`) must honour the same contract as `parse_sse`."""

    LINES = [
        "event: snapshot",
        "id: 0",
        'data: {"message_id":"m1","status":"streaming"}',
        "",
        ": keep-alive",
        "",
        "event: delta",
        "id: 12",
        'data: {"text":"hola",',
        'data: "index":1}',
        "",
        "event: ping",
        "",
        "event: done",
        "id: 13",
        'data: {"status":"completed"}',
    ]

    async def _collect(self) -> list[smoke.SseEvent]:
        return [event async for event in smoke._iter_sse(_aiter(self.LINES))]

    def test_async_parser_matches_the_sync_contract(self) -> None:
        expected = smoke.parse_sse(self.LINES)
        actual = asyncio.run(self._collect())

        self.assertEqual(actual, expected)
        self.assertEqual([event.name for event in actual], ["snapshot", "delta", "done"])
        self.assertEqual(actual[0].event_id, 0)
        self.assertEqual(actual[1].data, {"text": "hola", "index": 1})
        self.assertEqual(actual[2].data["status"], "completed")

    def test_async_parser_flushes_a_final_event_without_a_blank_line(self) -> None:
        actual = asyncio.run(
            self._collect_lines(["event: done", 'data: {"status":"completed"}'])
        )
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0].name, "done")
        self.assertEqual(actual[0].data, {"status": "completed"})

    async def _collect_lines(self, lines: Iterable[str]) -> list[smoke.SseEvent]:
        return [event async for event in smoke._iter_sse(_aiter(lines))]


class ComposeOverrideGuardTest(unittest.TestCase):
    """The fixed credentials in the E2E override must never be reachable by accident."""

    def test_override_requires_an_explicit_ephemeral_opt_in(self) -> None:
        root = E2E_DIR.parents[1]
        override = (root / "infra" / "docker-compose.e2e.yml").read_text(encoding="utf-8")

        required = override.count("E2E_ALLOW_EPHEMERAL_CREDENTIALS:?")
        self.assertGreaterEqual(required, 2)  # api/worker/scheduler and ingest
        for key in (
            "MAULWURF_SECRET_KEY",
            "MAULWURF_ENCRYPTION_KEY",
            "MAULWURF_INGEST_CREDENTIAL_SECRET",
            "MAULWURF_D3_TEXT_CLOSED_MODELS",
        ):
            self.assertIn(key, override)
        self.assertIn("name: maulwurf-e2e", override)
        self.assertIn("group_add:", override)
        self.assertIn("E2E_TLS_GID", override)

    def test_ci_acknowledges_the_ephemeral_override(self) -> None:
        root = E2E_DIR.parents[1]
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('E2E_ALLOW_EPHEMERAL_CREDENTIALS: "1"', workflow)
        self.assertIn("E2E_TLS_GID=$(id -g)", workflow)


class TlsMaterialTest(unittest.TestCase):
    def test_server_key_is_never_world_readable(self) -> None:
        script = (E2E_DIR / "gen_riva_tls.sh").read_text(encoding="utf-8")
        self.assertIn('chmod 640 "$dir/server.key"', script)
        self.assertNotIn('chmod 644 "$dir/ca.pem" "$dir/server.pem" "$dir/server.key"', script)


class ProviderDoubleTest(unittest.TestCase):
    def test_embedding_is_a_deterministic_unit_vector(self) -> None:
        first = provider.embedding("La fotosíntesis convierte la luz del sol")
        second = provider.embedding("La fotosíntesis convierte la luz del sol")
        self.assertEqual(first, second)
        self.assertEqual(len(first), provider.DIMENSION)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in first)), 1.0, places=9)

    def test_related_question_and_transcript_share_dimensions(self) -> None:
        question = provider.embedding("¿Qué convierte la luz del sol en energía química?")
        transcript = provider.embedding(provider.ANSWER)
        cosine = sum(a * b for a, b in zip(question, transcript, strict=True))
        self.assertGreater(cosine, 0.2)

    def test_chat_body_is_openai_sse_with_one_terminal_frame(self) -> None:
        body = provider.chat_body(provider.ANSWER, provider.MODEL).decode("utf-8")
        frames = [line[len("data: "):] for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1], "[DONE]")
        pieces = []
        for frame in frames[:-1]:
            payload = json.loads(frame)
            content = payload["choices"][0]["delta"].get("content")
            if content:
                pieces.append(content)
        self.assertEqual("".join(pieces), provider.ANSWER)
        self.assertIn("[S1]", provider.ANSWER)


if __name__ == "__main__":
    unittest.main()
