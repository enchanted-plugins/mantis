"""Unit tests for the ruff adapter.

Run (from repo root):
    python plugins/mantis-core/scripts/tests/test_ruff_adapter.py
or:
    python -m unittest discover -s plugins/mantis-core/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# Make sibling `scripts/` importable regardless of cwd (hyphen in
# `mantis-core` blocks package import).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import ruff_adapter  # noqa: E402
from ruff_adapter import (  # noqa: E402
    detect_ruff,
    load_registry,
    run_ruff,
    findings_to_flags,
    _is_security_rule,
)


# -------------------------------------------------------------------------
# Canned ruff findings — shape matches `ruff check --output-format=json`
# -------------------------------------------------------------------------

def _finding(code: str, row: int = 10, message: str = "canned") -> dict:
    return {
        "code": code,
        "message": message,
        "location": {"row": row, "column": 1},
        "end_location": {"row": row, "column": 10},
        "filename": "target.py",
        "fix": None,
        "url": None,
    }


class DetectRuffTests(unittest.TestCase):
    def test_detect_missing_ruff_returns_none(self):
        with mock.patch("ruff_adapter.shutil.which", return_value=None):
            self.assertIsNone(detect_ruff())

    def test_detect_present_ruff_returns_path(self):
        with mock.patch(
            "ruff_adapter.shutil.which", return_value="/usr/bin/ruff"
        ):
            self.assertEqual(detect_ruff(), "/usr/bin/ruff")


class RegistryTests(unittest.TestCase):
    def test_registry_loads(self):
        reg = load_registry()
        self.assertIn("F401", reg)
        self.assertIn("B006", reg)
        self.assertEqual(reg["F401"]["route"], "m1")
        self.assertEqual(reg["F401"]["severity"], "HIGH")
        self.assertEqual(reg["B006"]["route"], "m1")
        self.assertEqual(reg["B006"]["severity"], "HIGH")

    def test_m7_rules_tagged_as_m7(self):
        reg = load_registry()
        self.assertIn("UP001", reg)
        self.assertEqual(reg["UP001"]["route"], "m7")

    def test_security_rules_tagged_defer(self):
        reg = load_registry()
        # Concrete S-code (not a wildcard family provenance entry).
        self.assertIn("S102", reg)
        self.assertEqual(reg["S102"]["route"], "defer")

    def test_wildcard_family_entries_skipped(self):
        reg = load_registry()
        self.assertNotIn("S1**", reg)


class IsSecurityRuleTests(unittest.TestCase):
    def test_s_with_digits_is_security(self):
        self.assertTrue(_is_security_rule("S101"))
        self.assertTrue(_is_security_rule("S608"))

    def test_sim_is_not_security(self):
        # SIM* is simplify, not bandit-security. Must not be swept up.
        self.assertFalse(_is_security_rule("SIM102"))

    def test_f_is_not_security(self):
        self.assertFalse(_is_security_rule("F401"))


class RunRuffTests(unittest.TestCase):
    def _fake_proc(self, stdout: str, returncode: int = 0):
        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.stderr = ""
        proc.returncode = returncode
        return proc

    def test_malformed_json_returns_none(self):
        with mock.patch(
            "ruff_adapter.subprocess.run",
            return_value=self._fake_proc("not-json-at-all", returncode=1),
        ):
            result = run_ruff("target.py", "/usr/bin/ruff")
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        with mock.patch(
            "ruff_adapter.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=5),
        ):
            result = run_ruff("target.py", "/usr/bin/ruff", timeout_s=5)
        self.assertIsNone(result)

    def test_empty_stdout_returns_empty_list(self):
        with mock.patch(
            "ruff_adapter.subprocess.run",
            return_value=self._fake_proc("", returncode=0),
        ):
            result = run_ruff("target.py", "/usr/bin/ruff")
        self.assertEqual(result, [])

    def test_valid_findings_parsed(self):
        payload = json.dumps([_finding("F401", row=3)])
        with mock.patch(
            "ruff_adapter.subprocess.run",
            return_value=self._fake_proc(payload, returncode=1),
        ):
            result = run_ruff("target.py", "/usr/bin/ruff")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "F401")

    def test_non_list_json_returns_none(self):
        with mock.patch(
            "ruff_adapter.subprocess.run",
            return_value=self._fake_proc('{"oops": true}', returncode=0),
        ):
            result = run_ruff("target.py", "/usr/bin/ruff")
        self.assertIsNone(result)


class FindingsToFlagsTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()
        # Write a tiny source file so function-span resolution is exercised.
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        )
        self.tmp.write(
            "import os\n"          # line 1 — import, top-level
            "\n"                   # line 2
            "def foo(x=[]):\n"     # line 3 — B006 target
            "    return x\n"       # line 4
            "\n"                   # line 5
            "def bar():\n"         # line 6
            "    y = 1\n"          # line 7 — F841 target
        )
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_map_f_series_to_high(self):
        findings = [_finding("F401", row=1, message="unused import 'os'")]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(len(flags), 1)
        flag = flags[0]
        self.assertEqual(flag.rule_id, "F401")
        self.assertEqual(flag.severity, "HIGH")
        self.assertEqual(flag.flag_class, "ruff")
        self.assertEqual(flag.witness_hints["source"], "ruff")
        self.assertEqual(flag.witness_hints["reason"], "unused-import")

    def test_b006_carries_hint(self):
        findings = [_finding("B006", row=3, message="mutable default arg")]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].function, "foo")
        self.assertEqual(
            flags[0].witness_hints["reason"], "mutable-default-argument"
        )

    def test_function_resolution_for_inner_line(self):
        findings = [_finding("F841", row=7, message="unused local")]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].function, "bar")

    def test_top_level_resolves_to_module(self):
        findings = [_finding("F401", row=1, message="unused import")]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(flags[0].function, "<module>")

    def test_security_series_never_mapped(self):
        # Even if S102 slipped into registry as correctness_m1 — pretend it did.
        rigged = dict(self.registry)
        rigged["S102"] = {"bucket": "correctness_m1",
                          "severity": "HIGH", "route": "m1"}
        findings = [_finding("S102", row=1, message="exec")]
        flags = findings_to_flags(findings, rigged, self.path)
        self.assertEqual(
            flags, [],
            "S-series rules must NEVER produce M1 flags — Reaper's lane.",
        )

    def test_idiom_rules_dont_become_m1_flags(self):
        findings = [_finding("UP001", row=1, message="useless metaclass")]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(
            flags, [],
            "UP-series is idiom_m7 — must not emit as M1 in Slice A.",
        )

    def test_unknown_rule_id_ignored(self):
        findings = [_finding("ZZZ999", row=1)]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(flags, [])

    def test_missing_code_skipped(self):
        findings = [{"message": "no code", "location": {"row": 1}}]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(flags, [])

    def test_zero_line_skipped(self):
        findings = [_finding("F401", row=0)]
        flags = findings_to_flags(findings, self.registry, self.path)
        self.assertEqual(flags, [])


class MalformedJsonFallbackTests(unittest.TestCase):
    """The spec line `test_malformed_json_returns_empty` asks that a caller
    treat malformed JSON as "no findings" — `run_ruff` returns None (to
    distinguish from legitimate empty), and the `analyze_with_ruff` wrapper
    coerces that to []. Both behaviors covered."""

    def test_analyze_with_ruff_coerces_none_to_empty(self):
        with mock.patch("ruff_adapter.detect_ruff",
                        return_value="/usr/bin/ruff"), \
             mock.patch("ruff_adapter.run_ruff", return_value=None):
            out = ruff_adapter.analyze_with_ruff("target.py")
        self.assertEqual(out, [])

    def test_analyze_with_ruff_returns_none_when_absent(self):
        with mock.patch("ruff_adapter.detect_ruff", return_value=None):
            out = ruff_adapter.analyze_with_ruff("target.py")
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
