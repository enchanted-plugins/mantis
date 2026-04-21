"""Subscriber helpers — Phase 2 seed.

These read the bus tail for events published by sibling plugins
(Hornet, Reaper, Nook). In v2, siblings live in separate repos and do
not publish into this bus. Each helper therefore returns None / False
when no matching event exists — the no-op read is expected and correct.

When a Phase 2 deployment wires the enchanted-mcp transport, these
helpers keep their exact signatures; only ``bus.subscribe`` changes
underneath.

Contract (per root CLAUDE.md):
  * ``check_for_hornet_boost(file)`` — Hornet's V1 trust-score is the
    authoritative change classifier. Mantis consumes, never re-classifies.
  * ``check_for_reaper_context(file)`` — Reaper owns CWE taxonomy.
    Mantis attaches context, never re-reports.
  * ``check_for_nook_budget_pressure()`` — Nook signals budget threshold
    crossings. mantis-rubric drops judge tier from Sonnet to Haiku in
    response.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from .bus import Event, subscribe
except ImportError:
    # Script-mode fallback: allow `python shared/events/subscriptions.py`
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bus import Event, subscribe  # type: ignore


# ---------------------------------------------------------------------------
# Hornet — change.classified (trust score for M6 prior weighting)
# ---------------------------------------------------------------------------


def check_for_hornet_boost(file: str,
                            bus_path: Optional[Path] = None) -> Optional[float]:
    """Return trust-score delta from the most recent Hornet event for this file.

    Payload contract (Hornet V1):
        {"file": "...", "trust": 0.62, "classification": "..."}

    Returns None when no matching event exists.
    """
    latest_trust: Optional[float] = None
    for ev in subscribe(topic="hornet.change.classified", bus_path=bus_path):
        payload = ev.payload or {}
        if payload.get("file") != file:
            continue
        trust = payload.get("trust")
        if isinstance(trust, (int, float)):
            latest_trust = float(trust)
    return latest_trust


# ---------------------------------------------------------------------------
# Reaper — vuln.detected (CWE context for M1 annotations)
# ---------------------------------------------------------------------------


def check_for_reaper_context(file: str,
                              bus_path: Optional[Path] = None
                              ) -> Optional[dict]:
    """Return CWE context dict from the most recent Reaper event for this file.

    Payload contract (Reaper R3):
        {"file": "...", "cwe": "CWE-89", "severity": "HIGH", ...}

    Returns None when no matching event exists. Mantis never re-classifies
    the CWE (root CLAUDE.md §1); it only attaches context to M7's rubric
    input.
    """
    latest_ctx: Optional[dict] = None
    for ev in subscribe(topic="reaper.vuln.detected", bus_path=bus_path):
        payload = ev.payload or {}
        if payload.get("file") != file:
            continue
        latest_ctx = {k: v for k, v in payload.items() if k != "file"}
    return latest_ctx


# ---------------------------------------------------------------------------
# Nook — budget.threshold.crossed (drops mantis-rubric judge tier)
# ---------------------------------------------------------------------------


_NOOK_WINDOW_HOURS = 1


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        raw = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def check_for_nook_budget_pressure(bus_path: Optional[Path] = None) -> bool:
    """True when a nook.budget.threshold.crossed event fired in the last hour.

    Used by mantis-rubric's judge-tier selector per root CLAUDE.md §
    Agent tiers: Haiku is the budget fallback "when Nook fires".
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_NOOK_WINDOW_HOURS)
    for ev in subscribe(topic="nook.budget.threshold.crossed",
                         bus_path=bus_path):
        when = _parse_iso(ev.ts)
        if when is not None and when >= cutoff:
            return True
    return False
