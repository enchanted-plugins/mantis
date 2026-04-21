"""Local JSON-lines event bus — stand-in for the enchanted-mcp transport.

Brand invariant #7 names enchanted-mcp as the cross-plugin coordination
surface. Until that transport lands, this module provides a functionally
equivalent local-file bus so mantis plugins can publish and subscribe
without a sibling-plugin dependency.

Every event is a single JSON object appended as one line to
``shared/events/bus.jsonl``. Consumers tail the file and filter by topic
and/or since-timestamp. Publishing is atomic at the line level via
``os.O_APPEND`` — POSIX and Windows both guarantee that a single
``write()`` under ``O_APPEND`` lands at EOF without being interleaved
with concurrent appenders (up to PIPE_BUF on POSIX; Windows serializes
via the file handle's position lock). Each event is emitted in one
``write()`` call so two concurrent publishers never produce a corrupted
line.

Schema (persisted line):
    {"topic": "mantis.review.completed",
     "payload": {...},
     "ts": "2026-04-20T12:34:56.789012+00:00",
     "source": "mantis-verdict",
     "uuid": "8c3f5b12-..."}

Phase 2 migration: when enchanted-mcp lands, only the ``publish`` /
``subscribe`` / ``latest`` implementations change — their signatures
and the event schema are stable. Callers do not need to change.

Stdlib only. Advisory; failures do not propagate (callers wrap in
try/except — the bus is observability, not orchestration).
"""

from __future__ import annotations

import json
import os
import threading
import uuid as _uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional


# In-process lock: serialize writes from multiple threads so a single
# ``os.write()`` never interleaves with another. Cross-process atomicity
# on POSIX relies on the kernel's O_APPEND guarantee (single-write,
# line-sized payload); on Windows, cross-process appenders also serialize
# through the shared file position lock for O_APPEND handles.
_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Path resolution: walk up from this file until we find the repo root
# (marked by the presence of CLAUDE.md or the shared/ directory).
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "CLAUDE.md").exists() and (parent / "shared").is_dir():
            return parent
    # Fallback: three levels up from this file (shared/events/bus.py)
    return here.parents[2]


_REPO_ROOT = _find_repo_root()
BUS_PATH = _REPO_ROOT / "shared" / "events" / "bus.jsonl"


# ---------------------------------------------------------------------------
# Event record
# ---------------------------------------------------------------------------


@dataclass
class Event:
    topic: str
    payload: dict
    ts: str
    source: str
    uuid: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "Event":
        data = json.loads(line)
        return cls(
            topic=str(data.get("topic", "")),
            payload=data.get("payload", {}) or {},
            ts=str(data.get("ts", "")),
            source=str(data.get("source", "")),
            uuid=str(data.get("uuid", "")),
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def publish(topic: str, payload: dict, source: str,
            bus_path: Optional[Path] = None) -> Event:
    """Append one event line to the bus. Returns the Event record.

    Concurrent publishes are safe: the line is built in memory then
    written via a single ``os.write()`` on an ``O_APPEND`` fd. That
    syscall is atomic at the kernel level for line-sized writes.
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    event = Event(
        topic=topic,
        payload=payload,
        ts=_iso_now(),
        source=source,
        uuid=str(_uuid.uuid4()),
    )
    path = bus_path or BUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = event.to_json() + "\n"
    data = line.encode("utf-8")
    # O_APPEND guarantees the write lands at current EOF atomically for
    # line-sized payloads (POSIX kernel contract). The in-process lock
    # additionally serializes Python-thread writers so interleaving is
    # impossible on any platform.
    with _WRITE_LOCK:
        fd = os.open(str(path),
                      os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    return event


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


def _matches_topic(event_topic: str, filter_topic: Optional[str]) -> bool:
    if filter_topic is None:
        return True
    # Prefix match: "mantis." matches "mantis.review.completed"
    # Exact topic also matches (since a string is a prefix of itself).
    return event_topic == filter_topic or event_topic.startswith(filter_topic)


def _iter_lines(path: Path) -> Iterable[str]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if raw:
                yield raw


def subscribe(topic: Optional[str] = None,
              since: Optional[str] = None,
              limit: Optional[int] = None,
              bus_path: Optional[Path] = None) -> Iterator[Event]:
    """Yield events from the bus tail, filtered.

    Args:
        topic: exact-match or prefix-match filter; ``None`` = all.
                ``"mantis."`` matches ``"mantis.review.completed"``.
                ``"mantis.review.completed"`` matches only itself.
        since: ISO timestamp; yields only events with ``ts > since``.
        limit: maximum events to yield. The most recent N are kept.
        bus_path: override for tests.

    Non-blocking. Polling is the caller's responsibility.
    """
    path = bus_path or BUS_PATH
    matches: list[Event] = []
    for line in _iter_lines(path):
        try:
            ev = Event.from_json(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not _matches_topic(ev.topic, topic):
            continue
        if since is not None and ev.ts <= since:
            continue
        matches.append(ev)

    if limit is not None and limit >= 0:
        matches = matches[-limit:]

    for ev in matches:
        yield ev


def latest(topic: str, bus_path: Optional[Path] = None) -> Optional[Event]:
    """Return the most recent event whose topic matches (prefix or exact).

    Returns None if no matching event exists.
    """
    path = bus_path or BUS_PATH
    found: Optional[Event] = None
    for line in _iter_lines(path):
        try:
            ev = Event.from_json(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if _matches_topic(ev.topic, topic):
            found = ev
    return found


# ---------------------------------------------------------------------------
# Convenience — used by test harnesses to reset state between runs.
# ---------------------------------------------------------------------------


def _reset(bus_path: Optional[Path] = None) -> None:
    """Truncate the bus file. Test-only helper (prefix underscore)."""
    path = bus_path or BUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
