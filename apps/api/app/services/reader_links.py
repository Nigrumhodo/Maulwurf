"""Stable deep links into the authenticated transcript reader (M7/D3.4).

The reader is a same-origin SPA route: authorization comes from the session cookie, so the
URL carries only the class and segment identifiers. Callers must never add tokens, audio
locations or signed parameters; a lost link is reopened after logging in, not by embedding
a credential.
"""

import uuid


def class_reader_path(audio_id: uuid.UUID, segment_id: uuid.UUID | None = None) -> str:
    """Relative reader path for a class, anchored to a segment when one is known."""
    base = f"/biblioteca/{audio_id}"
    if segment_id is None:
        return base
    return f"{base}?segment={segment_id}"
