"""The mount contract accepts only the JSON `docker compose config` emits."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_compose_ingest_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_compose_ingest_env", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compose = _load()


def _model(**overrides: object) -> dict:
    service: dict = {
        "read_only": True,
        "tmpfs": ["/work/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777"],
        "mem_limit": "1073741824",
        "memswap_limit": "1073741824",
        "ulimits": {"core": {}, "nofile": {"soft": 1024, "hard": 1024}},
        "environment": {
            "TMPDIR": "/work/tmp",
            "INGEST_MAX_UPLOAD_BYTES": "209715200",
        },
    }
    service.update(overrides)
    return {"services": {"ingest": service}}


class MountContractTest(unittest.TestCase):
    def _failures(self, **overrides: object) -> list[str]:
        report = compose.Report()
        compose.check_mount_contract(report, _model(**overrides), "base")
        return report.failures

    def test_canonical_service_passes(self) -> None:
        self.assertEqual(self._failures(), [])

    def test_read_only_bind_passes(self) -> None:
        volume = {"type": "bind", "target": "/e2e-tls", "read_only": True}
        self.assertEqual(self._failures(volumes=[volume]), [])

    def test_omitted_read_only_is_writable(self) -> None:
        volume = {"type": "bind", "target": "/var/audio"}
        self.assertTrue(self._failures(volumes=[volume]))

    def test_string_volume_fails_closed(self) -> None:
        self.assertTrue(self._failures(volumes=["/e2e-tls:ro"]))

    def test_object_tmpfs_fails_closed(self) -> None:
        tmpfs = [{"target": "/work/tmp", "tmpfs": {"size": "268435456"}}]
        self.assertTrue(self._failures(tmpfs=tmpfs))

    def test_unit_suffix_on_the_tmpfs_size_fails_closed(self) -> None:
        tmpfs = ["/work/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777"]
        self.assertTrue(self._failures(tmpfs=tmpfs))

    def test_tmpfs_smaller_than_the_upload_fails(self) -> None:
        tmpfs = ["/work/tmp:rw,noexec,nosuid,nodev,size=100,mode=1777"]
        self.assertTrue(self._failures(tmpfs=tmpfs))

    def test_memory_must_be_a_decimal_string(self) -> None:
        self.assertTrue(self._failures(mem_limit=1073741824))
        self.assertTrue(self._failures(mem_limit="1g"))

    def test_core_zero_is_only_the_empty_object(self) -> None:
        self.assertTrue(self._failures(ulimits={"core": {"soft": 0, "hard": 0}}))
        self.assertTrue(self._failures(ulimits={"core": 0}))


class RivaIsolationTest(unittest.TestCase):
    def _model(self, api_env: dict[str, str]) -> dict:
        empty: dict[str, str] = {}
        return {
            "services": {
                "api": {"environment": api_env},
                "worker": {"environment": empty},
                "scheduler": {"environment": empty},
            }
        }

    def test_an_empty_override_is_not_a_secret(self) -> None:
        report = compose.Report()
        compose.check_riva_not_on_app_services(
            report,
            self._model({"NVIDIA_API_KEY": "", "RIVA_SERVER": ""}),
            "base",
        )
        self.assertEqual(report.failures, [])

    def test_a_riva_value_on_the_api_fails(self) -> None:
        report = compose.Report()
        compose.check_riva_not_on_app_services(
            report,
            self._model({"NVIDIA_API_KEY": "present"}),
            "base",
        )
        self.assertTrue(report.failures)


if __name__ == "__main__":
    unittest.main()
