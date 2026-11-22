"""The stranded sweep fences the row before it destroys the directory."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from maulwurf_ingest import reconciler


async def test_sweep_fences_before_it_destroys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    attempt_id, user_id = uuid.uuid4(), uuid.uuid4()
    order: list[str] = []
    seen: dict[str, int] = {}

    async def fake_fetch(
        _pool: object, _user: object, _query: str, *_args: object,
    ) -> list[dict[str, object]]:
        order.append("select")
        return [{"id": attempt_id, "user_id": user_id, "fencing_token": 7}]

    async def fake_fetchrow(
        _pool: object, _user: object, query: str, *args: object,
    ) -> dict[str, int] | None:
        order.append("fence")
        assert "fencing_token = fencing_token + 1" in query
        assert args[4] == 7
        return {"fencing_token": 8}

    def fake_dirs(_root: Path, aid: uuid.UUID) -> list[Path]:
        order.append("list")
        assert aid == attempt_id
        return [tmp_path / "work"]

    async def fake_destroy(
        _pool: object, aid: uuid.UUID, uid: uuid.UUID, token: int, workdirs: list[Path],
    ) -> None:
        order.append("destroy")
        assert (aid, uid, token) == (attempt_id, user_id, 8)
        assert workdirs
        seen["token"] = token

    monkeypatch.setattr("maulwurf_ingest.reconciler.rls.fetch", fake_fetch)
    monkeypatch.setattr("maulwurf_ingest.reconciler.rls.fetchrow", fake_fetchrow)
    monkeypatch.setattr("maulwurf_ingest.reconciler.local_workdirs", fake_dirs)
    monkeypatch.setattr("maulwurf_ingest.reconciler._destroy_stranded", fake_destroy)

    ended = await reconciler.sweep_stranded(object(), tmp_dir=str(tmp_path))

    assert ended == (attempt_id,)
    assert order == ["select", "fence", "list", "destroy"]
    assert seen["token"] == 8


async def test_a_lost_fence_does_not_destroy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    attempt_id, user_id = uuid.uuid4(), uuid.uuid4()
    destroyed = False

    async def fake_fetch(
        _pool: object, _user: object, _query: str, *_args: object,
    ) -> list[dict[str, object]]:
        return [{"id": attempt_id, "user_id": user_id, "fencing_token": 3}]

    async def fake_fetchrow(
        _pool: object, _user: object, _query: str, *_args: object,
    ) -> None:
        return None

    async def fake_destroy(*_args: object, **_kwargs: object) -> None:
        nonlocal destroyed
        destroyed = True

    monkeypatch.setattr("maulwurf_ingest.reconciler.rls.fetch", fake_fetch)
    monkeypatch.setattr("maulwurf_ingest.reconciler.rls.fetchrow", fake_fetchrow)
    monkeypatch.setattr("maulwurf_ingest.reconciler._destroy_stranded", fake_destroy)

    ended = await reconciler.sweep_stranded(object(), tmp_dir=str(tmp_path))

    assert ended == ()
    assert destroyed is False
