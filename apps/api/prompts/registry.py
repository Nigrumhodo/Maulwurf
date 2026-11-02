"""Versioned extraction prompts (L3.1).

`PROMPT_VERSION` is the identifier stored on an `analyze` run. The SHA-256 covers the
prompt files only, so a wording change is visible to the regression suite and class text
never enters the digest. Rendering fills the user template; it does not call a model.
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

_DIR = Path(__file__).resolve().parent / PROMPT_VERSION
SYSTEM_PATH = _DIR / "system_extraction.txt"
USER_PATH = _DIR / "user_extraction.txt"

# A segment that contains the closing fence could end the block early and append a fake id.
_SEGMENT_CLOSE = re.compile(r"</segment", re.IGNORECASE)

_USER_FIELDS = ("subject", "teacher", "class_date", "class_timezone", "segments")


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
    return Template(prompts.user_template).substitute(
        subject=subject,
        teacher=teacher,
        class_date=class_date.isoformat(),
        class_timezone=class_timezone,
        segments=blocks,
    )


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptRegistryError(
            "prompt_missing",
            f"the extraction prompt file is missing: {path.name}",
        ) from exc
    if not text:
        raise PromptRegistryError(
            "prompt_missing",
            f"the extraction prompt file is empty: {path.name}",
        )
    return text


def _fence(segment_id: UUID, text: str) -> str:
    safe = _SEGMENT_CLOSE.sub("</ segment", text)
    return f'<segment id="{segment_id}">\n{safe}\n</segment>'
