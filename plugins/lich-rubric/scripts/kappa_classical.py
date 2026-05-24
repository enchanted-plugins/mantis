"""Classical Cohen's Kappa for multi-file M7 reliability.

v2 proxy (kappa.py) vs. classical (this module)
-----------------------------------------------
`kappa.py` computes a per-file, per-axis agreement proxy — 1 item rated twice
on 5 axes gives 5 independent pair-scores, which is what M7 uses on a live
review. That's the single-file default and it stays unchanged.

This module adds the classical formulation for the case where a corpus of
N files has been scored twice (pass1 / pass2) on each axis. When N is large
enough for chance-corrected agreement to be meaningful (typically N >= 12),
classical Cohen's Kappa is the correct metric. The rubric-v1.json
`unstable_threshold = 0.4` value was calibrated against the proxy; we keep
the same threshold here with a note that classical Kappa on small N is
high-variance and the flag is advisory.

Formula (recap, weighted and unweighted)
----------------------------------------
For two raters over N items each assigning a class c in {1..K}:

    p_o = (1/N) * sum_i [rater1_i == rater2_i]
    p_e = sum_c (marginal_1(c)/N) * (marginal_2(c)/N)
    kappa = (p_o - p_e) / (1 - p_e)

Edge cases:
    * N = 0 -> NaN (no data to report).
    * 1 - p_e = 0 (both raters placed everything in one class AND it was the
      same class, or both placed everything in one class but different
      ones) -> NaN. Classical Kappa is genuinely undefined here; we return
      NaN explicitly rather than a misleading 1.0 or 0.0. Caller decides.
    * Perfect agreement with denominator > 0 -> 1.0.
    * Perfect disagreement on a K=5 scale -> ~ -0.67 (e.g., one rater all 1s,
      other all 5s; p_o=0, p_e is small and positive -> large negative).

Contract
--------
* Stdlib only — no numpy.
* Pure functions — no file I/O except the CLI layer.
* NaN is represented as `float('nan')`. Callers `math.isnan` to check.
* Scores are integers on [scale_min, scale_max].
* Axes are strings; corpus_kappa iterates in the order the caller passes them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Iterable


def _validate_rater_lists(r1: list[int], r2: list[int], scale_min: int, scale_max: int) -> None:
    if len(r1) != len(r2):
        raise ValueError(f"rater lists differ in length: {len(r1)} vs {len(r2)}")
    if scale_max <= scale_min:
        raise ValueError(f"scale_max ({scale_max}) must exceed scale_min ({scale_min})")
    for idx, s in enumerate(r1):
        if not isinstance(s, int) or isinstance(s, bool):
            raise ValueError(f"rater1[{idx}]={s!r} is not an int")
        if s < scale_min or s > scale_max:
            raise ValueError(f"rater1[{idx}]={s} outside [{scale_min}, {scale_max}]")
    for idx, s in enumerate(r2):
        if not isinstance(s, int) or isinstance(s, bool):
            raise ValueError(f"rater2[{idx}]={s!r} is not an int")
        if s < scale_min or s > scale_max:
            raise ValueError(f"rater2[{idx}]={s} outside [{scale_min}, {scale_max}]")


def cohen_kappa(
    rater1: list[int],
    rater2: list[int],
    scale_min: int = 1,
    scale_max: int = 5,
) -> float:
    """Return classical Cohen's Kappa for two raters over N paired items.

    * N = 0 -> NaN (no items; Kappa undefined).
    * 1 - p_e = 0 -> NaN (chance agreement is perfect; Kappa undefined).
    * Otherwise returns a float in [-1, 1].
    """
    _validate_rater_lists(rater1, rater2, scale_min, scale_max)
    n = len(rater1)
    if n == 0:
        return float("nan")

    # Observed agreement.
    agree = sum(1 for a, b in zip(rater1, rater2) if a == b)
    p_o = agree / n

    # Expected agreement by chance, summed over all classes that appear.
    classes = range(scale_min, scale_max + 1)
    p_e = 0.0
    for c in classes:
        m1 = sum(1 for s in rater1 if s == c) / n
        m2 = sum(1 for s in rater2 if s == c) / n
        p_e += m1 * m2

    denom = 1.0 - p_e
    if denom == 0.0:
        # Genuine edge case: raters exhausted their distributions in a way
        # that makes chance agreement 1.0. Kappa is undefined; don't fake it.
        return float("nan")
    return (p_o - p_e) / denom


def per_axis_kappa(
    axis_name: str,
    pass1_scores: list[int],
    pass2_scores: list[int],
    scale_min: int = 1,
    scale_max: int = 5,
    unstable_threshold: float = 0.4,
) -> dict:
    """Kappa for one rubric axis across N items.

    Returns:
        {"axis": axis_name,
         "kappa": float|NaN,
         "n_items": int,
         "agreement": float (raw p_o, for context),
         "unstable": bool (True iff kappa < threshold; NaN counts as unstable)}
    """
    kappa = cohen_kappa(pass1_scores, pass2_scores, scale_min, scale_max)
    n = len(pass1_scores)
    p_o = (
        sum(1 for a, b in zip(pass1_scores, pass2_scores) if a == b) / n
        if n > 0
        else float("nan")
    )
    # NaN propagates as "we can't trust this axis" -> unstable.
    unstable = True if math.isnan(kappa) else kappa < unstable_threshold
    return {
        "axis": axis_name,
        "kappa": kappa,
        "n_items": n,
        "agreement": p_o,
        "unstable": unstable,
    }


def corpus_kappa(
    scores_by_file: dict,
    axes: Iterable[str],
    scale_min: int = 1,
    scale_max: int = 5,
    unstable_threshold: float = 0.4,
) -> dict:
    """Aggregate per-axis kappa across a corpus.

    Input shape:
        {file_name: {"pass1": {axis: int, ...}, "pass2": {axis: int, ...}}, ...}

    Files whose pass1 or pass2 is None, missing, or has a null axis score
    are skipped with the file name added to `skipped_files` per axis. This
    preserves the "null means unfilled placeholder" contract of corpus_ingest.

    Returns:
        {"axes": {axis: per_axis_dict, ...},
         "n_files_total": int,
         "n_files_complete": int (files with all axes scored twice)}
    """
    axes = list(axes)
    files = sorted(scores_by_file.keys())
    n_total = len(files)

    # For counting fully-complete files, a file is complete iff pass1 and
    # pass2 are dicts and every axis is an int on both sides.
    def _is_complete(record: dict) -> bool:
        p1 = record.get("pass1")
        p2 = record.get("pass2")
        if not isinstance(p1, dict) or not isinstance(p2, dict):
            return False
        for axis in axes:
            v1 = p1.get(axis)
            v2 = p2.get(axis)
            if not isinstance(v1, int) or isinstance(v1, bool):
                return False
            if not isinstance(v2, int) or isinstance(v2, bool):
                return False
        return True

    n_complete = sum(1 for f in files if _is_complete(scores_by_file[f]))

    out_axes: dict = {}
    for axis in axes:
        r1: list[int] = []
        r2: list[int] = []
        skipped: list[str] = []
        for f in files:
            rec = scores_by_file[f]
            p1 = rec.get("pass1") or {}
            p2 = rec.get("pass2") or {}
            v1 = p1.get(axis) if isinstance(p1, dict) else None
            v2 = p2.get(axis) if isinstance(p2, dict) else None
            if (
                not isinstance(v1, int) or isinstance(v1, bool) or
                not isinstance(v2, int) or isinstance(v2, bool)
            ):
                skipped.append(f)
                continue
            r1.append(v1)
            r2.append(v2)
        info = per_axis_kappa(
            axis, r1, r2,
            scale_min=scale_min,
            scale_max=scale_max,
            unstable_threshold=unstable_threshold,
        )
        info["skipped_files"] = skipped
        out_axes[axis] = info

    return {
        "axes": out_axes,
        "n_files_total": n_total,
        "n_files_complete": n_complete,
    }


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------


_DEFAULT_AXES = [
    "clarity",
    "correctness_at_glance",
    "idiom_fit",
    "testability",
    "simplicity",
]


def _nan_to_none(obj):
    """JSON-serialize float('nan') as null rather than the non-standard NaN token."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _nan_to_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_nan_to_none(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute classical Cohen's Kappa across a rubric corpus.",
    )
    parser.add_argument(
        "--corpus-scores",
        required=True,
        help="Path to a JSON file shaped {file: {pass1: {...}, pass2: {...}}}",
    )
    parser.add_argument(
        "--axes",
        nargs="*",
        default=_DEFAULT_AXES,
        help="Rubric axes to report (default: 5 v1 axes)",
    )
    parser.add_argument(
        "--unstable-threshold",
        type=float,
        default=0.4,
        help="Kappa below this is flagged unstable (default: 0.4)",
    )
    args = parser.parse_args(argv)

    with open(args.corpus_scores, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    # Accept both shapes:
    #   (a) scaffold emitted by corpus_ingest.py: {rubric_version, axes, files: {name: {pass1, pass2}}}
    #   (b) flat dict: {name: {pass1, pass2}}
    # The scaffold-with-null-placeholders is handled naturally by corpus_kappa's
    # null-skipping; we still emit a warning so the operator knows the corpus
    # is unfilled.
    if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
        scores = raw["files"]
    else:
        scores = raw

    if not isinstance(scores, dict) or not scores:
        print(
            json.dumps({"status": "bad-input", "hint": "Expected a non-empty dict."}),
            file=sys.stderr,
        )
        return 2

    # Any file missing both pass1 and pass2 keys entirely -> not scorable.
    has_any_pass = any(
        isinstance(v, dict) and ("pass1" in v or "pass2" in v)
        for v in scores.values()
    )
    if not has_any_pass:
        print(
            json.dumps({
                "status": "no-passes",
                "hint": "No file has pass1/pass2 keys; is this a different JSON shape?",
            }),
            file=sys.stderr,
        )
        return 2

    # Soft warning when every score is still null (scaffold unfilled).
    all_null = all(
        all(v is None for v in (rec.get("pass1") or {}).values())
        and all(v is None for v in (rec.get("pass2") or {}).values())
        for rec in scores.values()
        if isinstance(rec, dict)
    )
    if all_null:
        print(
            json.dumps({
                "status": "scaffold-unfilled",
                "hint": "All scores are null; fill pass1/pass2 with integers before trusting kappa.",
            }),
            file=sys.stderr,
        )

    result = corpus_kappa(
        scores, args.axes, unstable_threshold=args.unstable_threshold
    )
    print(json.dumps(_nan_to_none(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
