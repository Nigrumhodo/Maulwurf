"""Structured JSON logs with correlation ids and a redaction guard (J2.5).

The formatter emits what the code logs plus the ids bound to the context (request_id,
audio_id, attempt_id, run_id). RedactionFilter keeps secrets and transcripts out of the
output. The formatter does not rewrite the message, so without the filter the leak is
visible again; the negative-control tests rely on that.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

CORRELATION_FIELDS: Final = ("request_id", "audio_id", "attempt_id", "run_id")
REDACTED: Final = "[REDACTED]"
_OWNED_MARK: Final = "_maulwurf_json_handler"

_CONTEXT: Final[dict[str, ContextVar[str | None]]] = {
    name: ContextVar(f"log_{name}", default=None) for name in CORRELATION_FIELDS
}

# Key substrings that must never be emitted. `text` matches exactly because it occurs inside
# `context`. Identifier or counter suffixes are safe even when they contain a sensitive
# substring (`transcript_version`, `token_count`).
_SENSITIVE_SUBSTRINGS: Final = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "transcript",
    "prompt",
)
_SAFE_SUFFIXES: Final = ("_id", "_ids", "_version", "_count", "_status", "_kid", "_bytes")

# `key=value` or `key: value` inside message text (the codebase logs with `%s=%s`).
_KEY_VALUE: Final = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)(?P<sep>\s*[=:]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_CREDENTIALS: Final = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/@]+@")
_BEARER: Final = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]+")


def is_sensitive_key(key: str) -> bool:
    """True when a field or key name must never reach the log output."""
    lowered = key.lower()
    if lowered == "text":
        return True
    if lowered.endswith(_SAFE_SUFFIXES):
        return False
    return any(part in lowered for part in _SENSITIVE_SUBSTRINGS)


def scrub_text(text: str) -> str:
    """Mask sensitive `key=value` pairs, URL credentials and bearer tokens in free text."""
    text = _URL_CREDENTIALS.sub(rf"\1{REDACTED}@", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)

    def _mask_pair(match: re.Match[str]) -> str:
        if is_sensitive_key(match.group("key")):
            return f"{match.group('key')}{match.group('sep')}{REDACTED}"
        return match.group(0)

    return _KEY_VALUE.sub(_mask_pair, text)


def _scrub_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: REDACTED if is_sensitive_key(key) else value for key, value in fields.items()
    }


@contextmanager
def correlation(**ids: str | None) -> Iterator[None]:
    """Bind correlation ids to the current context; they are restored on exit."""
    tokens: list[tuple[ContextVar[str | None], Any]] = []
    for name, value in ids.items():
        if name not in _CONTEXT:
            raise KeyError(f"campo de correlación desconocido: {name}")
        tokens.append((_CONTEXT[name], _CONTEXT[name].set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_ids() -> dict[str, str]:
    """Correlation ids bound right now, without the empty ones."""
    return {name: value for name, var in _CONTEXT.items() if (value := var.get())}


class RedactionFilter(logging.Filter):
    """Mask the message, extra fields and traceback before any handler runs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub_text(record.getMessage())
        record.args = ()
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            record.fields = _scrub_fields(fields)
        self._scrub_traceback(record)
        return True

    @staticmethod
    def _scrub_traceback(record: logging.LogRecord) -> None:
        if record.exc_text is None and record.exc_info and record.exc_info[0] is not None:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text is not None:
            record.exc_text = scrub_text(record.exc_text)
            record.exc_info = None


class JsonFormatter(logging.Formatter):
    """One JSON line per record. It only serializes; redaction is the filter's job."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(current_ids())
        for name in CORRELATION_FIELDS:
            explicit = getattr(record, name, None)
            if explicit:
                payload[name] = explicit
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload.update({k: v for k, v in fields.items() if k not in payload})
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Add one redacting JSON handler to the root logger, once per process.

    Existing handlers are kept: pytest (`caplog`) and others use them, and a repeated
    startup must not duplicate the output.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, _OWNED_MARK, False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    setattr(handler, _OWNED_MARK, True)
    root.addHandler(handler)
