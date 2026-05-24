"""Verdict composer.

Reads M1 flags (plugins/lich-core/state/review-flags.jsonl) and M5 runs
(plugins/lich-sandbox/state/run-log.jsonl), groups by file, applies the
verdict bar from rules.py, and appends per-file records to
plugins/lich-verdict/state/verdict.jsonl.

CLI:
    python plugins/lich-verdict/scripts/compose.py
    python plugins/lich-verdict/scripts/compose.py --file tests/fixtures/quality-ladder/bad.py

Advisory only. Never mutates upstream state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parents[2]
sys.path.insert(0, str(_SCRIPTS_DIR))

# Make `shared/learnings.py` importable via repo-root sys.path shim.
_SHARED = _REPO_ROOT / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from rules import compose  # noqa: E402

try:
    import learnings as _learnings  # noqa: E402
except Exception:  # pragma: no cover — learnings is advisory
    _learnings = None


def _emit_disagreement_learning(verdict_obj) -> None:
    """Detect cross-engine disagreement and append an F11 Gauss entry.

    Disagreement = at least one engine demands FAIL while at least one
    demands DEPLOY (HOLD in the middle is not an alarm — that's a
    conservative disagreement, which the compose rules already handle).
    """
    if _learnings is None:
        return
    demands = {e.engine: e.demands for e in verdict_obj.engines
               if e.status == "ran"}
    if "FAIL" in demands.values() and "DEPLOY" in demands.values():
        m1 = demands.get("M1", "n/a")
        m7 = demands.get("M7", "n/a")
        _learnings.safe_emit(
            plugin="lich-verdict",
            code="F11",
            axis="cross-engine-disagreement",
            hypothesis="engines disagree on final verdict",
            outcome=f"M1={m1} M5={demands.get('M5','n/a')} "
                    f"M7={m7} final={verdict_obj.verdict}",
            counter="adjudicate via Opus",
        )


_DEFAULT_M1 = _REPO_ROOT / "plugins" / "lich-core" / "state" / "review-flags.jsonl"
_DEFAULT_M5 = _REPO_ROOT / "plugins" / "lich-sandbox" / "state" / "run-log.jsonl"
_DEFAULT_OUT = _REPO_ROOT / "plugins" / "lich-verdict" / "state" / "verdict.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _group_by_file(records: list[dict], *, m5: bool = False) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if m5:
            f = (r.get("flag_ref") or {}).get("file", "<unknown>")
        else:
            f = r.get("file", "<unknown>")
        grouped[f].append(r)
    return dict(grouped)


def run(
    m1_path: Path = _DEFAULT_M1,
    m5_path: Path = _DEFAULT_M5,
    out_path: Path = _DEFAULT_OUT,
    file_filter: list[str] | None = None,
) -> dict:
    m1 = _load_jsonl(m1_path)
    m5 = _load_jsonl(m5_path)

    m1_by_file = _group_by_file(m1)
    m5_by_file = _group_by_file(m5, m5=True)

    if file_filter:
        # Explicit list wins — emit one record per requested file, even if
        # both engines are empty for it (that's the clean-file DEPLOY path).
        files = set(file_filter)
    else:
        files = set(m1_by_file) | set(m5_by_file)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "files": 0,
        "deploy": 0,
        "hold": 0,
        "fail": 0,
        "written_to": str(out_path),
    }

    with open(out_path, "a", encoding="utf-8") as w:
        for f in sorted(files):
            v = compose(
                file=f,
                m1_flags=m1_by_file.get(f, []),
                m5_runs=m5_by_file.get(f, []),
            )
            w.write(json.dumps(asdict(v)) + "\n")
            summary["files"] += 1
            summary[v.verdict.lower()] += 1
            # Advisory — Gauss Accumulation, never raises.
            try:
                _emit_disagreement_learning(v)
            except Exception:
                pass
            # Advisory event-bus publish. Bus failures never block the
            # verdict write — brand invariant #7 is observability, not
            # orchestration.
            try:
                from events.bus import publish as _publish  # noqa: E402
                _publish("lich.review.completed", {
                    "file": v.file,
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                    "engines_ran": [e.engine for e in v.engines
                                     if e.status == "ran"],
                }, source="lich-verdict")
            except Exception:
                pass

    return summary


def main() -> int:
    p = argparse.ArgumentParser(prog="lich-verdict-compose")
    p.add_argument("--m1", type=Path, default=_DEFAULT_M1)
    p.add_argument("--m5", type=Path, default=_DEFAULT_M5)
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--file", action="append", default=None,
                   help="Emit a verdict for this file (repeatable). When given, "
                        "clean files with no engine records still get a DEPLOY "
                        "record. When omitted, composes over every file seen in "
                        "the state logs.")
    args = p.parse_args()

    summary = run(args.m1, args.m5, args.out, args.file)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
