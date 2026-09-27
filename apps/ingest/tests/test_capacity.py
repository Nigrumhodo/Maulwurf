"""Unit math for S1.B2. Does not call ffmpeg or approve 200 MiB / 3 h."""

from __future__ import annotations

import pytest

from maulwurf_ingest.capacity import (
    APPROVED_3H,
    APPROVED_200_MIB,
    FRAGMENT_1S_16K_S16_BYTES,
    PCM_3H_16K_S16_BYTES,
    PROVISIONAL_MEM_LIMIT_BYTES,
    PROVISIONAL_SLOTS,
    PROVISIONAL_TMPFS_BYTES,
    PROVISIONAL_UPLOAD_BYTES,
    fits_limit,
    instance_bytes,
    slot_bytes,
)


def test_slot_is_the_sum_and_excludes_the_three_hour_pcm() -> None:
    slot = slot_bytes(
        original_bytes=1000,
        active_fragments=2,
        fragment_bytes=FRAGMENT_1S_16K_S16_BYTES,
        ffmpeg_buffer_bytes=10,
        sdk_buffer_bytes=20,
        margin_bytes=30,
    )
    assert FRAGMENT_1S_16K_S16_BYTES == 32000
    assert PCM_3H_16K_S16_BYTES == 345_600_000
    assert slot == 1000 + 2 * 32000 + 10 + 20 + 30
    assert slot < PCM_3H_16K_S16_BYTES


def test_negative_term_is_rejected() -> None:
    with pytest.raises(ValueError, match="margin_bytes"):
        slot_bytes(
            original_bytes=1,
            active_fragments=1,
            fragment_bytes=1,
            ffmpeg_buffer_bytes=1,
            sdk_buffer_bytes=1,
            margin_bytes=-1,
        )


def test_two_provisional_slots_do_not_fit_tmpfs_and_nothing_is_approved() -> None:
    one = slot_bytes(
        original_bytes=PROVISIONAL_UPLOAD_BYTES,
        active_fragments=1,
        fragment_bytes=FRAGMENT_1S_16K_S16_BYTES,
        ffmpeg_buffer_bytes=0,
        sdk_buffer_bytes=0,
        margin_bytes=0,
    )
    both = instance_bytes(one, PROVISIONAL_SLOTS)
    assert PROVISIONAL_UPLOAD_BYTES == 200 * 1024 * 1024
    assert PROVISIONAL_TMPFS_BYTES == 256 * 1024 * 1024
    assert fits_limit(one, PROVISIONAL_TMPFS_BYTES) is True
    assert fits_limit(both, PROVISIONAL_TMPFS_BYTES) is False
    assert fits_limit(both, PROVISIONAL_MEM_LIMIT_BYTES) is True
    assert APPROVED_200_MIB is False
    assert APPROVED_3H is False
