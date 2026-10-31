"""Fast checks for the E-S2-JF-06 helpers (J2.6).

They run with the standard library only, before compose starts, so a broken fixture or SSE
parser fails in seconds instead of after a 10-minute stack. The pipeline test itself is
`scripts/e2e/smoke_pipeline.py`; this file never talks to a service, a provider or a file
outside the repository.

Run: python3 -m unittest discover -s scripts/e2e/tests -v
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import unittest
import wave
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
