"""Per-axis agreement metric for the M7 Zheng Pairwise Rubric.

v2 Kappa simplification
-----------------------
True Cohen's Kappa requires N items rated by 2 raters with a chance-corrected
agreement formula. In M7 v2, the unit under review is a single file scored
twice (position-swapped) on 5 axes, so we have 1 item rated twice on 5
independent dimensions. Per-axis we have a pair of integer scores (s1, s2)
on a 1-5 scale; the v2 agreement proxy is:

    agreement = 1 - |s1 - s2| / (scale_max - 1)

which maps identical scores to 1.0 and maximally disagreeing scores (1 vs 5)
to 0.0. The rubric-v1.json contract threshold (unstable_threshold = 0.4)
was calibrated against this proxy, not against classical Cohen's Kappa.

Phase 3 replaces this with real Kappa once a corpus of ≥ 20 rated files
exists across reviewers; the threshold will be recalibrated with the
switch. Until then: honest numbers means honest labels — this is a Kappa
*proxy*, documented as such, never renamed or silently averaged away.
"""

from __future__ import annotations

from typing import Iterable


def per_axis_agreement(s1: int, s2: int, scale_max: int = 5) -> float:
    """Return the [0, 1] agreement for two scores on a 1..scale_max scale.

    Raises ValueError if either score is outside [1, scale_max].
    """
    if not isinstance(s1, int) or not isinstance(s2, int):
        raise ValueError(f"scores must be int; got {type(s1).__name__}, {type(s2).__name__}")
    if s1 < 1 or s1 > scale_max:
        raise ValueError(f"s1={s1} outside [1, {scale_max}]")
    if s2 < 1 or s2 > scale_max:
        raise ValueError(f"s2={s2} outside [1, {scale_max}]")
    if scale_max < 2:
        raise ValueError(f"scale_max must be >= 2; got {scale_max}")
    return 1.0 - abs(s1 - s2) / (scale_max - 1)


def compose_kappa(
    pass1: dict,
    pass2: dict,
    axes: Iterable[str],
    scale_max: int = 5,
    unstable_threshold: float = 0.4,
) -> dict:
    """Compose per-axis kappa proxy dict.

    Returns:
        {axis: {"s1": int, "s2": int, "delta": int,
                "agreement": float, "unstable": bool}, ...}
    """
    out: dict = {}
    for axis in axes:
        if axis not in pass1:
            raise ValueError(f"pass1 missing axis '{axis}'")
        if axis not in pass2:
            raise ValueError(f"pass2 missing axis '{axis}'")
        s1 = pass1[axis]
        s2 = pass2[axis]
        agreement = per_axis_agreement(s1, s2, scale_max=scale_max)
        out[axis] = {
            "s1": s1,
            "s2": s2,
            "delta": s1 - s2,
            "agreement": agreement,
            "unstable": agreement < unstable_threshold,
        }
    return out


def needs_opus_adjudication(kappa: dict, delta_threshold: float = 1.5) -> bool:
    """True iff any axis has |delta| >= delta_threshold."""
    for _axis, info in kappa.items():
        if abs(info["delta"]) >= delta_threshold:
            return True
    return False


def mean_score(pass1: dict, pass2: dict) -> float:
    """Mean of all scores across both passes. Honest-numbers caveat: this
    is a convenience aggregate; it never substitutes for per-axis reporting.
    """
    all_scores = list(pass1.values()) + list(pass2.values())
    if not all_scores:
        raise ValueError("no scores to average")
    return sum(all_scores) / len(all_scores)


def unstable_axes(kappa: dict) -> list:
    """Return axis names flagged unstable, in input iteration order."""
    return [axis for axis, info in kappa.items() if info["unstable"]]
