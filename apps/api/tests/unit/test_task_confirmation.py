"""A3.4 unit decisions: Calendar readiness, target normalization and dedupe keys.

The I-S3-AN-03 transaction cases run against PostgreSQL in
`tests/integration/test_task_endpoints.py`; these are the pure pieces behind them.
"""

import uuid

import pytest

from app.models import GoogleCredential
from app.services.oauth_google import CALENDAR_SCOPES
from app.services.tasks import (
    _calendar_ready,
    _calendar_target,
    _confirmation_dedupe_key,
)

SHORT_CALENDAR_SCOPES = [
    scope.removeprefix("https://www.googleapis.com/auth/") for scope in CALENDAR_SCOPES
]


def _credential(
    *, status: str = "connected", scopes: list[str] | None = None
) -> GoogleCredential:
    return GoogleCredential(
        user_id=uuid.uuid4(),
        access_token_enc=b"nonce+ciphertext",
        key_version=1,
        scopes=list(CALENDAR_SCOPES) if scopes is None else scopes,
        status=status,
    )


def test_a_blank_target_is_no_target() -> None:
    assert _calendar_target(None) is None
    assert _calendar_target("") is None
    assert _calendar_target(" \t\n") is None


def test_the_target_is_trimmed() -> None:
    assert _calendar_target("  maulwurf@group.calendar.example  ") == (
        "maulwurf@group.calendar.example"
    )


def test_calendar_is_not_ready_without_a_credential() -> None:
    assert not _calendar_ready(None, "maulwurf@group.calendar.example")


def test_calendar_is_not_ready_without_a_write_target() -> None:
    assert not _calendar_ready(_credential(), None)
    assert not _calendar_ready(_credential(), "   ")


def test_calendar_is_not_ready_when_the_credential_is_disconnected() -> None:
    assert not _calendar_ready(
        _credential(status="disconnected"), "maulwurf@group.calendar.example"
    )


def test_calendar_is_not_ready_without_the_three_calendar_scopes() -> None:
    assert not _calendar_ready(_credential(scopes=[]), "maulwurf@group.calendar.example")
    assert not _calendar_ready(
        _credential(scopes=["openid"]), "maulwurf@group.calendar.example"
    )
    assert not _calendar_ready(
        _credential(scopes=list(CALENDAR_SCOPES[:2])),
        "maulwurf@group.calendar.example",
    )


@pytest.mark.parametrize("scopes", [list(CALENDAR_SCOPES), SHORT_CALENDAR_SCOPES])
def test_calendar_is_ready_with_a_connected_full_grant(scopes: list[str]) -> None:
    assert _calendar_ready(
        _credential(scopes=scopes), "maulwurf@group.calendar.example"
    )


def test_the_confirmation_dedupe_key_is_version_specific_and_stable() -> None:
    key = _confirmation_dedupe_key(version=2, due_version=1, cycle=0)

    assert key == "confirm:2:1:0"
    assert key != _confirmation_dedupe_key(version=3, due_version=1, cycle=0)
    assert key != _confirmation_dedupe_key(version=2, due_version=2, cycle=0)
    assert key != _confirmation_dedupe_key(version=2, due_version=1, cycle=1)
