"""Verdict bar as structured data.

Threshold math from CLAUDE.md § Verdict bar, applied as pure functions over
per-engine inputs. No I/O here — compose.py handles reading/writing.

Contract invariants:
  * Confirmed M5 runtime failure is a hard FAIL trigger (CLAUDE.md §5).
    Confirmed bugs are facts, not probabilities; they do not average with
    rubric scores.
  * M5 `platform-unsupported` is honest, not a failure mode. Per §2, it
    degrades M5 to DEPLOY-with-reduced-confidence; M1 alone drives the
    runtime-failure judgment.
  * Missing engines (M6 posteriors, M7 rubric — v2 scope gap) annotate
    verdicts as `preliminary`. They never block DEPLOY; the scope gap is
    documented, not silently pretended-ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional


Severity = Literal["CRITICAL", "HIGH", "MED", "LOW"]
VerdictLevel = Literal["DEPLOY", "HOLD", "FAIL"]
EngineStatus = Literal["ran", "unsupported", "not-evaluated", "error"]


_LEVEL_ORDER = {"DEPLOY": 0, "HOLD": 1, "FAIL": 2}


def _worst(a: VerdictLevel, b: VerdictLevel) -> VerdictLevel:
    return a if _LEVEL_ORDER[a] >= _LEVEL_ORDER[b] else b


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class EngineResult:
    engine: str
    status: EngineStatus
    demands: VerdictLevel
    reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def evaluate_m1(flags: Iterable[dict]) -> EngineResult:
    flags = list(flags)
    counts = {"CRITICAL": 0, "HIGH": 0, "MED": 0, "LOW": 0}
    for f in flags:
        sev = f.get("severity", "MED")
        counts[sev] = counts.get(sev, 0) + 1
    data = {"counts": counts, "total": len(flags)}

    if not flags:
        return EngineResult("M1", "ran", "DEPLOY", ["no flags"], data)

    if counts["CRITICAL"] > 0 or counts["HIGH"] >= 3:
        reasons = []
        if counts["CRITICAL"] > 0:
            reasons.append(f"{counts['CRITICAL']} CRITICAL flag(s)")
        if counts["HIGH"] >= 3:
            reasons.append(f"{counts['HIGH']} HIGH flag(s) (>= 3 threshold)")
        return EngineResult("M1", "ran", "FAIL", reasons, data)

    if 1 <= counts["HIGH"] <= 2:
        return EngineResult("M1", "ran", "HOLD",
                             [f"{counts['HIGH']} HIGH flag(s) (1-2 threshold)"], data)

    return EngineResult("M1", "ran", "DEPLOY",
                         ["all flags below HIGH severity"], data)


def evaluate_m5(runs: Iterable[dict]) -> EngineResult:
    runs = list(runs)
    if not runs:
        return EngineResult("M5", "not-evaluated", "DEPLOY",
                             ["no sandbox runs recorded"])

    counts: dict[str, int] = {}
    for r in runs:
        s = r.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    data = {"counts": counts, "total": len(runs)}

    confirmed = counts.get("confirmed-bug", 0)
    if confirmed > 0:
        return EngineResult("M5", "ran", "FAIL",
                             [f"{confirmed} confirmed runtime failure(s) with concrete witness"],
                             data)

    timeouts = counts.get("timeout-without-confirmation", 0)
    if timeouts > 0:
        return EngineResult("M5", "ran", "HOLD",
                             [f"{timeouts} timeout(s) without confirmation"], data)

    unsupported = counts.get("platform-unsupported", 0)
    if unsupported == len(runs):
        return EngineResult("M5", "unsupported", "DEPLOY",
                             ["platform-unsupported (no resource.setrlimit; WSL absent) — "
                              "M1-only runtime-failure judgment per CLAUDE.md §2"],
                             data)

    sandbox_errors = counts.get("sandbox-error", 0)
    if sandbox_errors > 0:
        return EngineResult("M5", "ran", "HOLD",
                             [f"{sandbox_errors} sandbox-error(s) — infra issue, not a finding"],
                             data)

    return EngineResult("M5", "ran", "DEPLOY",
                         ["no runtime failure confirmed in sandbox"], data)


def evaluate_m6(posteriors: Optional[dict] = None, *,
                 flags: Optional[list] = None,
                 dev_id: str = "default") -> EngineResult:
    if posteriors is None and flags:
        try:
            import sys
            _pref = Path(__file__).resolve().parents[3] / "plugins" / "lich-preference" / "scripts"
            if str(_pref) not in sys.path:
                sys.path.insert(0, str(_pref))
            from reader import evaluate as pref_evaluate
            posteriors = pref_evaluate(flags, dev_id)
        except Exception:
            pass

    if posteriors is None:
        return EngineResult("M6", "not-evaluated", "DEPLOY",
                             ["Bayesian preference engine not yet observed for this (dev, rule) set"])

    surfaced = posteriors.get("surfaced_count", 0)
    accept_majority = posteriors.get("accept_majority_count", 0)
    borderline = posteriors.get("borderline_count", 0)
    if surfaced == 0:
        return EngineResult("M6", "ran", "DEPLOY",
                             ["no flags surfaced — nothing to react to"], posteriors)
    accept_pct = accept_majority / surfaced
    borderline_pct = (accept_majority + borderline) / surfaced
    if accept_pct >= 0.8:
        return EngineResult("M6", "ran", "DEPLOY",
                             [f">=80% surfaced have posterior mean > 0.5 ({accept_pct:.0%})"], posteriors)
    if borderline_pct >= 0.5:
        return EngineResult("M6", "ran", "HOLD",
                             [f"{borderline_pct:.0%} surfaced have posterior > 0.3"], posteriors)
    return EngineResult("M6", "ran", "HOLD",
                         [f"only {accept_pct:.0%} posterior-majority accept"], posteriors)


def evaluate_m7(scores: Optional[dict] = None, *, file: Optional[str] = None) -> EngineResult:
    # v2: read from kappa-log.jsonl if no scores passed directly
    if scores is None and file is not None:
        # Lazy import to avoid cross-plugin coupling at module-load time
        try:
            import sys
            _rubric_scripts = Path(__file__).resolve().parents[3] / "plugins" / "lich-rubric" / "scripts"
            if str(_rubric_scripts) not in sys.path:
                sys.path.insert(0, str(_rubric_scripts))
            from reader import latest_for
            rec = latest_for(file)
            if rec:
                scores = rec
        except Exception:
            pass

    if scores is None:
        return EngineResult("M7", "not-evaluated", "DEPLOY",
                             ["Zheng pairwise rubric not yet scored for this file"])

    # Compute demand per CLAUDE.md verdict bar:
    kappa = scores.get("kappa", {})
    unstable = scores.get("unstable_axes", [])
    axes_means = {k: (v["s1"] + v["s2"]) / 2 for k, v in kappa.items()}
    data = {"mean_score": scores.get("mean_score"), "unstable_axes": unstable,
            "axes_means": axes_means}

    # FAIL: any axis ≤ 2 OR > 2 axes < 3
    if any(m <= 2 for m in axes_means.values()):
        return EngineResult("M7", "ran", "FAIL",
                             [f"axis score <= 2: {[a for a,m in axes_means.items() if m<=2]}"],
                             data)
    low = [a for a, m in axes_means.items() if m < 3]
    if len(low) > 2:
        return EngineResult("M7", "ran", "FAIL",
                             [f">2 axes < 3: {low}"], data)

    # HOLD: any axis < 3.5 OR Kappa < 0.4 (unstable)
    under_threshold = [a for a, m in axes_means.items() if m < 3.5]
    if under_threshold or unstable:
        reasons = []
        if under_threshold:
            reasons.append(f"axes < 3.5: {under_threshold}")
        if unstable:
            reasons.append(f"unstable Kappa (< 0.4): {unstable}")
        return EngineResult("M7", "ran", "HOLD", reasons, data)

    # DEPLOY: all axes >= 3.5 AND all stable
    return EngineResult("M7", "ran", "DEPLOY",
                         ["all 5 axes >= 3.5 and Kappa >= 0.4"], data)


@dataclass
class Verdict:
    verdict: VerdictLevel
    confidence: Literal["high", "preliminary", "reduced"]
    file: str
    engines: list[EngineResult]
    reasons: list[str]
    caveats: list[str]
    ts: str


def compose(
    file: str,
    m1_flags: Iterable[dict],
    m5_runs: Iterable[dict],
    m6_posteriors: Optional[dict] = None,
    m7_scores: Optional[dict] = None,
) -> Verdict:
    import os
    m1_list = list(m1_flags)
    dev_id = os.environ.get("LICH_DEV_ID", "default")
    engines = [
        evaluate_m1(m1_list),
        evaluate_m5(m5_runs),
        evaluate_m6(m6_posteriors, flags=m1_list, dev_id=dev_id),
        evaluate_m7(m7_scores, file=file),
    ]

    final: VerdictLevel = "DEPLOY"
    reasons: list[str] = []
    for e in engines:
        final = _worst(final, e.demands)
        reasons.extend(f"[{e.engine}] {r}" for r in e.reasons)

    caveats: list[str] = []
    not_eval = [e for e in engines if e.status == "not-evaluated"]
    unsupported = [e for e in engines if e.status == "unsupported"]

    if not_eval or unsupported:
        if final == "DEPLOY":
            confidence = "preliminary"
        else:
            confidence = "reduced"
        for e in not_eval:
            caveats.append(f"{e.engine}: not-evaluated — verdict preliminary")
        for e in unsupported:
            caveats.append(f"{e.engine}: unsupported on this platform — reduced confidence")
    else:
        confidence = "high"

    return Verdict(
        verdict=final,
        confidence=confidence,
        file=file,
        engines=engines,
        reasons=reasons,
        caveats=caveats,
        ts=_now_iso(),
    )
