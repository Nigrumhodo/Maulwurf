"""E-S2-SG-11 reduced: a short synthetic class in RAM, not the 1 h Caddy demo."""

from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import tmpfs_dir  # noqa: E402

from maulwurf_ingest.audio.synthetic import DEFAULT_PHRASE_ES
from maulwurf_ingest.synthetic_class import run_synthetic_class

_ = (tmpfs_dir,)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible"),
    pytest.mark.skipif(
        shutil.which("espeak-ng") is None and shutil.which("espeak") is None,
        reason="espeak-ng no disponible",
    ),
]


async def test_short_synthetic_class_commits_known_text_without_riva(
    pool: asyncpg.Pool, tmpfs_dir: Path,
) -> None:
    report = await run_synthetic_class(pool, tmp_dir=str(tmpfs_dir))

    assert report.transcript_text == DEFAULT_PHRASE_ES
    assert report.passed
    assert report.published_limits_validated is False
    assert report.published_max_upload_bytes is None
    assert report.published_timestamp_precision is None
    assert report.timestamp_precision == "none"
    assert report.segment_times_null
    assert report.riva_constructed is False
    assert report.audio_row_has_no_bytes
    assert report.workdir_absent
    assert list(tmpfs_dir.iterdir()) == []
