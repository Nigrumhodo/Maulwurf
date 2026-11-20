#!/usr/bin/env python3
"""Deterministic OpenAI-compatible double for the E-S2-JF-06 smoke (J2.6).

It is not a provider contract: it has no key, no model, no network and no retention. Its
only purpose is that the compose smoke can run the real embeddings + chat adapters without
a paid provider. The vectors are a deterministic bag-of-words projection of the input, so
the smoke's question and transcript share dimensions and the dense branch returns the
class chunk. The chat answer is a fixed synthetic sentence that cites ``[S1]``.

Run with the standard library only: ``python provider_double.py`` (port from ``PORT``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DIMENSION = 1536
MODEL = "maulwurf-e2e-synthetic-v1"
ANSWER = "La fotosíntesis convierte la luz del sol en energía química [S1]."
# D3.8: opt-in deterministic tool-call turn for the browser/chat demo. Unset keeps the
# existing smoke answer; the trigger is matched only in the last user message.
TOOL_TRIGGER = os.environ.get("PROVIDER_DOUBLE_TOOL_TRIGGER", "").strip()
TOOL_CALL = json.dumps(
    {
        "tool": "propose_task",
        "arguments": {
            "titulo": "Repasar la fotosíntesis",
            "tipo": "tarea",
            "timezone": "America/Bogota",
            "mencion": {"kind": "all_day", "due_date": "2026-11-20"},
            "detalle": None,
            "sources": [],
        },
    },
    ensure_ascii=False,
)
_WORD = re.compile(r"\w+", re.UNICODE)


def embedding(text: str) -> list[float]:
    """Deterministic unit vector: one bucket per token occurrence, then L2 normalisation."""
    vector = [0.0] * DIMENSION
    for token in _WORD.findall(text.casefold()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % DIMENSION
        vector[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _chunk(piece: str, model: str) -> dict[str, Any]:
    return {
        "id": "e2e",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
    }


def _answer_pieces(answer: str, size: int = 7) -> list[str]:
    return [answer[start : start + size] for start in range(0, len(answer), size)]


def chat_body(answer: str, model: str) -> bytes:
    """Whole SSE answer; small enough that Content-Length keeps the fake simple."""
    frames = [f"data: {json.dumps(_chunk(piece, model), ensure_ascii=False)}\n\n"
              for piece in _answer_pieces(answer)]
    frames.append(
        "data: "
        + json.dumps(
            {
                "id": "e2e",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode("utf-8")


def chat_answer_for(messages: object, *, trigger: str = TOOL_TRIGGER) -> str:
    """Choose the deterministic answer for one chat request.

    The tool turn is opt-in. Without a trigger the existing smoke answer is returned; a
    second call after the `Tool result` message also returns it, so the backend's bounded
    tool loop terminates instead of repeating the call.
    """
    if not trigger or not isinstance(messages, list):
        return ANSWER
    user_texts = [
        item.get("content")
        for item in messages
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    if any(
        isinstance(text, str) and text.startswith("Tool result:") for text in user_texts
    ):
        return ANSWER
    last = user_texts[-1] if user_texts else ""
    return TOOL_CALL if isinstance(last, str) and trigger in last else ANSWER


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Paths and status only: the adapter never sends audio, but the question text must
        # not end up in the smoke logs either.
        sys.stderr.write("provider-double %s\n" % (fmt % args))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/healthz":
            _json_response(self, 200, {"status": "ok"})
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length.isdigit() else 0
        try:
            body: object = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            _json_response(self, 400, {"error": "invalid_json"})
            return
        if not isinstance(body, dict):
            _json_response(self, 400, {"error": "invalid_body"})
            return
        if self.path.endswith("/embeddings"):
            self._embeddings(body)
            return
        if self.path.endswith("/chat/completions"):
            self._chat(body)
            return
        _json_response(self, 404, {"error": "not_found"})

    def _embeddings(self, body: dict[str, Any]) -> None:
        raw = body.get("input")
        texts: object = [raw] if isinstance(raw, str) else raw
        if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
            _json_response(self, 400, {"error": "invalid_input"})
            return
        data = [
            {"object": "embedding", "index": index, "embedding": embedding(text)}
            for index, text in enumerate(texts)
        ]
        _json_response(
            self,
            200,
            {
                "object": "list",
                "model": body.get("model", MODEL),
                "data": data,
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    def _chat(self, body: dict[str, Any]) -> None:
        if body.get("stream") is not True:
            _json_response(self, 400, {"error": "stream_required"})
            return
        model = body.get("model", MODEL)
        answer = chat_answer_for(body.get("messages"))
        payload = chat_body(answer, model if isinstance(model, str) else MODEL)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - compose network only
    server.daemon_threads = True
    sys.stderr.write(f"provider-double listening on {port}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
