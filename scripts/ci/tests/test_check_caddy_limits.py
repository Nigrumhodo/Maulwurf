"""Route coverage uses the decorator's first argument and prefix matches only."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_caddy_limits.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_caddy_limits", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


caddy = _load()


class RoutePathTest(unittest.TestCase):
    def test_multiline_decorator_uses_its_first_argument(self) -> None:
        source = textwrap.dedent(
            '''
            router = APIRouter(prefix="/internal")

            @router.get(
                "/metrics",
                responses={200: {"description": "see /healthz"}},
            )
            async def metrics() -> None:
                return None
            '''
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(caddy._router_paths(Path(directory)), ["/internal/metrics"])

    def test_a_non_string_first_argument_fails_closed(self) -> None:
        source = textwrap.dedent(
            '''
            router = APIRouter(prefix="/internal")

            @router.get(
                "/metrics",
                responses={200: {"description": "see /healthz"}},
            )
            async def metrics() -> None:
                return None

            @router.post(PATH)
            async def skipped() -> None:
                return None
            '''
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.py"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(caddy.CheckError) as raised:
                caddy._router_paths(Path(directory))
        self.assertIn("not a path string", str(raised.exception))

    def test_real_routers_include_nested_and_internal_paths(self) -> None:
        paths = caddy._router_paths(caddy.ROUTER_DIR)
        self.assertIn("/auth/google/start", paths)
        self.assertIn("/audios/{audio_id}/attempts/{attempt_id}/cancel", paths)
        self.assertIn("/internal/metrics", paths)
        self.assertIn("/healthz", paths)

    def test_prefix_match_covers_a_deep_path_and_not_the_bare_root(self) -> None:
        self.assertTrue(caddy._path_matches("/chat/*", "/chat/conversations/a/messages"))
        self.assertFalse(caddy._path_matches("/me/*", "/me"))
        self.assertTrue(caddy._path_matches("/search", "/search"))

    def test_a_second_star_is_rejected_before_matching(self) -> None:
        self.assertFalse(caddy._pattern_is_prefix_or_exact("/chat/*/*"))
        self.assertTrue(caddy._pattern_is_prefix_or_exact("/chat/*"))
        self.assertTrue(caddy._pattern_is_prefix_or_exact("/healthz"))

    def test_the_tracked_caddyfile_covers_the_routers(self) -> None:
        report = caddy.check(caddy.DEFAULT_CADDYFILE)
        self.assertGreater(report.proxy_handle_count, 0)


if __name__ == "__main__":
    unittest.main()
