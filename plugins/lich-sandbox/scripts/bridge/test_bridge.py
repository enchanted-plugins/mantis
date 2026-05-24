"""Lich M5 sandbox — bridge unit tests.

Covers the three behaviours that can be exercised without WSL installed:

1. `check()` on the current Windows host returns `unsupported` with an
   informative reason that names WSL.
2. `check()` caches its result — the second call does not re-probe.
3. Windows-to-WSL path translation (`c:\\foo\\bar.py` -> `/mnt/c/foo/bar.py`).

Tests that require WSL to actually run are gated with `skipUnless`. This
file imports cleanly on POSIX CI because `wsl.py` has no Windows-only
imports at module scope.
"""

from __future__ import annotations

import platform
import unittest
from unittest import mock

from . import platform_guard
from .platform_guard import check
from .wsl import _windows_to_wsl


class CheckOnThisHostTest(unittest.TestCase):
    """Fire-once probe on the current host. Assertions adapt per platform
    so the suite is green on POSIX CI and on this Windows-without-WSL box."""

    def setUp(self) -> None:
        platform_guard._reset_cache_for_tests()

    def test_shape(self) -> None:
        result = check()
        self.assertIn("supported", result)
        self.assertIn("backend", result)
        self.assertIn("reason", result)
        self.assertIsInstance(result["supported"], bool)
        self.assertIn(result["backend"], {"posix", "wsl", "unsupported"})

    @unittest.skipUnless(platform.system() == "Windows",
                         "Windows-specific — this host should report unsupported")
    def test_windows_without_wsl_is_unsupported(self) -> None:
        result = check()
        # On this specific box, WSL is not installed. The probe must
        # surface that honestly — no silent green.
        self.assertFalse(result["supported"])
        self.assertEqual(result["backend"], "unsupported")
        # The reason string MUST mention WSL so reviewers know why M5
        # skipped, per CLAUDE.md behavioral contract 2.
        self.assertIn("WSL", result["reason"])

    @unittest.skipUnless(platform.system() in ("Linux", "Darwin"),
                         "POSIX-only check")
    def test_posix_is_supported(self) -> None:
        result = check()
        self.assertTrue(result["supported"])
        self.assertEqual(result["backend"], "posix")


class CheckCacheTest(unittest.TestCase):
    """One probe per process — subsequent calls hit the cache."""

    def setUp(self) -> None:
        platform_guard._reset_cache_for_tests()

    def test_second_call_does_not_reprobe(self) -> None:
        # Prime the cache.
        first = check()
        # Patch the Windows probe to blow up if called; POSIX path does
        # not consult subprocess at all, so the patch is safe either way.
        with mock.patch.object(
            platform_guard, "_probe_windows",
            side_effect=AssertionError("cache miss — re-probed"),
        ):
            second = check()
        self.assertIs(first, second)  # same dict identity = cached


class PathTranslationTest(unittest.TestCase):
    """Windows -> WSL path translation is pure; run everywhere."""

    def test_lowercase_drive_and_slash_flip(self) -> None:
        self.assertEqual(
            _windows_to_wsl(r"c:\foo\bar.py"),
            "/mnt/c/foo/bar.py",
        )

    def test_uppercase_drive_normalises_to_lower(self) -> None:
        self.assertEqual(
            _windows_to_wsl(r"C:\git\enchanted-skills\lich\foo.py"),
            "/mnt/c/git/enchanted-skills/lich/foo.py",
        )

    def test_forward_slash_input_still_works(self) -> None:
        self.assertEqual(
            _windows_to_wsl("D:/data/file.py"),
            "/mnt/d/data/file.py",
        )

    def test_drive_root_only(self) -> None:
        self.assertEqual(_windows_to_wsl(r"C:\\"), "/mnt/c/")


@unittest.skipUnless(check().get("backend") == "wsl",
                     "WSL not available on this host")
class WslExecutionTest(unittest.TestCase):
    """Smoke tests that only run on hosts where WSL is present."""

    def test_basic_invocation(self) -> None:  # pragma: no cover — infra-gated
        from .wsl import run_in_wsl
        import json
        # Minimal target: a no-op function. On a real box we'd write a
        # temp .py file; smoke test kept intentionally narrow.
        result = run_in_wsl(
            target_file=r"C:\Windows\System32\drivers\etc\hosts",  # not a .py
            function_name="nope",
            witness_json=json.dumps({"args": [], "kwargs": {}}),
            timeout_s=5,
        )
        self.assertIn("exit_code", result)
        self.assertIn("duration_ms", result)


if __name__ == "__main__":
    unittest.main()
