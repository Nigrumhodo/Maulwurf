"""Bearer auth for scripts/ops/budget_check.py stays off the command line."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "budget_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("budget_check", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


budget = _load()


class TokenSourceTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("MAULWURF_METRICS_TOKEN", None)

    def test_cli_token_is_rejected_before_any_request(self) -> None:
        self.assertEqual(budget.main(["--token", "visible-in-ps"]), 2)
        self.assertEqual(budget.main(["--token=visible-in-ps"]), 2)

    def test_token_file_sets_the_bearer_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api_metrics_token"
            path.write_text("file-token\n", encoding="utf-8")
            request = budget._request(
                "http://api:8000/internal/metrics",
                budget._token_from_sources(str(path)),
            )
        self.assertEqual(request.get_header("Authorization"), "Bearer file-token")

    def test_environment_token_is_accepted(self) -> None:
        os.environ["MAULWURF_METRICS_TOKEN"] = "env-token"
        request = budget._request(
            "http://127.0.0.1:8000/internal/metrics",
            budget._token_from_sources(None),
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer env-token")

    def test_file_and_environment_must_agree(self) -> None:
        os.environ["MAULWURF_METRICS_TOKEN"] = "env-token"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api_metrics_token"
            path.write_text("other-token\n", encoding="utf-8")
            with self.assertRaises(budget.MetricsError):
                budget._token_from_sources(str(path))


if __name__ == "__main__":
    unittest.main()
