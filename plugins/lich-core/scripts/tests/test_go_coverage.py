#!/usr/bin/env python3
"""Go-adapter coverage-truth tests.

Before this change, `go.analyze()` returned `[]` for five entirely different
situations and no caller could tell them apart:

    staticcheck not installed
    staticcheck timed out
    staticcheck invocation/parse failure
    rule registry unreadable
    genuinely no findings

Measured impact: the shipped 5s timeout expired on every real Go package
(staticcheck needs 8-12s warm, 40-98s cold), so the adapter reported
`total: 0` on a file containing a HIGH-severity SA5000 defect. A timeout was
being rendered as a clean result.

These tests pin each state to a distinct coverage status and pin the
capability-based security handoff.

Run: python3 plugins/lich-core/scripts/tests/test_go_coverage.py
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS)))
for p in (SCRIPTS, os.path.join(ROOT, "shared", "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from adapters import go  # noqa: E402
import coverage as cov  # noqa: E402
import handoff  # noqa: E402


def statuses(entries):
    return {e.cls: e.status for e in entries}


class TestZeroFlagsIsNotClean(unittest.TestCase):
    """Each distinct failure must produce a distinct, non-clean status."""

    def setUp(self):
        self._detect = go.detect
        self._run = go.run_staticcheck
        self._reg = go.load_registry

    def tearDown(self):
        go.detect = self._detect
        go.run_staticcheck = self._run
        go.load_registry = self._reg

    def test_missing_staticcheck_is_unavailable(self):
        go.detect = lambda: None
        flags, entries = go.analyze_with_coverage("x.go")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.UNAVAILABLE)

    def test_timeout_is_degraded_not_clean(self):
        go.detect = lambda: "/fake/staticcheck"
        go.run_staticcheck = lambda *a, **k: None
        flags, entries = go.analyze_with_coverage("x.go")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.DEGRADED)

    def test_registry_failure_is_degraded(self):
        def boom(*a, **k):
            raise OSError("registry gone")
        go.detect = lambda: "/fake/staticcheck"
        go.run_staticcheck = lambda *a, **k: []
        go.load_registry = boom
        flags, entries = go.analyze_with_coverage("x.go")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.DEGRADED)

    def test_genuine_clean_is_partial_never_complete(self):
        """Even a real clean run under-covers by design, so never 'complete'."""
        go.detect = lambda: "/fake/staticcheck"
        go.run_staticcheck = lambda *a, **k: []
        go.load_registry = lambda *a, **k: {}
        flags, entries = go.analyze_with_coverage("x.go")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.PARTIAL)

    def test_every_failure_mode_yields_false_clean_risk(self):
        for name, detect, run in (
            ("missing", lambda: None, self._run),
            ("timeout", lambda: "/fake/sc", lambda *a, **k: None),
        ):
            with self.subTest(mode=name):
                go.detect = detect
                go.run_staticcheck = run
                flags, entries = go.analyze_with_coverage("x.go")
                report = cov.build_report("lich-core", "x.go", "go",
                                          flags, entries)
                self.assertTrue(report["false_clean_risk"])
                self.assertFalse(report["clean"])


class TestTimeoutDefault(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("LICH_STATICCHECK_TIMEOUT_S", None)

    def test_default_is_realistic_for_staticcheck(self):
        """5s expired on every real package; the default must exceed it."""
        self.assertGreaterEqual(go._default_timeout_s(), 60)

    def test_env_override(self):
        os.environ["LICH_STATICCHECK_TIMEOUT_S"] = "30"
        self.assertEqual(go._default_timeout_s(), 30)

    def test_garbage_env_falls_back(self):
        os.environ["LICH_STATICCHECK_TIMEOUT_S"] = "not-a-number"
        self.assertEqual(go._default_timeout_s(), 120)


class TestCapabilityHandoff(unittest.TestCase):
    """Deferring a lane requires evidence, not branding."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ.pop("LICH_HYDRA_REPORT", None)
        os.environ.pop("LICH_HYDRA_REPORT_DIR", None)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("LICH_HYDRA_REPORT", None)

    def _report(self, status, cls="injection", truncated=False):
        path = os.path.join(self._tmp.name, "h.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "schema": cov.SCHEMA,
                "analysis_status": status,
                "target": {"path": "x.go"},
                "coverage": [{"class": cls, "status": status,
                              "truncated": truncated, "notes": "t"}],
            }, fh)
        os.environ["LICH_HYDRA_REPORT"] = path
        return path

    def test_no_report_means_uncovered_not_owned(self):
        state, reason = handoff.hydra_coverage("injection", "x.go")
        self.assertEqual(state, handoff.UNCOVERED)
        self.assertFalse(handoff.may_suppress("injection", "x.go")[0])

    def test_partial_hydra_coverage_does_not_authorise_suppression(self):
        self._report("partial")
        allowed, _ = handoff.may_suppress("injection", "x.go")
        self.assertFalse(allowed)

    def test_unsupported_hydra_is_unavailable(self):
        self._report("unsupported")
        state, _ = handoff.hydra_coverage("injection", "x.go")
        self.assertEqual(state, handoff.UNAVAILABLE)

    def test_complete_hydra_coverage_authorises_suppression(self):
        self._report("complete")
        allowed, reason = handoff.may_suppress("injection", "x.go")
        self.assertTrue(allowed, reason)

    def test_truncated_complete_does_not_authorise(self):
        self._report("complete", truncated=True)
        self.assertFalse(handoff.may_suppress("injection", "x.go")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
