"""M7 score ingestion: validate, compose kappa, append to kappa-log.jsonl.

This layer receives two judge passes (pass1 and pass2 — each a
{axis: score} dict) from a Claude subagent that runs outside this code.
It never spawns the judge. Its contracts:

  1. Validate the score shape against rubric-v1.json.
  2. Compute the per-axis Kappa proxy (see kappa.py module docstring).
  3. Append exactly one JSONL record to plugins/lich-rubric/state/kappa-log.jsonl.
  4. Report unstable axes and Opus-adjudication flags — never average away.

Zero external deps; stdlib only.

CLI:
    python plugins/lich-rubric/scripts/score_ingest.py \\
        --file path/to/file.py \\
        --pass1 '{"clarity":4,"correctness_at_glance":4,...}' \\
        --pass2 '{"clarity":4,"correctness_at_glance":3,...}' \\
        [--judge claude-sonnet-4-6]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from kappa import (  # noqa: E402
    compose_kappa,
    mean_score,
    needs_opus_adjudication,
    unstable_axes,
)

# Repo-root sys.path shim for shared/learnings.py (advisory Gauss log).
_SHARED = Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import learnings as _learnings  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover — advisory
    _learnings = None

_REPO_ROOT = _SCRIPTS_DIR.parents[2]
RUBRIC_PATH = _REPO_ROOT / "plugins" / "lich-rubric" / "config" / "rubric-v1.json"
LOG_PATH = _REPO_ROOT / "plugins" / "lich-rubric" / "state" / "kappa-log.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_path(p: str) -> str:
    return p.replace("\\", "/")


def load_rubric(rubric_path: Optional[Path] = None) -> dict:
    """Read rubric-v1.json. Returns the parsed dict with axes[] etc."""
    path = rubric_path or RUBRIC_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_scores(scores: dict, axes: list) -> None:
    """Raise ValueError if scores is missing axes, has extra keys, or any
    score is outside [1, 5]. Does not mutate.
    """
    if not isinstance(scores, dict):
        raise ValueError(f"scores must be dict; got {type(scores).__name__}")
    expected = set(axes)
    got = set(scores.keys())
    missing = expected - got
    extra = got - expected
    if missing:
        raise ValueError(f"missing axes: {sorted(missing)}")
    if extra:
        raise ValueError(f"unknown axes: {sorted(extra)}")
    for axis, s in scores.items():
        if not isinstance(s, int) or isinstance(s, bool):
            raise ValueError(f"axis '{axis}' score must be int; got {type(s).__name__}")
        if s < 1 or s > 5:
            raise ValueError(f"axis '{axis}' score {s} outside [1, 5]")


def ingest(
    file: str,
    pass1: dict,
    pass2: dict,
    judge_model: str = "claude-sonnet-4-6",
    rubric_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
) -> dict:
    """Validate, compute kappa, and append one JSONL record. Returns the record."""
    rubric = load_rubric(rubric_path)
    axes = [a["id"] for a in rubric["axes"]]
    scale_max = rubric.get("scoring_scale", {}).get("max", 5)
    unstable_threshold = rubric.get("kappa", {}).get("unstable_threshold", 0.4)
    delta_threshold = rubric.get("position_swap", {}).get("escalate_if_delta_gte", 1.5)
    rubric_version = rubric.get("rubric_version", "unknown")

    validate_scores(pass1, axes)
    validate_scores(pass2, axes)

    kappa = compose_kappa(
        pass1, pass2, axes,
        scale_max=scale_max,
        unstable_threshold=unstable_threshold,
    )

    record = {
        "ts": _now_iso(),
        "file": _normalize_path(file),
        "rubric_version": rubric_version,
        "judge_model": judge_model,
        "pass1": dict(pass1),
        "pass2": dict(pass2),
        "kappa": kappa,
        "mean_score": mean_score(pass1, pass2),
        "unstable_axes": unstable_axes(kappa),
        "needs_opus_adjudication": needs_opus_adjudication(kappa, delta_threshold),
    }

    out_path = log_path or LOG_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as w:
        w.write(json.dumps(record) + "\n")

    # Gauss Accumulation — unstable axes are re-rubric candidates.
    if record["unstable_axes"] and _learnings is not None:
        try:
            kappa_values = {k: v.get("kappa") for k, v in kappa.items()}
            _learnings.safe_emit(
                plugin="lich-rubric",
                code="F13",
                axis=",".join(record["unstable_axes"]),
                hypothesis=f"axes {record['unstable_axes']} unstable",
                outcome=f"Kappa={kappa_values}",
                counter="re-rubric or drop axis",
            )
        except Exception:
            pass

    return record


def main() -> int:
    p = argparse.ArgumentParser(prog="lich-rubric-score-ingest")
    p.add_argument("--file", required=True, help="path of the file under review")
    p.add_argument("--pass1", required=True, help="JSON dict of pass-1 scores")
    p.add_argument("--pass2", required=True, help="JSON dict of pass-2 scores")
    p.add_argument("--judge", default="claude-sonnet-4-6", help="judge model id")
    p.add_argument("--rubric", type=Path, default=None, help="override rubric path")
    p.add_argument("--log", type=Path, default=None, help="override log path")
    args = p.parse_args()

    try:
        pass1 = json.loads(args.pass1)
        pass2 = json.loads(args.pass2)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON in --pass1/--pass2: {e}"}))
        return 1

    try:
        rec = ingest(
            file=args.file,
            pass1=pass1,
            pass2=pass2,
            judge_model=args.judge,
            rubric_path=args.rubric,
            log_path=args.log,
        )
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    summary = {
        "file": rec["file"],
        "mean_score": rec["mean_score"],
        "unstable_axes": rec["unstable_axes"],
        "needs_opus_adjudication": rec["needs_opus_adjudication"],
        "logged_to": str(args.log or LOG_PATH),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
