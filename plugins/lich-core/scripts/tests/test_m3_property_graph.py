"""Unit tests for the M3 Yamaguchi Property-Graph Traversal adapter.

Run (from repo root):
    python plugins/lich-core/scripts/tests/test_m3_property_graph.py
or:
    python -m unittest discover -s plugins/lich-core/scripts/tests -p 'test_*.py'

Joern is NEVER invoked in these tests. Subprocess is mocked throughout;
canned JSON payloads stand in for real Joern output.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

# Make sibling `scripts/` importable regardless of cwd (hyphen in
# `lich-core` blocks package import).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import m3_property_graph  # noqa: E402
from m3_property_graph import (  # noqa: E402
    LANG,
    FILE_EXTENSIONS,
    analyze,
    detect,
    _run_joern_query,
    _finding_to_flag,
    _query_touches_security,
    _M3_QUERIES,
)
from m1_walker import Flag  # noqa: E402


# -------------------------------------------------------------------------
# Helpers — canned Joern payloads (the *parsed JSON* Joern would print).
# -------------------------------------------------------------------------


def _null_deref_finding(
    file: str = "/tmp/example.py",
    line: int = 42,
    function: str = "caller_fn",
    deref_expr: str = "user.name",
) -> dict:
    return {
        "file": file,
        "line": line,
        "function": function,
        "deref_expr": deref_expr,
    }


def _unbounded_iter_finding(
    file: str = "/tmp/example.py",
    line: int = 17,
    function: str = "process",
    iter_target: str = "items",
) -> dict:
    return {
        "file": file,
        "line": line,
        "function": function,
        "iter_target": iter_target,
    }


def _tainted_div_finding(
    file: str = "/tmp/example.py",
    line: int = 88,
    function: str = "compute",
    denom_expr: str = "denom",
) -> dict:
    return {
        "file": file,
        "line": line,
        "function": function,
        "denom_expr": denom_expr,
    }


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = ""):
    proc = mock.MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# -------------------------------------------------------------------------
# detect()
# -------------------------------------------------------------------------


class DetectTests(unittest.TestCase):
    def test_detect_missing_joern_returns_none(self):
        """When neither `joern` nor `joern-cli` is on PATH, return None."""
        with mock.patch(
            "m3_property_graph.shutil.which", return_value=None
        ):
            self.assertIsNone(detect())

    def test_detect_present_joern_returns_path(self):
        with mock.patch(
            "m3_property_graph.shutil.which",
            side_effect=lambda name: (
                "/usr/local/bin/joern" if name == "joern" else None
            ),
        ):
            self.assertEqual(detect(), "/usr/local/bin/joern")

    def test_detect_falls_back_to_joern_cli(self):
        """If `joern` is missing but `joern-cli` is on PATH, use the latter."""
        def _which(name):
            if name == "joern":
                return None
            if name == "joern-cli":
                return "/opt/joern-cli/bin/joern-cli"
            return None
        with mock.patch(
            "m3_property_graph.shutil.which", side_effect=_which
        ):
            self.assertEqual(detect(), "/opt/joern-cli/bin/joern-cli")


# -------------------------------------------------------------------------
# Canned query -> flag mappings
# -------------------------------------------------------------------------


class CannedMappingTests(unittest.TestCase):
    """Feed a canned Joern JSON list into _run_joern_query via subprocess
    mock and confirm the per-rule mapping produces the expected Flag."""

    def setUp(self):
        # Force `detect()` to appear positive for analyze() tests.
        self._detect_patch = mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        )
        self._detect_patch.start()

    def tearDown(self):
        self._detect_patch.stop()

    def _run_analyze_with_per_rule_output(
        self, per_rule: dict[str, list[dict]],
    ) -> list[Flag]:
        """Drive analyze() with a mock subprocess.run that returns a
        different canned JSON payload per rule_id.

        Rule dispatch is identified by scanning the temp .sc script's
        contents for a known rule sentinel. We write the rule_id into the
        script header for precisely this purpose.
        """
        def _side_effect(argv, **kwargs):
            # argv = [joern_path, "--script", tmp_path]
            self.assertEqual(argv[1], "--script")
            with open(argv[2], "r", encoding="utf-8") as fh:
                body = fh.read()
            for rid, payload in per_rule.items():
                if f"Rule:   {rid}" in body:
                    return _fake_proc(json.dumps(payload), returncode=0)
            # Default: empty findings.
            return _fake_proc("[]", returncode=0)

        with mock.patch(
            "m3_property_graph.subprocess.run", side_effect=_side_effect
        ):
            return analyze("/tmp/example.py")

    def test_canned_null_deref_query_output_maps_to_m3_001(self):
        flags = self._run_analyze_with_per_rule_output({
            "M3-001": [_null_deref_finding(line=42, function="caller_fn",
                                           deref_expr="user.name")],
        })
        m3_001 = [f for f in flags if f.rule_id == "M3-001"]
        self.assertEqual(len(m3_001), 1)
        flag = m3_001[0]
        self.assertEqual(flag.line, 42)
        self.assertEqual(flag.function, "caller_fn")
        self.assertEqual(flag.flag_class, "m3-cpg")
        self.assertEqual(flag.severity, "HIGH")
        self.assertEqual(flag.witness_hints["source"], "joern")
        self.assertEqual(flag.witness_hints["engine"], "M3")
        self.assertEqual(flag.witness_hints["reason"],
                         "cross-function-null-dereference")
        self.assertEqual(flag.witness_hints["deref_expr"], "user.name")
        self.assertEqual(flag.witness_hints["boundary_values"], [None])
        self.assertTrue(flag.needs_M5_confirmation)

    def test_canned_unbounded_iter_maps_to_m3_002(self):
        flags = self._run_analyze_with_per_rule_output({
            "M3-002": [_unbounded_iter_finding(line=17, function="process",
                                               iter_target="items")],
        })
        m3_002 = [f for f in flags if f.rule_id == "M3-002"]
        self.assertEqual(len(m3_002), 1)
        flag = m3_002[0]
        self.assertEqual(flag.line, 17)
        self.assertEqual(flag.function, "process")
        self.assertEqual(flag.severity, "MED")
        self.assertEqual(flag.flag_class, "m3-cpg")
        self.assertEqual(flag.witness_hints["reason"],
                         "unbounded-iteration-over-external-input")
        self.assertEqual(flag.witness_hints["iter_target"], "items")
        # No scalar boundary value for collection-size runtime failures.
        self.assertNotIn("boundary_values", flag.witness_hints)

    def test_canned_tainted_div_maps_to_m3_003(self):
        flags = self._run_analyze_with_per_rule_output({
            "M3-003": [_tainted_div_finding(line=88, function="compute",
                                            denom_expr="denom")],
        })
        m3_003 = [f for f in flags if f.rule_id == "M3-003"]
        self.assertEqual(len(m3_003), 1)
        flag = m3_003[0]
        self.assertEqual(flag.line, 88)
        self.assertEqual(flag.function, "compute")
        self.assertEqual(flag.severity, "HIGH")
        self.assertEqual(flag.witness_hints["reason"],
                         "dataflow-reachable-division-denominator")
        self.assertEqual(flag.witness_hints["denom_expr"], "denom")
        # M3-003 is a div-by-zero runtime-failure candidate; witness is 0.
        self.assertEqual(flag.witness_hints["boundary_values"], [0])

    def test_all_three_rules_fire_on_multi_finding_run(self):
        flags = self._run_analyze_with_per_rule_output({
            "M3-001": [_null_deref_finding()],
            "M3-002": [_unbounded_iter_finding()],
            "M3-003": [_tainted_div_finding()],
        })
        ids = sorted(f.rule_id for f in flags)
        self.assertEqual(ids, ["M3-001", "M3-002", "M3-003"])


# -------------------------------------------------------------------------
# Error handling paths
# -------------------------------------------------------------------------


class ErrorPathTests(unittest.TestCase):
    def test_timeout_returns_none_empty_flag_list(self):
        """Subprocess timeout => _run_joern_query returns None, and
        analyze() produces [] (no flags) rather than raising."""
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ), mock.patch(
            "m3_property_graph.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="joern", timeout=60),
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_malformed_json_returns_empty(self):
        """Garbage stdout => _run_joern_query returns None; analyze()
        produces [] rather than raising JSONDecodeError."""
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ), mock.patch(
            "m3_property_graph.subprocess.run",
            return_value=_fake_proc("not-json-at-all-{{{", returncode=0),
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_non_list_json_returns_empty(self):
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ), mock.patch(
            "m3_property_graph.subprocess.run",
            return_value=_fake_proc('{"not": "a list"}', returncode=0),
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_empty_stdout_yields_no_flags(self):
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ), mock.patch(
            "m3_property_graph.subprocess.run",
            return_value=_fake_proc("", returncode=0),
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_invocation_oserror_yields_no_flags(self):
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ), mock.patch(
            "m3_property_graph.subprocess.run",
            side_effect=OSError("exec format error"),
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_detect_absent_yields_no_flags(self):
        with mock.patch(
            "m3_property_graph.detect", return_value=None
        ):
            flags = analyze("/tmp/example.py")
        self.assertEqual(flags, [])

    def test_unsupported_extension_yields_no_flags(self):
        """Files outside FILE_EXTENSIONS short-circuit to []."""
        with mock.patch(
            "m3_property_graph.detect", return_value="/usr/local/bin/joern"
        ):
            flags = analyze("/tmp/example.md")
        self.assertEqual(flags, [])

    def test_zero_line_finding_skipped(self):
        """A finding with missing/zero line is dropped (cannot attach
        a witness to an unknown location)."""
        rule_meta = _M3_QUERIES["M3-001"]
        flag = _finding_to_flag(
            "/tmp/x.py", "M3-001", rule_meta,
            {"line": 0, "function": "f"},
        )
        self.assertIsNone(flag)


# -------------------------------------------------------------------------
# Security guard
# -------------------------------------------------------------------------


class SecurityGuardTests(unittest.TestCase):
    def test_security_pattern_in_query_never_mapped(self):
        """If a caller (or future code edit) passes a query string
        touching CWE / injection / taint-sink vocab, _run_joern_query
        refuses even with Joern detected. Hydra R3 owns CWE taxonomy."""
        bad_query = 'cpg.call.name("taintSink").reachableByFlows(...)'
        with mock.patch(
            "m3_property_graph.subprocess.run"
        ) as run_mock:
            result = _run_joern_query(
                file_path="/tmp/example.py",
                rule_id="M3-999",
                query=bad_query,
                joern_path="/usr/local/bin/joern",
            )
        self.assertIsNone(result)
        # Most important: subprocess NEVER ran — the guard blocks before
        # invoking Joern at all.
        run_mock.assert_not_called()

    def test_security_guard_catches_cwe_mention(self):
        self.assertTrue(
            _query_touches_security("M3-X", "cpg ... CWE-89 sinks")
        )

    def test_security_guard_catches_injection(self):
        self.assertTrue(
            _query_touches_security("M3-X", "find injection sites")
        )

    def test_security_guard_catches_rule_id_mention(self):
        self.assertTrue(
            _query_touches_security("CWE-79", "cpg.call.name(\"foo\")")
        )

    def test_security_guard_lets_correctness_pass(self):
        """The canonical v1 queries must NOT trip the guard."""
        for rid, meta in _M3_QUERIES.items():
            self.assertFalse(
                _query_touches_security(rid, meta["query"]),
                f"Canonical rule {rid} tripped the security guard — "
                "either a false-positive token or Hydra-lane drift.",
            )


# -------------------------------------------------------------------------
# Flag shape — must match M1 field-for-field.
# -------------------------------------------------------------------------


class FlagShapeTests(unittest.TestCase):
    """Critical contract: downstream consumers (M5 sandbox, M-verdict)
    bind to `m1_walker.Flag`. M3 must emit the same dataclass with the
    same field set. A mismatch here fractures the sandbox JSONL schema."""

    def test_flag_shape_matches_m1_schema(self):
        rule_meta = _M3_QUERIES["M3-001"]
        flag = _finding_to_flag(
            "/tmp/x.py", "M3-001", rule_meta,
            _null_deref_finding(line=10),
        )
        self.assertIsNotNone(flag)
        # Same dataclass, not a lookalike.
        self.assertIsInstance(flag, Flag)
        # Same field set, byte-for-byte.
        m1_fields = {f.name for f in dataclasses.fields(Flag)}
        m3_fields = {f.name for f in dataclasses.fields(type(flag))}
        self.assertEqual(m1_fields, m3_fields)
        # Required M1 fields all populated.
        for attr in (
            "file", "line", "function", "rule_id",
            "flag_class", "severity", "witness_hints",
            "needs_M5_confirmation", "m1_confidence",
        ):
            self.assertTrue(hasattr(flag, attr))
        # Types on each field.
        self.assertIsInstance(flag.file, str)
        self.assertIsInstance(flag.line, int)
        self.assertIsInstance(flag.function, str)
        self.assertIsInstance(flag.rule_id, str)
        self.assertIsInstance(flag.flag_class, str)
        self.assertIsInstance(flag.severity, str)
        self.assertIsInstance(flag.witness_hints, dict)
        self.assertIsInstance(flag.needs_M5_confirmation, bool)
        self.assertIsInstance(flag.m1_confidence, float)

    def test_severity_is_canonical_bucket(self):
        """Severity must be one of the canonical M1/M5 buckets
        (HIGH | MED | LOW). Verdict's DEPLOY/HOLD/FAIL table depends
        on this vocabulary."""
        for rid, meta in _M3_QUERIES.items():
            self.assertIn(
                meta["severity"], {"HIGH", "MED", "LOW"},
                f"{rid} has non-canonical severity {meta['severity']!r}",
            )


# -------------------------------------------------------------------------
# Module-level constants surface
# -------------------------------------------------------------------------


class ModuleSurfaceTests(unittest.TestCase):
    def test_lang_is_python_for_v1(self):
        self.assertEqual(LANG, "python")

    def test_file_extensions_claim_py_js_java(self):
        self.assertIn(".py", FILE_EXTENSIONS)
        self.assertIn(".js", FILE_EXTENSIONS)
        self.assertIn(".java", FILE_EXTENSIONS)

    def test_three_canonical_rules_declared(self):
        self.assertEqual(
            sorted(_M3_QUERIES.keys()),
            ["M3-001", "M3-002", "M3-003"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
