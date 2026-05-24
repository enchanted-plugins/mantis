"""Unit tests for the verdict bar."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from rules import compose, evaluate_m1, evaluate_m5, evaluate_m6, evaluate_m7  # noqa: E402


class M1Evaluation(unittest.TestCase):
    def test_empty_flags_deploys(self):
        self.assertEqual(evaluate_m1([]).demands, "DEPLOY")

    def test_one_high_holds(self):
        self.assertEqual(evaluate_m1([{"severity": "HIGH"}]).demands, "HOLD")

    def test_two_high_holds(self):
        self.assertEqual(evaluate_m1([{"severity": "HIGH"}] * 2).demands, "HOLD")

    def test_three_high_fails(self):
        self.assertEqual(evaluate_m1([{"severity": "HIGH"}] * 3).demands, "FAIL")

    def test_critical_fails_alone(self):
        self.assertEqual(evaluate_m1([{"severity": "CRITICAL"}]).demands, "FAIL")

    def test_only_low_and_med_deploys(self):
        flags = [{"severity": "LOW"}] * 3 + [{"severity": "MED"}] * 2
        self.assertEqual(evaluate_m1(flags).demands, "DEPLOY")


class M5Evaluation(unittest.TestCase):
    def test_no_runs_not_evaluated(self):
        r = evaluate_m5([])
        self.assertEqual(r.status, "not-evaluated")
        self.assertEqual(r.demands, "DEPLOY")

    def test_confirmed_bug_hard_fails(self):
        self.assertEqual(evaluate_m5([{"status": "confirmed-bug"}]).demands, "FAIL")

    def test_timeout_holds(self):
        self.assertEqual(
            evaluate_m5([{"status": "timeout-without-confirmation"}]).demands, "HOLD"
        )

    def test_all_platform_unsupported_deploys_as_unsupported(self):
        r = evaluate_m5([{"status": "platform-unsupported"}] * 3)
        self.assertEqual(r.demands, "DEPLOY")
        self.assertEqual(r.status, "unsupported")

    def test_mix_with_confirmed_wins_fail(self):
        runs = [{"status": "confirmed-bug"}, {"status": "no-bug-found"}]
        self.assertEqual(evaluate_m5(runs).demands, "FAIL")

    def test_all_clean_deploys(self):
        self.assertEqual(evaluate_m5([{"status": "no-bug-found"}] * 5).demands, "DEPLOY")

    def test_sandbox_errors_hold(self):
        self.assertEqual(evaluate_m5([{"status": "sandbox-error"}]).demands, "HOLD")


class M6M7Stubs(unittest.TestCase):
    def test_m6_not_evaluated(self):
        r = evaluate_m6()
        self.assertEqual(r.status, "not-evaluated")
        self.assertEqual(r.demands, "DEPLOY")

    def test_m7_not_evaluated(self):
        r = evaluate_m7()
        self.assertEqual(r.status, "not-evaluated")


class Compose(unittest.TestCase):
    def test_clean_file_deploys_preliminary(self):
        v = compose(file="x.py", m1_flags=[], m5_runs=[])
        self.assertEqual(v.verdict, "DEPLOY")
        self.assertEqual(v.confidence, "preliminary")
        self.assertTrue(v.caveats, "caveats should note M5/M6/M7 not evaluated")

    def test_confirmed_bug_is_hard_fail(self):
        v = compose(
            file="x.py",
            m1_flags=[{"severity": "HIGH"}],
            m5_runs=[{"status": "confirmed-bug"}],
        )
        self.assertEqual(v.verdict, "FAIL")

    def test_three_high_m1_alone_fails(self):
        v = compose(file="x.py", m1_flags=[{"severity": "HIGH"}] * 3, m5_runs=[])
        self.assertEqual(v.verdict, "FAIL")

    def test_windows_platform_unsupported_with_clean_m1_deploys(self):
        v = compose(
            file="x.py",
            m1_flags=[],
            m5_runs=[{"status": "platform-unsupported"}],
        )
        self.assertEqual(v.verdict, "DEPLOY")
        self.assertTrue(
            any("unsupported" in c.lower() for c in v.caveats),
            f"expected unsupported caveat, got {v.caveats}",
        )

    def test_one_high_plus_unsupported_holds(self):
        v = compose(
            file="x.py",
            m1_flags=[{"severity": "HIGH"}],
            m5_runs=[{"status": "platform-unsupported"}] * 2,
        )
        self.assertEqual(v.verdict, "HOLD")
        self.assertEqual(v.confidence, "reduced")


if __name__ == "__main__":
    unittest.main()
