"""Mantis M5 sandbox — orchestrator.

Reads M1's review-flags.jsonl, synthesizes witnesses, dispatches to the
platform-appropriate runner (POSIX / WSL / unsupported), classifies each
outcome, and appends a record per witness to run-log.jsonl.

Per-flag accounting:
    - If no witnesses can be synthesized -> one `input-synthesis-failed` record.
    - If any witness confirms a bug         -> record per witness; the flag
                                                 is considered confirmed on
                                                 the first confirming witness,
                                                 but remaining witnesses still
                                                 run (cheap; boundary coverage).
    - If all witnesses run clean            -> one `no-bug-found` record
                                                 (summary, not per-witness),
                                                 per the spec: "all witnesses
                                                 clean for a flag -> emits
                                                 single no-bug-found record".

CLI:
    python plugins/mantis-sandbox/scripts/sandbox.py [flags.jsonl]

Default input:  plugins/mantis-core/state/review-flags.jsonl
Default output: plugins/mantis-sandbox/state/run-log.jsonl
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import traceback
from pathlib import Path

# Repo-root-relative imports. We support two invocation shapes:
#   (a) package import:  from plugins.mantis_sandbox.scripts import sandbox
#       — fails because the directory is hyphenated. Not the primary path.
#   (b) script run:      python plugins/mantis-sandbox/scripts/sandbox.py
#       — we sys.path-augment to the scripts/ dir and import bare names.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Also add the plugin root so `bridge.platform_guard` resolves.
_PLUGIN_ROOT = _SCRIPTS_DIR.parent
if str(_PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

import outcome as _outcome  # noqa: E402
import witness_synth as _witness  # noqa: E402
from runners import _base as _runner_base  # noqa: E402

# Repo-root sys.path shim for shared/learnings.py (advisory Gauss log).
_SHARED = Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import learnings as _learnings  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover — advisory
    _learnings = None

# Bridge imports are deferred — the bridge package is owned by Agent 6
# and may not exist yet at import time on every host. We import lazily
# in `_select_backend` and fall back to a minimal platform check.


# -------------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------------

_REPO_ROOT = _SCRIPTS_DIR.parents[2]   # plugins/mantis-sandbox/scripts/ -> repo root
_DEFAULT_INPUT = _REPO_ROOT / "plugins" / "mantis-core" / "state" / "review-flags.jsonl"
_DEFAULT_OUTPUT = _REPO_ROOT / "plugins" / "mantis-sandbox" / "state" / "run-log.jsonl"


# -------------------------------------------------------------------------
# Backend selection
# -------------------------------------------------------------------------


def _select_backend() -> tuple[str, object | None]:
    """Return (backend_name, runner_or_bridge).

    backend_name is one of: "posix" | "wsl" | "unsupported".
    For "posix", runner is a PosixPythonRunner instance.
    For "wsl", runner is the bridge.wsl.run_in_wsl callable.
    For "unsupported", runner is None.
    """
    try:
        from bridge.platform_guard import check  # type: ignore
    except Exception:
        # Bridge not yet available — minimal fallback: POSIX on non-Windows,
        # unsupported on Windows. Never silently pretend M5 ran.
        import platform as _platform
        if _platform.system() == "Windows":
            return "unsupported", None
        from runners.python import PosixPythonRunner
        return "posix", PosixPythonRunner()

    guard = check()
    backend = guard.get("backend", "unsupported")

    if backend == "posix":
        from runners.python import PosixPythonRunner
        return "posix", PosixPythonRunner()
    if backend == "wsl":
        from bridge.wsl import run_in_wsl  # type: ignore
        return "wsl", run_in_wsl
    return "unsupported", None


# -------------------------------------------------------------------------
# I/O helpers
# -------------------------------------------------------------------------


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _read_flags(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("needs_M5_confirmation", True):
                out.append(rec)
    return out


def _append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _emit_unsupported(flag: dict, out_path: Path) -> None:
    _append_record(out_path, {
        "ts": _iso_now(),
        "flag_ref": flag,
        "witness": None,
        "status": "platform-unsupported",
        "exit_code": None,
        "signal": None,
        "error_class": None,
        "traceback_head": None,
        "duration_ms": 0,
        "backend": "unsupported",
    })


def _emit_synth_failed(flag: dict, out_path: Path, backend: str) -> None:
    _append_record(out_path, {
        "ts": _iso_now(),
        "flag_ref": flag,
        "witness": None,
        "status": "input-synthesis-failed",
        "exit_code": None,
        "signal": None,
        "error_class": None,
        "traceback_head": "no witnesses could be constructed for this flag",
        "duration_ms": 0,
        "backend": backend,
    })


# -------------------------------------------------------------------------
# Run one witness via the selected backend
# -------------------------------------------------------------------------


def _run_witness(
    backend: str,
    runner: object,
    flag: dict,
    witness: dict,
) -> _runner_base.RunResult:
    if backend == "posix":
        return runner.run(  # type: ignore[union-attr]
            target_file=flag["file"],
            function_name=flag["function"],
            witness=witness,
        )
    if backend == "wsl":
        # Bridge contract (per Agent 6): returns dict with keys
        #   {exit_code, stdout, stderr, duration_ms, signal}
        result = runner(  # type: ignore[misc]
            script_path=flag["file"],
            entrypoint=flag["function"],
            witness_json=json.dumps(witness),
            timeout_s=10,
        )
        return _runner_base.RunResult(
            exit_code=int(result.get("exit_code", 1)),
            stdout=str(result.get("stdout", "")),
            stderr=str(result.get("stderr", "")),
            duration_ms=int(result.get("duration_ms", 0)),
            signal_name=result.get("signal"),
        )
    # Should not be reached — platform-unsupported is handled upstream.
    raise RuntimeError(f"unknown backend: {backend}")


# -------------------------------------------------------------------------
# Main per-flag processing
# -------------------------------------------------------------------------


def _process_flag(
    flag: dict,
    backend: str,
    runner: object,
    out_path: Path,
) -> dict:
    """Run all witnesses for one flag. Returns a per-flag counter dict."""
    counters = {
        "confirmed-bug": 0,
        "timeout-without-confirmation": 0,
        "no-bug-found": 0,
        "input-synthesis-failed": 0,
        "sandbox-error": 0,
    }

    flag_class = flag.get("flag_class", "")
    witness_hints = flag.get("witness_hints", {}) or {}
    target_file = flag.get("file", "")
    function_name = flag.get("function", "")

    witnesses = _witness.synthesize(
        flag_class=flag_class,
        witness_hints=witness_hints,
        target_file=target_file,
        function_name=function_name,
    )

    if not witnesses:
        _emit_synth_failed(flag, out_path, backend)
        counters["input-synthesis-failed"] += 1
        return counters

    any_non_clean = False
    clean_witness_count = 0
    records_buffer: list[dict] = []

    for w in witnesses:
        try:
            result = _run_witness(backend, runner, flag, w)
        except Exception:
            # Runner raised — infra error. Record and continue to next witness.
            _append_record(out_path, {
                "ts": _iso_now(),
                "flag_ref": flag,
                "witness": w,
                "status": "sandbox-error",
                "exit_code": None,
                "signal": None,
                "error_class": "RunnerException",
                "traceback_head": traceback.format_exc()[:1000],
                "duration_ms": 0,
                "backend": backend,
            })
            counters["sandbox-error"] += 1
            any_non_clean = True
            # Advisory event-bus publish. Failures swallowed per brand
            # invariant #7 — the bus is observability, not orchestration.
            try:
                import sys as _sys
                _shared = _REPO_ROOT / "shared"
                if str(_shared) not in _sys.path:
                    _sys.path.insert(0, str(_shared))
                from events.bus import publish as _publish  # type: ignore
                _publish("mantis.sandbox.failed", {
                    "file": flag.get("file", ""),
                    "function": flag.get("function", ""),
                    "error_class": "RunnerException",
                    "duration_ms": 0,
                }, source="mantis-sandbox")
            except Exception:
                pass
            continue

        status, error_class = _outcome.classify(
            flag_class=flag_class,
            exit_code=result.exit_code,
            stderr=result.stderr,
            signal_name=result.signal_name,
        )

        record = {
            "ts": _iso_now(),
            "flag_ref": flag,
            "witness": w,
            "status": status,
            "exit_code": result.exit_code,
            "signal": result.signal_name,
            "error_class": error_class,
            "traceback_head": (result.stderr or "")[:1000],
            "duration_ms": result.duration_ms,
            "backend": backend,
        }

        if status == "no-bug-found":
            clean_witness_count += 1
            # Defer writing clean records; we emit one summary if all clean.
            records_buffer.append(record)
        else:
            # Flush any buffered clean records so the timeline is honest.
            for r in records_buffer:
                _append_record(out_path, r)
                counters["no-bug-found"] += 1
            records_buffer.clear()

            _append_record(out_path, record)
            counters[status] = counters.get(status, 0) + 1
            any_non_clean = True

            # Advisory event-bus publish for classified sandbox-errors
            # (SIGKILL / SIGXFSZ / resource caps). Per brand invariant
            # #7 the bus is observability, never orchestration.
            if status == "sandbox-error":
                try:
                    import sys as _sys
                    _shared = _REPO_ROOT / "shared"
                    if str(_shared) not in _sys.path:
                        _sys.path.insert(0, str(_shared))
                    from events.bus import publish as _publish  # type: ignore
                    _publish("mantis.sandbox.failed", {
                        "file": flag.get("file", ""),
                        "function": flag.get("function", ""),
                        "error_class": error_class,
                        "duration_ms": result.duration_ms,
                    }, source="mantis-sandbox")
                except Exception:
                    pass

            # Gauss Accumulation — confirmed bug = new fixture candidate.
            if status == "confirmed-bug" and _learnings is not None:
                try:
                    _learnings.safe_emit(
                        plugin="mantis-sandbox",
                        code="F06",
                        axis=flag_class or "unknown",
                        hypothesis="M1 flag confirmed with witness",
                        outcome=json.dumps(w, separators=(",", ":"))[:500],
                        counter="add witness to fixture corpus",
                    )
                except Exception:
                    pass

    # All witnesses clean -> emit a single summary no-bug-found record.
    if not any_non_clean and clean_witness_count > 0:
        _append_record(out_path, {
            "ts": _iso_now(),
            "flag_ref": flag,
            "witness": {"witnesses_tried": clean_witness_count},
            "status": "no-bug-found",
            "exit_code": 0,
            "signal": None,
            "error_class": None,
            "traceback_head": None,
            "duration_ms": 0,
            "backend": backend,
        })
        counters["no-bug-found"] += 1

    return counters


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    in_path = Path(argv[0]) if argv else _DEFAULT_INPUT
    out_path = Path(argv[1]) if len(argv) > 1 else _DEFAULT_OUTPUT

    flags = _read_flags(in_path)
    if not flags:
        print(json.dumps({"confirmed": 0, "timeout": 0, "sandbox_error": 0,
                          "no_bug": 0, "synth_failed": 0, "unsupported": 0,
                          "reason": "no-flags-to-confirm"}))
        return 0

    backend, runner = _select_backend()

    if backend == "unsupported":
        for flag in flags:
            _emit_unsupported(flag, out_path)
        print(json.dumps({"confirmed": 0, "timeout": 0, "sandbox_error": 0,
                          "no_bug": 0, "synth_failed": 0,
                          "unsupported": len(flags),
                          "reason": "platform-unsupported"}))
        return 0

    totals = {"confirmed": 0, "timeout": 0, "sandbox_error": 0,
              "no_bug": 0, "synth_failed": 0, "unsupported": 0}
    for flag in flags:
        counters = _process_flag(flag, backend, runner, out_path)
        totals["confirmed"] += counters.get("confirmed-bug", 0)
        totals["timeout"] += counters.get("timeout-without-confirmation", 0)
        totals["sandbox_error"] += counters.get("sandbox-error", 0)
        totals["no_bug"] += counters.get("no-bug-found", 0)
        totals["synth_failed"] += counters.get("input-synthesis-failed", 0)

    print(json.dumps(totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
