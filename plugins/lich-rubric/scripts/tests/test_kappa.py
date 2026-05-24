"""Unit tests for the M7 ingestion layer: kappa math, score_ingest, reader."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from kappa import (  # noqa: E402
    compose_kappa,
    mean_score,
    needs_opus_adjudication,
    per_axis_agreement,
    unstable_axes,
)
from reader import all_files_with_scores, latest_for  # noqa: E402
from score_ingest import ingest, validate_scores  # noqa: E402


_AXES = [
    "clarity",
    "correctness_at_glance",
    "idiom_fit",
    "testability",
    "simplicity",
]


def _perfect_pass(n: int = 4) -> dict:
    return {a: n for a in _AXES}


class PerAxisAgreement(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(per_axis_agreement(5, 5), 1.0)
        self.assertEqual(per_axis_agreement(1, 1), 1.0)
        self.assertEqual(per_axis_agreement(3, 3), 1.0)

    def test_maximum_disagreement(self):
        self.assertEqual(per_axis_agreement(1, 5), 0.0)
        self.assertEqual(per_axis_agreement(5, 1), 0.0)

    def test_mid_disagreement(self):
        self.assertEqual(per_axis_agreement(3, 4), 0.75)
        self.assertEqual(per_axis_agreement(4, 3), 0.75)
        self.assertAlmostEqual(per_axis_agreement(2, 4), 0.5)

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            per_axis_agreement(0, 3)
        with self.assertRaises(ValueError):
            per_axis_agreement(3, 6)
        with self.assertRaises(ValueError):
            per_axis_agreement(-1, 3)

    def test_non_int_raises(self):
        with self.assertRaises(ValueError):
            per_axis_agreement(3.5, 4)  # type: ignore[arg-type]


class ComposeKappa(unittest.TestCase):
    def test_matching_passes_all_agree(self):
        p1 = _perfect_pass(4)
        p2 = _perfect_pass(4)
        k = compose_kappa(p1, p2, _AXES)
        for axis in _AXES:
            self.assertEqual(k[axis]["agreement"], 1.0)
            self.assertFalse(k[axis]["unstable"])
            self.assertEqual(k[axis]["delta"], 0)

    def test_one_axis_max_disagree_flags_unstable(self):
        p1 = _perfect_pass(4)
        p1["clarity"] = 1
        p2 = _perfect_pass(4)
        p2["clarity"] = 5
        k = compose_kappa(p1, p2, _AXES)
        self.assertTrue(k["clarity"]["unstable"])
        self.assertEqual(k["clarity"]["agreement"], 0.0)
        for axis in _AXES:
            if axis != "clarity":
                self.assertFalse(k[axis]["unstable"])

    def test_missing_axis_raises(self):
        p1 = _perfect_pass(4)
        p2 = _perfect_pass(4)
        del p2["simplicity"]
        with self.assertRaises(ValueError):
            compose_kappa(p1, p2, _AXES)


class NeedsOpus(unittest.TestCase):
    def test_delta_2_triggers(self):
        p1 = _perfect_pass(4)
        p1["clarity"] = 2
        p2 = _perfect_pass(4)
        p2["clarity"] = 4
        k = compose_kappa(p1, p2, _AXES)
        self.assertTrue(needs_opus_adjudication(k))

    def test_delta_1_does_not_trigger(self):
        p1 = _perfect_pass(4)
        p1["clarity"] = 3
        p2 = _perfect_pass(4)
        p2["clarity"] = 4
        k = compose_kappa(p1, p2, _AXES)
        self.assertFalse(needs_opus_adjudication(k))

    def test_exactly_delta_threshold_triggers(self):
        # 1.5 threshold, integer scores — 2 triggers, 1 does not.
        p1 = _perfect_pass(4)
        p1["clarity"] = 1
        p2 = _perfect_pass(4)
        p2["clarity"] = 3
        k = compose_kappa(p1, p2, _AXES)
        self.assertTrue(needs_opus_adjudication(k, delta_threshold=1.5))


class MeanAndUnstable(unittest.TestCase):
    def test_mean_matches_manual(self):
        p1 = {"a": 4, "b": 5}
        p2 = {"a": 3, "b": 4}
        # (4 + 5 + 3 + 4) / 4 = 4.0
        self.assertEqual(mean_score(p1, p2), 4.0)

    def test_unstable_axes_lists_flagged_only(self):
        p1 = _perfect_pass(4)
        p1["clarity"] = 1
        p1["simplicity"] = 2
        p2 = _perfect_pass(4)
        p2["clarity"] = 5
        p2["simplicity"] = 5
        k = compose_kappa(p1, p2, _AXES)
        u = unstable_axes(k)
        self.assertIn("clarity", u)
        # (2, 5) -> |delta|=3, agreement = 1 - 3/4 = 0.25 -> unstable
        self.assertIn("simplicity", u)
        self.assertNotIn("testability", u)


class ScoreIngest(unittest.TestCase):
    def _tmp(self) -> Path:
        d = tempfile.mkdtemp(prefix="lich-kappa-")
        return Path(d) / "kappa-log.jsonl"

    def test_valid_pass_writes_record(self):
        log = self._tmp()
        p1 = _perfect_pass(4)
        p2 = _perfect_pass(4)
        rec = ingest(file="x/y.py", pass1=p1, pass2=p2, log_path=log)
        self.assertEqual(rec["file"], "x/y.py")
        self.assertEqual(rec["unstable_axes"], [])
        self.assertFalse(rec["needs_opus_adjudication"])
        self.assertTrue(log.exists())
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["rubric_version"], "1.0")
        self.assertEqual(parsed["judge_model"], "claude-sonnet-4-6")

    def test_invalid_axis_raises(self):
        log = self._tmp()
        p1 = _perfect_pass(4)
        p2 = _perfect_pass(4)
        p2["extra_axis"] = 3
        with self.assertRaises(ValueError):
            ingest(file="x.py", pass1=p1, pass2=p2, log_path=log)

    def test_score_out_of_range_raises(self):
        log = self._tmp()
        p1 = _perfect_pass(4)
        p2 = _perfect_pass(4)
        p2["clarity"] = 7
        with self.assertRaises(ValueError):
            ingest(file="x.py", pass1=p1, pass2=p2, log_path=log)

    def test_validate_scores_missing_axis(self):
        p = {"clarity": 4}
        with self.assertRaises(ValueError):
            validate_scores(p, _AXES)

    def test_validate_scores_rejects_bool(self):
        p = {a: 4 for a in _AXES}
        p["clarity"] = True  # bool is int subclass — must still reject
        with self.assertRaises(ValueError):
            validate_scores(p, _AXES)

    def test_path_normalization_backslashes(self):
        log = self._tmp()
        rec = ingest(
            file="x\\y\\z.py",
            pass1=_perfect_pass(4),
            pass2=_perfect_pass(4),
            log_path=log,
        )
        self.assertEqual(rec["file"], "x/y/z.py")


class Reader(unittest.TestCase):
    def _tmp(self) -> Path:
        d = tempfile.mkdtemp(prefix="lich-reader-")
        return Path(d) / "kappa-log.jsonl"

    def test_latest_for_no_log(self):
        log = self._tmp()  # file does not exist yet
        self.assertIsNone(latest_for("x.py", log_path=log))

    def test_latest_for_returns_most_recent(self):
        log = self._tmp()
        # Write two records for same file with increasing ts.
        with open(log, "w", encoding="utf-8") as w:
            w.write(json.dumps({"file": "a.py", "ts": "2026-01-01T00:00:00Z", "mean_score": 3.0}) + "\n")
            w.write(json.dumps({"file": "a.py", "ts": "2026-04-01T00:00:00Z", "mean_score": 4.2}) + "\n")
            w.write(json.dumps({"file": "b.py", "ts": "2026-03-01T00:00:00Z", "mean_score": 2.5}) + "\n")
        rec = latest_for("a.py", log_path=log)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["mean_score"], 4.2)

    def test_latest_for_missing_file(self):
        log = self._tmp()
        with open(log, "w", encoding="utf-8") as w:
            w.write(json.dumps({"file": "a.py", "ts": "2026-01-01T00:00:00Z"}) + "\n")
        self.assertIsNone(latest_for("nope.py", log_path=log))

    def test_all_files_with_scores(self):
        log = self._tmp()
        with open(log, "w", encoding="utf-8") as w:
            w.write(json.dumps({"file": "a.py", "ts": "2026-01-01T00:00:00Z"}) + "\n")
            w.write(json.dumps({"file": "b.py", "ts": "2026-01-02T00:00:00Z"}) + "\n")
            w.write(json.dumps({"file": "a.py", "ts": "2026-01-03T00:00:00Z"}) + "\n")
        self.assertEqual(all_files_with_scores(log_path=log), {"a.py", "b.py"})

    def test_latest_for_windows_path_normalizes(self):
        log = self._tmp()
        with open(log, "w", encoding="utf-8") as w:
            w.write(json.dumps({"file": "x/y.py", "ts": "2026-01-01T00:00:00Z"}) + "\n")
        rec = latest_for("x\\y.py", log_path=log)
        self.assertIsNotNone(rec)


class Integration(unittest.TestCase):
    """Ingest-then-read round trip with an axis disagreement that surfaces unstable."""

    def test_ingest_then_reader_roundtrip(self):
        d = tempfile.mkdtemp(prefix="lich-integration-")
        log = Path(d) / "kappa-log.jsonl"

        p1 = _perfect_pass(4)
        p1["clarity"] = 1  # strong disagreement on one axis
        p2 = _perfect_pass(4)
        p2["clarity"] = 5

        rec = ingest(
            file="tests/fixtures/quality-ladder/high_level.py",
            pass1=p1,
            pass2=p2,
            log_path=log,
        )
        self.assertIn("clarity", rec["unstable_axes"])
        self.assertTrue(rec["needs_opus_adjudication"])

        read_back = latest_for(
            "tests/fixtures/quality-ladder/high_level.py",
            log_path=log,
        )
        self.assertIsNotNone(read_back)
        self.assertEqual(read_back["unstable_axes"], ["clarity"])
        self.assertTrue(read_back["kappa"]["clarity"]["unstable"])
        self.assertFalse(read_back["kappa"]["simplicity"]["unstable"])


if __name__ == "__main__":
    unittest.main()
