"""Tests for classical Cohen's Kappa (corpus-level M7 reliability).

Covers:
    * Sanity: perfect agreement -> 1, perfect disagreement -> ~-0.67, random -> near 0.
    * Edge: 1 - p_e = 0 -> NaN (not crash, not 1.0).
    * Edge: empty rater lists -> NaN.
    * Validation: out-of-range scores, bool rejection, length mismatch.
    * per_axis_kappa: carries threshold flagging, marks NaN as unstable.
    * corpus_kappa: aggregates across files, skips null placeholders cleanly.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from kappa_classical import (  # noqa: E402
    cohen_kappa,
    corpus_kappa,
    per_axis_kappa,
)


class CohenKappaSanity(unittest.TestCase):
    def test_perfect_agreement_with_varied_scores(self):
        r1 = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
        self.assertEqual(cohen_kappa(r1, list(r1)), 1.0)

    def test_perfect_disagreement_5class(self):
        # One rater all 1, other all 5. p_o=0; p_e has two non-zero terms
        # (1/1 and 5/1 marginals, but each only hits 1 side) -> p_e = 0.
        # With p_e = 0, kappa = (0 - 0) / (1 - 0) = 0.  Classic textbook case.
        r1 = [1] * 5
        r2 = [5] * 5
        self.assertEqual(cohen_kappa(r1, r2), 0.0)

    def test_perfect_disagreement_mixed_classes(self):
        # Mixed-class disagreement gives the expected negative value.
        # r1 = [1,1,1,5,5], r2 = [5,5,5,1,1]; p_o=0; marginals force p_e > 0.
        r1 = [1, 1, 1, 5, 5]
        r2 = [5, 5, 5, 1, 1]
        k = cohen_kappa(r1, r2)
        # p_e = (3/5)*(2/5) + (2/5)*(3/5) = 12/25 = 0.48
        # kappa = (0 - 0.48) / (1 - 0.48) = -0.923...
        self.assertAlmostEqual(k, -0.48 / 0.52, places=6)
        self.assertLess(k, -0.5)

    def test_random_agreement_near_zero(self):
        # Deterministically chosen "noisy" pair; kappa should be small.
        r1 = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2]
        r2 = [2, 3, 4, 5, 1, 3, 1, 4, 2, 1, 5, 3]
        k = cohen_kappa(r1, r2)
        self.assertLess(abs(k), 0.35)

    def test_partial_agreement(self):
        # 8/10 match. p_o=0.8. Varied marginals yield p_e ~ 0.2 -> kappa ~ 0.75.
        r1 = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
        r2 = [1, 2, 3, 4, 5, 1, 2, 3, 1, 1]
        k = cohen_kappa(r1, r2)
        self.assertGreater(k, 0.6)
        self.assertLess(k, 0.85)

    def test_range_is_bounded(self):
        # Any valid input must land in [-1, 1] (or NaN).
        cases = [
            ([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]),
            ([3, 3, 3, 3, 3], [3, 3, 3, 3, 4]),
            ([1, 5, 1, 5, 1], [1, 5, 1, 5, 1]),
        ]
        for r1, r2 in cases:
            k = cohen_kappa(r1, r2)
            if not math.isnan(k):
                self.assertGreaterEqual(k, -1.0)
                self.assertLessEqual(k, 1.0)


class CohenKappaEdges(unittest.TestCase):
    def test_empty_lists_return_nan(self):
        k = cohen_kappa([], [])
        self.assertTrue(math.isnan(k))

    def test_chance_agreement_one_means_nan(self):
        # Both raters gave every item the same single class: p_o = 1, p_e = 1.
        # Classical Kappa is undefined; we return NaN, not 1.0.
        r1 = [3] * 10
        r2 = [3] * 10
        k = cohen_kappa(r1, r2)
        self.assertTrue(math.isnan(k))

    def test_both_raters_collapsed_but_different_classes(self):
        # r1 all 2s, r2 all 4s -> p_o=0, p_e = (1.0 on class 2)*(0 on 2) +
        # (0 on 4)*(1.0 on 4) = 0. kappa = (0-0)/(1-0) = 0.
        r1 = [2] * 6
        r2 = [4] * 6
        self.assertEqual(cohen_kappa(r1, r2), 0.0)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            cohen_kappa([1, 2, 3], [1, 2])

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            cohen_kappa([1, 2, 6], [1, 2, 3])
        with self.assertRaises(ValueError):
            cohen_kappa([1, 2, 3], [0, 2, 3])

    def test_bool_rejected(self):
        with self.assertRaises(ValueError):
            cohen_kappa([True, False], [1, 2])  # type: ignore[list-item]

    def test_non_int_rejected(self):
        with self.assertRaises(ValueError):
            cohen_kappa([1.5, 2, 3], [1, 2, 3])  # type: ignore[list-item]

    def test_custom_scale(self):
        # 3-class scale, perfect agreement.
        k = cohen_kappa([1, 2, 3, 1, 2], [1, 2, 3, 1, 2], scale_min=1, scale_max=3)
        self.assertEqual(k, 1.0)

    def test_bad_scale_raises(self):
        with self.assertRaises(ValueError):
            cohen_kappa([1], [1], scale_min=5, scale_max=1)


class PerAxisKappa(unittest.TestCase):
    def test_stable_axis(self):
        info = per_axis_kappa("clarity", [4, 4, 3, 5, 4], [4, 4, 3, 5, 3])
        # 4/5 match; p_e is the chance term. kappa should be comfortably > 0.4.
        self.assertGreater(info["kappa"], 0.4)
        self.assertFalse(info["unstable"])
        self.assertEqual(info["n_items"], 5)
        self.assertAlmostEqual(info["agreement"], 0.8)
        self.assertEqual(info["axis"], "clarity")

    def test_unstable_axis_flagged(self):
        info = per_axis_kappa("simplicity", [1, 1, 5, 5], [5, 5, 1, 1])
        self.assertLess(info["kappa"], 0.4)
        self.assertTrue(info["unstable"])

    def test_nan_kappa_marked_unstable(self):
        # Everyone collapsed on one class -> NaN -> flagged unstable.
        info = per_axis_kappa("idiom_fit", [3, 3, 3], [3, 3, 3])
        self.assertTrue(math.isnan(info["kappa"]))
        self.assertTrue(info["unstable"])

    def test_empty_items_marked_unstable_nan(self):
        info = per_axis_kappa("testability", [], [])
        self.assertTrue(math.isnan(info["kappa"]))
        self.assertTrue(info["unstable"])
        self.assertEqual(info["n_items"], 0)


class CorpusKappa(unittest.TestCase):
    _AXES = [
        "clarity",
        "correctness_at_glance",
        "idiom_fit",
        "testability",
        "simplicity",
    ]

    def _band_pass(self, score: int) -> dict:
        return {a: score for a in self._AXES}

    def test_synthetic_12_file_corpus_stable(self):
        # 4 bad (mostly 2s), 4 mid (3s), 4 high (4-5s). Pass1/pass2 mostly agree.
        scores = {}
        for i in range(1, 5):
            # Bad: pass1=2s, pass2=2s except one axis off by 1.
            p1 = self._band_pass(2)
            p2 = self._band_pass(2)
            p2["clarity"] = 3  # small noise
            scores[f"b0{i}.py"] = {"pass1": p1, "pass2": p2}
        for i in range(1, 5):
            p1 = self._band_pass(3)
            p2 = self._band_pass(3)
            p2["simplicity"] = 4  # small noise
            scores[f"m0{i}.py"] = {"pass1": p1, "pass2": p2}
        for i in range(1, 5):
            p1 = self._band_pass(5)
            p2 = self._band_pass(5)
            p2["idiom_fit"] = 4  # small noise
            scores[f"h0{i}.py"] = {"pass1": p1, "pass2": p2}

        result = corpus_kappa(scores, self._AXES)
        self.assertEqual(result["n_files_total"], 12)
        self.assertEqual(result["n_files_complete"], 12)
        # correctness_at_glance is identical pass1 == pass2 on every file and
        # the distribution across bands is spread (4x 2s, 4x 3s, 4x 5s) -> p_o=1,
        # p_e<1, so kappa is defined and equals 1.0.
        ca = result["axes"]["correctness_at_glance"]
        self.assertFalse(math.isnan(ca["kappa"]))
        self.assertEqual(ca["kappa"], 1.0)
        self.assertFalse(ca["unstable"])
        # clarity has one disagreement in 12 -> kappa < 1 but still comfortably stable.
        cl = result["axes"]["clarity"]
        self.assertFalse(math.isnan(cl["kappa"]))
        self.assertGreater(cl["kappa"], 0.4)

    def test_null_placeholders_skipped(self):
        # 2 filled, 1 unfilled (pass1 is None). Kappa computed over 2 items.
        scores = {
            "h01.py": {"pass1": self._band_pass(5), "pass2": self._band_pass(5)},
            "h02.py": {"pass1": self._band_pass(4), "pass2": self._band_pass(4)},
            "h03.py": {"pass1": None, "pass2": None},
        }
        result = corpus_kappa(scores, self._AXES)
        self.assertEqual(result["n_files_total"], 3)
        self.assertEqual(result["n_files_complete"], 2)
        for axis in self._AXES:
            self.assertEqual(result["axes"][axis]["n_items"], 2)
            self.assertIn("h03.py", result["axes"][axis]["skipped_files"])

    def test_axis_missing_in_one_pass_skipped(self):
        scores = {
            "a.py": {"pass1": self._band_pass(4), "pass2": self._band_pass(4)},
            "b.py": {
                "pass1": {"clarity": 3},  # only one axis filled
                "pass2": self._band_pass(3),
            },
        }
        result = corpus_kappa(scores, self._AXES)
        # For every axis except clarity, b.py is skipped.
        self.assertEqual(result["axes"]["clarity"]["n_items"], 2)
        for axis in self._AXES:
            if axis == "clarity":
                continue
            self.assertEqual(result["axes"][axis]["n_items"], 1)
            self.assertIn("b.py", result["axes"][axis]["skipped_files"])

    def test_threshold_flags_unstable_per_axis(self):
        # Every file disagrees maximally on simplicity; stable on clarity.
        scores = {}
        for i, band in enumerate([2, 2, 3, 3, 4, 4, 5, 5]):
            p1 = {a: band for a in self._AXES}
            p2 = {a: band for a in self._AXES}
            p1["simplicity"] = 1
            p2["simplicity"] = 5
            scores[f"f{i}.py"] = {"pass1": p1, "pass2": p2}
        result = corpus_kappa(scores, self._AXES)
        self.assertTrue(result["axes"]["simplicity"]["unstable"])


class IntegrationSmallCorpus(unittest.TestCase):
    """End-to-end: 12-item synthetic corpus touches per_axis and corpus_kappa."""

    def test_12_items_per_axis_direct(self):
        axis = "clarity"
        pass1 = [2, 2, 2, 2, 3, 3, 3, 3, 5, 5, 5, 5]
        pass2 = [2, 3, 2, 2, 3, 3, 4, 3, 5, 5, 4, 5]
        info = per_axis_kappa(axis, pass1, pass2)
        self.assertEqual(info["n_items"], 12)
        self.assertAlmostEqual(info["agreement"], 9 / 12)
        self.assertFalse(math.isnan(info["kappa"]))
        # With this level of agreement, kappa should comfortably be > 0.4.
        self.assertGreater(info["kappa"], 0.4)
        self.assertFalse(info["unstable"])


if __name__ == "__main__":
    unittest.main()
