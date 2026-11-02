"""Versioned extraction and summary prompts (L3.1, L3.8).

`PROMPT_VERSION` is the identifier stored on an `analyze` run. `SUMMARY_PROMPT_VERSION`
is the summary shape stored in the analysis config key. Each SHA-256 covers its own prompt
files only, so a wording change is visible to the regression suite and class text never
enters the digest. Rendering fills the user template; it does not call a model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from string import Template
from uuid import UUID

PROMPT_VERSION = "v1"
SUMMARY_PROMPT_VERSION = "v1"

_DIR = Path(__file__).resolve().parent / PROMPT_VERSION
SYSTEM_PATH = _DIR / "system_extraction.txt"
USER_PATH = _DIR / "user_extraction.txt"
SYSTEM_SUMMARY_PATH = _DIR / "system_summary.txt"
USER_SUMMARY_PATH = _DIR / "user_summary.txt"

# A segment that contains the closing fence could end the block early and append a fake id.
_SEGMENT_CLOSE = re.compile(r"</segment", re.IGNORECASE)
# The same fence rule for a window note that tries to close the summary block early.
_WINDOW_CLOSE = re.compile(r"</window", re.IGNORECASE)

_USER_FIELDS = ("subject", "teacher", "class_date", "class_timezone", "segments")
_SUMMARY_FIELDS = ("subject", "teacher", "class_date", "class_timezone", "windows")
# Subject and teacher are user-supplied. Flatten and bound them so a name cannot add a
# line of instructions or close a tag. The class date and timezone are already closed.
_METADATA_LIMIT = 200


class PromptRegistryError(Exception):
    """A versioned prompt file is missing or does not match the template contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ExtractionPrompts:
    """The v1 extraction prompts and the digest of their file bytes."""

    version: str
    system: str
    user_template: str
    digest: str


def load_extraction_prompts() -> ExtractionPrompts:
    """Read the v1 extraction prompts. A missing file is a configuration failure."""
    system = _read(SYSTEM_PATH)
    user = _read(USER_PATH)
    missing = [name for name in _USER_FIELDS if f"${name}" not in user]
    if missing:
        raise PromptRegistryError(
            "prompt_template_invalid",
            "the user extraction prompt is missing a required placeholder",
        )
    digest = hashlib.sha256(SYSTEM_PATH.read_bytes() + b"\n" + USER_PATH.read_bytes()).hexdigest()
    return ExtractionPrompts(
        version=PROMPT_VERSION,
        system=system,
        user_template=user,
        digest=digest,
    )


def render_user_prompt(
    *,
    subject: str,
    teacher: str,
    class_date: date,
    class_timezone: str,
    segments: tuple[tuple[UUID, str], ...],
) -> str:
    """Fill the user template. Segment text is fenced and cannot close the fence."""
    prompts = load_extraction_prompts()
    blocks = "\n".join(_fence(segment_id, text) for segment_id, text in segments)
    return _substitute(
        prompts.user_template,
        subject=subject,
        teacher=teacher,
        class_date=class_date,
        class_timezone=class_timezone,
        segments=blocks,
    )


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptRegistryError(
            "prompt_missing",
            f"the prompt file is missing: {path.name}",
        ) from exc
    if not text:
        raise PromptRegistryError(
            "prompt_missing",
            f"the prompt file is empty: {path.name}",
        )
    return text


def render_window_prompt(
    *,
    subject: str,
    teacher: str,
    class_date: date,
    class_timezone: str,
    segments: tuple[tuple[UUID, str, int], ...],
) -> str:
    """Fill the user template with one window's clipped segments.

    ``local_start`` is the code-point offset of the clip inside the full segment, so a
    citation can still use segment-local offsets (the §M4 contract) without seeing text
    that this window does not contain.
    """
    prompts = load_extraction_prompts()
    blocks = "\n".join(
        _fence(segment_id, text, local_start=local_start)
        for segment_id, text, local_start in segments
    )
    return _substitute(
        prompts.user_template,
        subject=subject,
        teacher=teacher,
        class_date=class_date,
        class_timezone=class_timezone,
        segments=blocks,
    )


def _substitute(
    template: str,
    *,
    subject: str,
    teacher: str,
    class_date: date,
    class_timezone: str,
    segments: str,
) -> str:
    return Template(template).substitute(
        subject=_metadata(subject),
        teacher=_metadata(teacher),
        class_date=class_date.isoformat(),
        class_timezone=class_timezone,
        segments=segments,
    )


def _metadata(value: str) -> str:
    """One line, bounded, with no angle brackets that could close the prompt frame."""
    flat = " ".join(value.split())[:_METADATA_LIMIT]
    return flat.replace("<", "‹").replace(">", "›")


def _fence(segment_id: UUID, text: str, *, local_start: int | None = None) -> str:
    safe = _SEGMENT_CLOSE.sub("</ segment", text)
    extra = f' local_start="{local_start}"' if local_start is not None else ""
    return f'<segment id="{segment_id}"{extra}>\n{safe}\n</segment>'


@dataclass(frozen=True)
class SummaryPrompts:
    """The v1 summary prompts and the digest of their file bytes."""

    version: str
    system: str
    user_template: str
    digest: str


def load_summary_prompts() -> SummaryPrompts:
    """Read the v1 summary prompts. A missing file is a configuration failure."""
    system = _read(SYSTEM_SUMMARY_PATH)
    user = _read(USER_SUMMARY_PATH)
    missing = [name for name in _SUMMARY_FIELDS if f"${name}" not in user]
    if missing:
        raise PromptRegistryError(
            "prompt_template_invalid",
            "the user summary prompt is missing a required placeholder",
        )
    digest = hashlib.sha256(
        SYSTEM_SUMMARY_PATH.read_bytes() + b"\n" + USER_SUMMARY_PATH.read_bytes()
    ).hexdigest()
    return SummaryPrompts(
        version=SUMMARY_PROMPT_VERSION,
        system=system,
        user_template=user,
        digest=digest,
    )


def render_summary_prompt(
    *,
    subject: str,
    teacher: str,
    class_date: date,
    class_timezone: str,
    windows: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    """Fill the summary template with fenced window notes, not the raw transcript.

    Each note is the extraction window's own summary and topics. The fence cannot be
    closed from inside a note, so a window cannot append a second block of instructions.
    """
    prompts = load_summary_prompts()
    blocks = "\n".join(_window_fence(text, topics) for text, topics in windows)
    return Template(prompts.user_template).substitute(
        subject=_metadata(subject),
        teacher=_metadata(teacher),
        class_date=class_date.isoformat(),
        class_timezone=class_timezone,
        windows=blocks,
    )


def _window_fence(text: str, topics: tuple[str, ...]) -> str:
    safe_text = _WINDOW_CLOSE.sub("</ window", text)
    safe_topics = ", ".join(_one_line(topic) for topic in topics)
    return f"<window>\n{safe_text}\nTopics: {safe_topics}\n</window>"


def _one_line(value: str) -> str:
    flat = " ".join(value.split())
    return _WINDOW_CLOSE.sub("</ window", flat)
