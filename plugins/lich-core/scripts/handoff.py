#!/usr/bin/env python3
"""Capability-based cross-tool handoff.

Lich's Go adapter drops every rule bucketed `security_defer_to_hydra` on the
stated grounds that "Hydra R3 owns CWEs". That is a BRANDING claim, not a
capability claim, and it is unconditional: Lich suppresses the class whether
or not Hydra actually covered it.

Measured consequence on a Go CLI fixture: Hydra's Go injection rule requires
an HTTP taint token on the sink line and could not fire, while Lich refused
to look because the lane was "owned". A confirmed command injection fell
into the gap between two tools that each believed the other had it.

This module replaces ownership with evidence. A lane may only be suppressed
when a Hydra analysis report actually demonstrates coverage of that class for
that file. With no evidence, the correct state is UNCOVERED and it must be
surfaced, never silently dropped.

Evidence source, in priority order:
  1. $LICH_HYDRA_REPORT — path to a JSON enchanter.analysis-report/v1 doc
  2. $LICH_HYDRA_REPORT_DIR — directory of such reports, matched by target
  3. nothing -> UNCOVERED
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple

# Coverage states mirrored from the shared contract.
COVERED = "covered"
PARTIAL = "partial"
UNCOVERED = "uncovered"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"

_SCHEMA_PREFIX = "enchanter.analysis-report/"


def _load_report(target_file: str) -> Optional[dict]:
    """Locate a Hydra report describing `target_file`, if one exists."""
    direct = os.environ.get("LICH_HYDRA_REPORT")
    if direct and os.path.isfile(direct):
        try:
            with open(direct, encoding="utf-8") as fh:
                doc = json.load(fh)
            if str(doc.get("schema", "")).startswith(_SCHEMA_PREFIX):
                return doc
        except (OSError, ValueError):
            return None

    dirpath = os.environ.get("LICH_HYDRA_REPORT_DIR")
    if dirpath and os.path.isdir(dirpath):
        want = os.path.basename(os.path.abspath(target_file))
        try:
            names = sorted(os.listdir(dirpath))
        except OSError:
            return None
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            if not str(doc.get("schema", "")).startswith(_SCHEMA_PREFIX):
                continue
            path = ((doc.get("target") or {}).get("path")) or ""
            if os.path.basename(os.path.abspath(path)) == want:
                return doc
    return None


def hydra_coverage(defect_class: str, target_file: str) -> Tuple[str, str]:
    """Return (state, reason) describing Hydra's coverage of `defect_class`.

    `state` is one of COVERED / PARTIAL / DEGRADED / UNAVAILABLE / UNCOVERED.
    Only COVERED authorises Lich to suppress the lane.
    """
    doc = _load_report(target_file)
    if doc is None:
        return UNCOVERED, (
            "no Hydra analysis report available (set LICH_HYDRA_REPORT or "
            "LICH_HYDRA_REPORT_DIR); the security lane is UNCOVERED, not owned")

    overall = doc.get("analysis_status")
    if overall == "unsupported":
        return UNAVAILABLE, (
            "Hydra reports no rules for this language; the security lane is "
            "not covered by either tool")

    for entry in doc.get("coverage") or []:
        cls = entry.get("class")
        if cls not in (defect_class, "*"):
            continue
        status = entry.get("status")
        if status == "complete" and not entry.get("truncated"):
            return COVERED, f"Hydra reports complete coverage of {cls}"
        if status in ("partial", "degraded"):
            return (
                PARTIAL if status == "partial" else DEGRADED,
                f"Hydra reports {status} coverage of {cls}: "
                f"{entry.get('notes', '')}",
            )
        if status in ("unsupported", "unavailable"):
            return UNAVAILABLE, f"Hydra cannot analyse {cls}: {status}"

    return UNCOVERED, (
        f"Hydra report present but makes no coverage claim for "
        f"'{defect_class}'")


def may_suppress(defect_class: str, target_file: str) -> Tuple[bool, str]:
    """True only when deferring the lane is backed by real evidence."""
    state, reason = hydra_coverage(defect_class, target_file)
    return (state == COVERED), reason
