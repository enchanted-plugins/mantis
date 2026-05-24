"""SpotBugs adapter for Java M1 coverage.

SpotBugs requires compiled .class files. When the project ships them at a
conventional path (target/classes/ for Maven, build/classes/java/main for
Gradle), we invoke spotbugs against that directory and filter findings to
the source file under review. When .class files are absent we return empty
honestly with a stderr note — never silently pretend.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from m1_walker import Flag

from ._base import (
    _log,
    detect_binary,
    is_security_bucket,
    load_registry,
    run_subprocess,
)

LANG = "java"
FILE_EXTENSIONS = [".java"]

_CLASS_DIR_CANDIDATES = ("target/classes", "build/classes/java/main", "out/production")


def detect() -> Optional[str]:
    return detect_binary("spotbugs", "spotbugs.bat")


def _find_class_dir(source_file: str) -> Optional[Path]:
    path = Path(source_file).resolve()
    for parent in path.parents:
        for rel in _CLASS_DIR_CANDIDATES:
            cand = parent / rel
            if cand.is_dir():
                return cand
    return None


def _severity(rule_id: str) -> str:
    if rule_id.startswith(("NP_", "RC_", "RV_", "NPE_")):
        return "HIGH"
    if rule_id.startswith(("DM_", "DMI_", "SE_")):
        return "MED"
    return "LOW"


def analyze(file_path: str, *, timeout_s: int = 60) -> list[Flag]:
    binary = detect()
    if not binary:
        return []
    class_dir = _find_class_dir(file_path)
    if not class_dir:
        _log({"event": "spotbugs-no-class-dir", "file": file_path})
        return []
    proc = run_subprocess(
        [binary, "-textui", "-xml:withMessages", str(class_dir)],
        timeout_s=timeout_s,
    )
    if not proc or not proc.stdout:
        return []
    registry = load_registry(LANG)
    flags: list[Flag] = []
    abs_target = str(Path(file_path).resolve()).replace("\\", "/")
    try:
        root = ET.fromstring(proc.stdout)
    except ET.ParseError as e:
        _log({"event": "spotbugs-xml-parse-failed", "error": str(e)})
        return []
    for bug in root.iter("BugInstance"):
        rule_id = bug.get("type") or ""
        src = bug.find("SourceLine")
        if src is None:
            continue
        src_name = src.get("sourcepath") or src.get("name") or ""
        if not abs_target.endswith(src_name.replace("\\", "/")):
            continue
        bucket, sev_default = registry.get(rule_id, ("unmapped", "MED"))
        if is_security_bucket(bucket) or bucket != "correctness_m1":
            continue
        line = int(src.get("start") or 0)
        flags.append(Flag(
            file=file_path,
            line=line,
            function="<unknown>",
            rule_id=f"JAVA-{rule_id}",
            flag_class=rule_id.lower(),
            severity=_severity(rule_id),
            witness_hints={},
            needs_M5_confirmation=False,
            m1_confidence=0.85,
        ))
    return flags
