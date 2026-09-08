#!/usr/bin/env python3
"""Suite D - cross-tool integration coverage tests.

These exercise the contract that matters when Hydra and Lich are composed:

    A BUG BEING PRESENT and A LANE BEING ANALYSED are different facts, and the
    composed system must never let the second silently stand in for the first.

The original benchmark's central failure was exactly this: Hydra could not
fire on non-HTTP Go taint, Lich refused to look because Hydra "owned"
security, and neither reported a gap - so a confirmed command injection fell
between two tools that each believed the other had it.

Scenarios covered (from the repair programme's Suite D list):
    Hydra unavailable / unsupported language / truncated / partial coverage
    Lich analyzer missing / unsupported language
    both zero findings with incomplete coverage
    both cover the same defect
    conflicting coverage claims

Hydra's location defaults to a sibling checkout; override with HYDRA_ROOT.

Run: python3 plugins/lich-core/scripts/tests/test_integration_coverage.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LICH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS)))
for p in (SCRIPTS, os.path.join(LICH_ROOT, "shared", "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import coverage as cov  # noqa: E402
import handoff  # noqa: E402
from adapters import go as go_adapter  # noqa: E402
from adapters import typescript as ts_adapter  # noqa: E402

HYDRA_ROOT = os.environ.get(
    "HYDRA_ROOT", os.path.join(os.path.dirname(LICH_ROOT), "hydra"))
HYDRA_SCANNER = os.path.join(HYDRA_ROOT, "shared", "scripts",
                             "vuln-scanner.py")
HAVE_HYDRA = os.path.isfile(HYDRA_SCANNER)


def run_hydra(path):
    proc = subprocess.run([sys.executable, HYDRA_SCANNER, path],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    return json.loads(proc.stdout)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        for var in ("LICH_HYDRA_REPORT", "LICH_HYDRA_REPORT_DIR"):
            os.environ.pop(var, None)

    def tearDown(self):
        self._tmp.cleanup()
        for var in ("LICH_HYDRA_REPORT", "LICH_HYDRA_REPORT_DIR"):
            os.environ.pop(var, None)

    def write(self, name, body):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p


@unittest.skipUnless(HAVE_HYDRA, "hydra checkout not found; set HYDRA_ROOT")
class TestHydraCoverageStates(_Tmp):
    """Hydra must distinguish 'analysed' from 'not analysed' on every path."""

    def test_unsupported_language_is_not_clean(self):
        p = self.write("x.c", 'char *pw = "hunter2";\nsystem(argv[1]);\n')
        r = run_hydra(p)
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["analysis_status"], "unsupported")
        self.assertFalse(r["clean"])
        self.assertTrue(r["false_clean_risk"])

    def test_truncation_is_reported(self):
        p = self.write("big.go", "package main\n" + "// f\n" * 2200)
        r = run_hydra(p)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["analysis_status"], "degraded")
        self.assertLess(r["target"]["lines_analyzed"], r["target"]["lines_total"])

    def test_partial_coverage_declares_its_blind_shapes(self):
        p = self.write("a.go", "package main\n\nfunc main() {}\n")
        r = run_hydra(p)
        self.assertEqual(r["analysis_status"], "partial")
        inj = [c for c in r["coverage"] if c["class"] == "injection"]
        self.assertTrue(inj)
        for shape in ("config-file", "argv", "environment", "cross-function"):
            self.assertIn(shape, inj[0]["shapes_unsupported"])

    def test_a_real_finding_is_still_reported(self):
        p = self.write("bad.py",
                       'import subprocess\n'
                       'subprocess.check_output("curl " + url, shell=True)\n')
        r = run_hydra(p)
        self.assertGreater(len(r["findings"]), 0)


class TestLichCoverageStates(_Tmp):
    """Lich must distinguish a missing analyser from a clean file."""

    def setUp(self):
        super().setUp()
        self._go_detect = go_adapter.detect
        self._ts_detect = ts_adapter.detect

    def tearDown(self):
        go_adapter.detect = self._go_detect
        ts_adapter.detect = self._ts_detect
        super().tearDown()

    def test_missing_go_analyzer_is_unavailable(self):
        go_adapter.detect = lambda: None
        flags, entries = go_adapter.analyze_with_coverage("x.go")
        self.assertEqual(flags, [])
        self.assertEqual(
            {e.cls: e.status for e in entries}["correctness"], cov.UNAVAILABLE)

    def test_missing_ts_analyzer_is_unavailable(self):
        ts_adapter.detect = lambda: None
        flags, entries = ts_adapter.analyze_with_coverage("x.ts")
        self.assertEqual(flags, [])
        self.assertEqual(
            {e.cls: e.status for e in entries}["correctness"], cov.UNAVAILABLE)


class TestHandoffNegotiation(_Tmp):
    """A lane may only be deferred on EVIDENCE of the other tool's coverage."""

    def _hydra_report(self, status, cls="injection", truncated=False):
        d = os.path.join(self.tmp, "reports")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "r.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"schema": cov.SCHEMA, "analysis_status": status,
                       "target": {"path": "x.go"},
                       "coverage": [{"class": cls, "status": status,
                                     "truncated": truncated, "notes": ""}]}, fh)
        os.environ["LICH_HYDRA_REPORT"] = p
        return p

    def test_no_hydra_report_leaves_the_lane_uncovered(self):
        state, _ = handoff.hydra_coverage("injection", "x.go")
        self.assertEqual(state, handoff.UNCOVERED)

    def test_hydra_partial_does_not_authorise_deferral(self):
        """The exact original failure: Hydra could not cover it, Lich deferred."""
        self._hydra_report("partial")
        allowed, reason = handoff.may_suppress("injection", "x.go")
        self.assertFalse(allowed, reason)

    def test_hydra_unsupported_is_unavailable_not_covered(self):
        self._hydra_report("unsupported")
        state, _ = handoff.hydra_coverage("injection", "x.go")
        self.assertEqual(state, handoff.UNAVAILABLE)
        self.assertFalse(handoff.may_suppress("injection", "x.go")[0])

    def test_truncated_complete_does_not_authorise_deferral(self):
        self._hydra_report("complete", truncated=True)
        self.assertFalse(handoff.may_suppress("injection", "x.go")[0])

    def test_only_complete_untruncated_authorises_deferral(self):
        self._hydra_report("complete")
        allowed, _ = handoff.may_suppress("injection", "x.go")
        self.assertTrue(allowed)

    def test_report_for_a_different_class_does_not_authorise(self):
        self._hydra_report("complete", cls="crypto")
        self.assertFalse(handoff.may_suppress("injection", "x.go")[0])


class TestComposedVerdict(_Tmp):
    """Both tools silent + incomplete coverage must never compose to clean."""

    def test_two_empty_partial_results_do_not_make_a_clean_verdict(self):
        entries = [
            cov.CoverageEntry("injection", "hydra-regex", cov.PATTERN,
                              cov.PARTIAL),
            cov.CoverageEntry("correctness", "staticcheck",
                              cov.INTRAPROCEDURAL, cov.PARTIAL),
        ]
        r = cov.build_report("composed", "x.go", "go", [], entries)
        self.assertFalse(r["clean"])
        self.assertTrue(r["false_clean_risk"])

    def test_worst_status_dominates_the_composition(self):
        entries = [
            cov.CoverageEntry("a", "hydra", cov.PATTERN, cov.COMPLETE),
            cov.CoverageEntry("b", "lich", cov.AST, cov.UNAVAILABLE),
        ]
        r = cov.build_report("composed", "x.go", "go", [], entries)
        self.assertEqual(r["analysis_status"], cov.UNAVAILABLE)
        self.assertFalse(r["clean"])

    def test_both_covering_the_same_class_can_be_clean(self):
        """Overlap is allowed to yield clean when both are genuinely complete."""
        entries = [
            cov.CoverageEntry("injection", "hydra", cov.AST, cov.COMPLETE),
            cov.CoverageEntry("injection", "lich", cov.AST, cov.COMPLETE),
        ]
        r = cov.build_report("composed", "x.go", "go", [], entries)
        self.assertTrue(r["clean"])

    def test_findings_present_is_never_reported_as_clean(self):
        entries = [cov.CoverageEntry("a", "e", cov.AST, cov.COMPLETE)]
        r = cov.build_report("composed", "x.go", "go", [{"t": "x"}], entries)
        self.assertFalse(r["clean"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
