"""Offline corpus prep: scaffold a scores.json for human or LLM judging.

Given the rubric corpus directory (default: tests/fixtures/rubric-corpus/),
walk every `.py` file and emit a JSON scaffold with `null` placeholders for
pass1 and pass2 on each axis. The operator (or a subagent judge) fills in
the integer scores later; `kappa_classical.py` then consumes the filled file.

This script DOES NOT spawn any LLM calls. It is strictly an offline file
lister. Filling scores is a separate, deliberate step.

Scaffold shape
--------------
{
  "rubric_version": "1.0",
  "axes": ["clarity", "correctness_at_glance", "idiom_fit",
           "testability", "simplicity"],
  "files": {
    "b01.py": {
      "pass1": {"clarity": null, "correctness_at_glance": null, ...},
      "pass2": {"clarity": null, "correctness_at_glance": null, ...}
    },
    ...
  }
}

Contract
--------
* Zero runtime deps (stdlib only).
* Never overwrites an existing scaffold unless --force is passed — the
  most common operator accident is running this twice and nuking hours of
  hand-scoring.
* Loads axes from rubric-v1.json so a future rubric bump doesn't need a
  code edit here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_THIS = Path(__file__).resolve()
_PLUGIN_DIR = _THIS.parents[1]  # plugins/lich-rubric
_REPO_ROOT = _PLUGIN_DIR.parents[1]  # <repo>
_DEFAULT_CORPUS = _REPO_ROOT / "tests" / "fixtures" / "rubric-corpus"
_DEFAULT_OUT = _DEFAULT_CORPUS / "scores.json"
_RUBRIC_CONFIG = _PLUGIN_DIR / "config" / "rubric-v1.json"


def _load_axes() -> list[str]:
    with open(_RUBRIC_CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    return [a["id"] for a in cfg["axes"]]


def _empty_axis_pass(axes: list[str]) -> dict:
    # `None` -> JSON null. Operator fills each with an int on [1, 5].
    return {axis: None for axis in axes}


def build_scaffold(corpus_dir: Path, axes: list[str], rubric_version: str = "1.0") -> dict:
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"corpus dir not found: {corpus_dir}")
    py_files = sorted(p.name for p in corpus_dir.glob("*.py"))
    if not py_files:
        raise ValueError(f"no .py files in {corpus_dir}")

    files: dict = {}
    for name in py_files:
        files[name] = {
            "pass1": _empty_axis_pass(axes),
            "pass2": _empty_axis_pass(axes),
        }
    return {
        "rubric_version": rubric_version,
        "axes": axes,
        "corpus_dir": str(corpus_dir.relative_to(_REPO_ROOT)).replace("\\", "/"),
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a scores.json with null placeholders for the rubric corpus.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=_DEFAULT_CORPUS,
        help="Directory containing *.py corpus files (default: tests/fixtures/rubric-corpus)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="Output path for the scaffold JSON (default: <corpus-dir>/scores.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing scaffold (refuses by default to protect hand-scored files).",
    )
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        print(
            json.dumps({
                "status": "scaffold-exists",
                "path": str(args.out),
                "hint": "pass --force to overwrite; this file may contain hand-scored values.",
            }),
            file=sys.stderr,
        )
        return 1

    axes = _load_axes()
    scaffold = build_scaffold(args.corpus_dir, axes)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(scaffold, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # Print the scaffold path so the operator can pipe / open it.
    print(str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
