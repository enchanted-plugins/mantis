#!/usr/bin/env python3
"""TypeScript adapter coverage tests.

Before this adapter existed, lich-typescript shipped a 93-rule registry and a
SKILL prompt but NO executable code path: `adapters.dispatch('.ts')` returned
only the semgrep polyglot adapter, so a .ts file received no TypeScript
analysis at all while the plugin was described as a language adapter mapping
biome rules. The registry was real; the code that used it did not exist.

These tests pin the adapter's existence, its routing, and - most importantly -
that a missing or failing biome is reported as unavailable/degraded rather
than as an empty, falsely clean result.

Run: python3 plugins/lich-core/scripts/tests/test_typescript_coverage.py
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

import adapters  # noqa: E402
from adapters import typescript as ts  # noqa: E402
import coverage as cov  # noqa: E402


def statuses(entries):
    return {e.cls: e.status for e in entries}


class TestRouting(unittest.TestCase):
    def test_ts_and_js_extensions_reach_the_adapter(self):
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cjs"):
            with self.subTest(ext=ext):
                mods = [f.__module__.rsplit(".", 1)[-1]
                        for f in adapters.dispatch("x" + ext)]
                self.assertIn("typescript", mods,
                              f"{ext} must route to the typescript adapter")

    def test_go_routing_is_unchanged(self):
        mods = [f.__module__.rsplit(".", 1)[-1]
                for f in adapters.dispatch("x.go")]
        self.assertIn("go", mods)
        self.assertNotIn("typescript", mods)


class TestRegistry(unittest.TestCase):
    def test_registry_actually_loads_rules(self):
        """The registry existed but nothing read it. It must load now."""
        reg = ts.load_registry()
        self.assertGreater(len(reg), 50)

    def test_correctness_routes_to_m1_and_security_defers(self):
        reg = ts.load_registry()
        routes = {v["route"] for v in reg.values()}
        self.assertIn("m1", routes)
        self.assertIn("defer", routes)

    def test_no_security_rule_routes_to_m1(self):
        reg = ts.load_registry()
        for rid, v in reg.items():
            if v["bucket"] == "security_defer_to_hydra":
                self.assertEqual(v["route"], "defer", rid)


class TestZeroFlagsIsNotClean(unittest.TestCase):
    def setUp(self):
        self._detect = ts.detect
        self._run = ts.run_biome

    def tearDown(self):
        ts.detect = self._detect
        ts.run_biome = self._run

    def test_missing_biome_is_unavailable(self):
        ts.detect = lambda: None
        flags, entries = ts.analyze_with_coverage("x.ts")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.UNAVAILABLE)

    def test_timeout_is_degraded_not_clean(self):
        ts.detect = lambda: "/fake/biome"
        ts.run_biome = lambda *a, **k: None
        flags, entries = ts.analyze_with_coverage("x.ts")
        self.assertEqual(flags, [])
        self.assertEqual(statuses(entries)["correctness"], cov.DEGRADED)

    def test_genuine_clean_is_partial_never_complete(self):
        ts.detect = lambda: "/fake/biome"
        ts.run_biome = lambda *a, **k: []
        flags, entries = ts.analyze_with_coverage("x.ts")
        self.assertEqual(statuses(entries)["correctness"], cov.PARTIAL)

    def test_every_failure_mode_carries_false_clean_risk(self):
        for name, detect, run in (
            ("missing", lambda: None, self._run),
            ("timeout", lambda: "/fake/biome", lambda *a, **k: None),
        ):
            with self.subTest(mode=name):
                ts.detect = detect
                ts.run_biome = run
                flags, entries = ts.analyze_with_coverage("x.ts")
                report = cov.build_report("lich-core", "x.ts", "ts",
                                          flags, entries)
                self.assertTrue(report["false_clean_risk"])
                self.assertFalse(report["clean"])

    def test_diagnostics_map_to_flags(self):
        """A correctness diagnostic must become an M1 flag."""
        reg = ts.load_registry()
        m1 = [r for r, v in reg.items() if v["route"] == "m1"]
        self.assertTrue(m1, "registry has no m1 rules to test with")
        diag = [{"category": "lint/" + m1[0],
                 "description": "test diagnostic",
                 "location": {"span": {"line": 7}}}]
        flags = ts.findings_to_flags(diag, reg, "x.ts")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line, 7)

    def test_security_diagnostic_never_becomes_an_m1_flag(self):
        reg = ts.load_registry()
        sec = [r for r, v in reg.items() if v["route"] == "defer"]
        if not sec:
            self.skipTest("registry has no security rules")
        diag = [{"category": "lint/" + sec[0], "description": "x",
                 "location": {"span": {"line": 3}}}]
        self.assertEqual(ts.findings_to_flags(diag, reg, "x.ts"), [])


class TestTimeoutBudget(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("LICH_BIOME_TIMEOUT_S", None)

    def test_default_and_override(self):
        self.assertGreaterEqual(ts._default_timeout_s(), 10)
        os.environ["LICH_BIOME_TIMEOUT_S"] = "25"
        self.assertEqual(ts._default_timeout_s(), 25)

    def test_garbage_env_falls_back(self):
        os.environ["LICH_BIOME_TIMEOUT_S"] = "nonsense"
        self.assertEqual(ts._default_timeout_s(), 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
