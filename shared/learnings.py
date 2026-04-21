"""Gauss Accumulation — per-session learning log + cross-plugin aggregator.

Brand invariant #6 (CLAUDE.md): "Per-session learnings at
`plugins/<name>/state/learnings.jsonl`; exported to `shared/learnings.json`."

Schema per entry (tagged with one code from shared/conduct/failure-modes.md):

    {"ts": "2026-04-20T12:34:56+00:00",
     "plugin": "mantis-core",
     "code": "F12",
     "axis": "div-zero",
     "hypothesis": "rule PY-M1-001 flagged but dismissed via M6 posterior",
     "outcome": "{'posterior_mean': 0.12, 'dev': 'alice'}",
     "counter": "monitor for dev-specific override"}

Storage:
    plugin local:  plugins/<plugin>/state/learnings.jsonl  (JSONL, append-only)
    aggregated:    shared/learnings.json                   (pretty JSON snapshot)

Naming note: the brand invariant specifies `learnings.json` as the path, but
`mantis-preference` already owns `plugins/mantis-preference/state/learnings.json`
for Beta-Binomial posteriors (pretty JSON object, not a log). To avoid
clobbering preferences while keeping "JSONL, append-only" semantics, this
module uses the `.jsonl` extension for per-plugin Gauss logs. The aggregated
snapshot at `shared/learnings.json` is the human-reviewed artifact and
remains `.json`.

CLI:
    python shared/learnings.py export
    python shared/learnings.py tail --plugin mantis-core --n 20
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# -----------------------------------------------------------------------------
# Canonical F-codes (shared/conduct/failure-modes.md)
# -----------------------------------------------------------------------------

VALID_CODES = frozenset({f"F{n:02d}" for n in range(1, 15)})  # F01..F14


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent          # shared/
_REPO_ROOT = _HERE.parent                         # repo root
_PLUGIN_LOG_NAME = "learnings.jsonl"
_AGG_PATH = _HERE / "learnings.json"


def _plugin_log(plugin: str) -> Path:
    return _REPO_ROOT / "plugins" / plugin / "state" / _PLUGIN_LOG_NAME


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Learning record
# -----------------------------------------------------------------------------


@dataclass
class Learning:
    """One Gauss Accumulation entry. `code` must be one of F01..F14."""

    plugin: str
    code: str
    hypothesis: str
    outcome: str
    counter: str
    axis: str = ""
    ts: str = field(default_factory=_iso_now)

    def __post_init__(self) -> None:
        if self.code not in VALID_CODES:
            raise ValueError(
                f"invalid code {self.code!r}; expected one of F01..F14 "
                f"(see shared/conduct/failure-modes.md)"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Learning":
        return cls(
            plugin=d["plugin"],
            code=d["code"],
            hypothesis=d.get("hypothesis", ""),
            outcome=d.get("outcome", ""),
            counter=d.get("counter", ""),
            axis=d.get("axis", ""),
            ts=d.get("ts", _iso_now()),
        )


# -----------------------------------------------------------------------------
# Append + read (per-plugin)
# -----------------------------------------------------------------------------


def append(plugin: str, learning: Learning) -> Path:
    """Append one JSONL record to plugins/<plugin>/state/learnings.jsonl.

    The plugin name on the record is forced to match the target file, so
    a caller cannot silently cross-attribute.
    """
    if learning.plugin != plugin:
        learning = Learning(
            plugin=plugin,
            code=learning.code,
            hypothesis=learning.hypothesis,
            outcome=learning.outcome,
            counter=learning.counter,
            axis=learning.axis,
            ts=learning.ts,
        )
    path = _plugin_log(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One-line JSON + newline. POSIX O_APPEND makes <PIPE_BUF concurrent
    # writes atomic; on Windows (git-bash) append mode serializes through
    # the CRT, which is adequate at the volumes Mantis produces.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(learning.to_dict(), separators=(",", ":")) + "\n")
    return path


def read_all(plugin: str) -> list[Learning]:
    """Return entries in file order (chronological, since append-only)."""
    path = _plugin_log(plugin)
    if not path.exists():
        return []
    out: list[Learning] = []
    with path.open("r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue  # skip corrupt line; advisory log, not load-bearing
            try:
                out.append(Learning.from_dict(rec))
            except (KeyError, ValueError):
                continue
    return out


# -----------------------------------------------------------------------------
# Aggregate export
# -----------------------------------------------------------------------------


def _discover_plugins() -> list[str]:
    plugins_dir = _REPO_ROOT / "plugins"
    if not plugins_dir.exists():
        return []
    return sorted(
        p.name for p in plugins_dir.iterdir()
        if p.is_dir() and (p / "state").exists()
    )


def export_aggregated(out_path: Path | None = None) -> dict:
    """Aggregate every plugin's learnings.jsonl into shared/learnings.json.

    Deduplication: entries keyed by (ts, plugin, code). First occurrence wins.
    Entries are emitted sorted by (ts, plugin, code) for stable review diffs.
    """
    out = out_path or _AGG_PATH
    seen: set[tuple[str, str, str]] = set()
    entries: list[dict] = []
    for plugin in _discover_plugins():
        for learning in read_all(plugin):
            key = (learning.ts, learning.plugin, learning.code)
            if key in seen:
                continue
            seen.add(key)
            entries.append(learning.to_dict())
    entries.sort(key=lambda e: (e.get("ts", ""), e.get("plugin", ""), e.get("code", "")))
    snapshot = {
        "generated_at": _iso_now(),
        "entries": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


# -----------------------------------------------------------------------------
# Safe emit — advisory wrapper for engine integrations
# -----------------------------------------------------------------------------


def safe_emit(
    plugin: str,
    code: str,
    hypothesis: str,
    outcome: str,
    counter: str,
    axis: str = "",
) -> None:
    """Fire-and-forget append. Never raises — learning writes are advisory
    and must not block the engine that produced the signal."""
    try:
        append(
            plugin,
            Learning(
                plugin=plugin,
                code=code,
                hypothesis=hypothesis,
                outcome=outcome,
                counter=counter,
                axis=axis,
            ),
        )
    except Exception:
        # Silent by contract — learnings never block engines.
        pass


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _cli_export(_args) -> int:
    snap = export_aggregated()
    print(json.dumps({
        "generated_at": snap["generated_at"],
        "entries": len(snap["entries"]),
        "written_to": str(_AGG_PATH),
    }))
    return 0


def _cli_tail(args) -> int:
    entries = read_all(args.plugin)
    tail = entries[-args.n:] if args.n > 0 else entries
    for e in tail:
        print(json.dumps(e.to_dict()))
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gauss-learnings")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("export", help="write aggregated snapshot to shared/learnings.json")

    tail = sub.add_parser("tail", help="print the last N entries for one plugin")
    tail.add_argument("--plugin", required=True)
    tail.add_argument("--n", type=int, default=20)

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "export":
        return _cli_export(args)
    if args.cmd == "tail":
        return _cli_tail(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
