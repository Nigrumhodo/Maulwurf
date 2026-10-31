"""CLI safety rules for the S3.1 backfill: tenant required and dry-run by default."""

import uuid

import pytest

from tools.backfill_analysis import build_parser


def test_parser_requires_the_tenant_and_defaults_to_dry_run() -> None:
    user_id = uuid.uuid4()

    args = build_parser().parse_args(["--user-id", str(user_id)])

    assert args.user_id == user_id
    assert args.apply is False
    assert args.limit == 500
    assert args.audio_ids is None


def test_parser_accepts_repeated_classes_and_dates() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    args = build_parser().parse_args(
        [
            "--user-id",
            str(uuid.uuid4()),
            "--audio-id",
            str(first),
            "--audio-id",
            str(second),
            "--from-date",
            "2026-09-01",
            "--to-date",
            "2026-09-30",
        ]
    )

    assert args.audio_ids == [first, second]
    assert args.class_date_from.isoformat() == "2026-09-01"
    assert args.class_date_to.isoformat() == "2026-09-30"


@pytest.mark.parametrize("limit", ["0", "2001", "abc"])
def test_parser_rejects_unbounded_limits(limit: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--user-id", str(uuid.uuid4()), "--limit", limit])
