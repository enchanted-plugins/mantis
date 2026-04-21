"""Tests for docs/architecture/generate.py — verdict-report HTML renderer.

Seeds minimal synthetic state, runs the generator with --html-only, and asserts
the rendered HTML contains the right counts, verdict chips, dark-theme CSS, and
has no unresolved ${...} template placeholders.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ARCH_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ARCH_DIR.parent.parent
sys.path.insert(0, str(ARCH_DIR))


def seed_state(tmp_root: Path) -> None:
    """Populate tmp_root with the five engine state files — one fixture per
    M5 outcome class plus matching verdict / flags / kappa rows."""
    (tmp_root / "plugins" / "mantis-verdict" / "state").mkdir(parents=True)
    (tmp_root / "plugins" / "mantis-core" / "state").mkdir(parents=True)
    (tmp_root / "plugins" / "mantis-sandbox" / "state").mkdir(parents=True)
    (tmp_root / "plugins" / "mantis-rubric" / "state").mkdir(parents=True)
    (tmp_root / "plugins" / "mantis-rubric" / "config").mkdir(parents=True)
    (tmp_root / "plugins" / "mantis-preference" / "state").mkdir(parents=True)
    (tmp_root / "shared").mkdir(parents=True)

    verdicts = [
        {"verdict": "DEPLOY", "confidence": "high", "file": "src/clean.py",
         "engines": [{"engine": "M1", "status": "ran"}, {"engine": "M5", "status": "ran"}],
         "reasons": ["[M1] no flags"], "ts": "2026-04-21T10:00:00Z"},
        {"verdict": "HOLD", "confidence": "preliminary", "file": "src/risky.py",
         "engines": [{"engine": "M1", "status": "ran"}, {"engine": "M5", "status": "unsupported"}],
         "reasons": ["[M5] timeout without confirmation"], "ts": "2026-04-21T10:01:00Z"},
        {"verdict": "FAIL", "confidence": "reduced", "file": "src/broken.py",
         "engines": [{"engine": "M1", "status": "ran"}, {"engine": "M5", "status": "ran"}],
         "reasons": ["[M5] confirmed div-zero witness n=0"], "ts": "2026-04-21T10:02:00Z"},
    ]
    flags = [
        {"ts": "2026-04-21T10:00:00Z", "file": "src/risky.py", "rule_id": "PY-M1-001",
         "severity": "HIGH", "flag_class": "div-zero"},
        {"ts": "2026-04-21T10:00:01Z", "file": "src/broken.py", "rule_id": "PY-M1-001",
         "severity": "CRITICAL", "flag_class": "div-zero"},
        {"ts": "2026-04-21T10:00:02Z", "file": "src/broken.py", "rule_id": "PY-M1-002",
         "severity": "HIGH", "flag_class": "index-oob"},
    ]
    sandbox_runs = [
        {"ts": "2026-04-21T10:00:10Z", "status": "confirmed", "backend": "subprocess",
         "flag_ref": {"file": "src/broken.py"}, "witness": {"n": 0}},
        {"ts": "2026-04-21T10:00:11Z", "status": "timeout", "backend": "subprocess",
         "flag_ref": {"file": "src/risky.py"}, "witness": None},
        {"ts": "2026-04-21T10:00:12Z", "status": "no-bug", "backend": "subprocess",
         "flag_ref": {"file": "src/clean.py"}, "witness": None},
        {"ts": "2026-04-21T10:00:13Z", "status": "platform-unsupported", "backend": "unsupported",
         "flag_ref": {"file": "src/risky.py"}, "witness": None},
    ]
    kappa = [
        {"ts": "2026-04-21T10:00:20Z", "file": "src/clean.py",
         "pass1": {"clarity": 5, "correctness_at_glance": 5, "idiom_fit": 5, "testability": 5, "simplicity": 5},
         "pass2": {"clarity": 5, "correctness_at_glance": 5, "idiom_fit": 5, "testability": 5, "simplicity": 5},
         "kappa": {a: {"s1": 5, "s2": 5, "delta": 0, "agreement": 1.0, "unstable": False}
                   for a in ["clarity", "correctness_at_glance", "idiom_fit", "testability", "simplicity"]},
         "mean_score": 5.0, "unstable_axes": []},
        {"ts": "2026-04-21T10:00:21Z", "file": "src/broken.py",
         "pass1": {"clarity": 3, "correctness_at_glance": 1, "idiom_fit": 2, "testability": 2, "simplicity": 1},
         "pass2": {"clarity": 1, "correctness_at_glance": 1, "idiom_fit": 2, "testability": 2, "simplicity": 4},
         "kappa": {
             "clarity": {"s1": 3, "s2": 1, "delta": 2, "agreement": 0.3, "unstable": True},
             "correctness_at_glance": {"s1": 1, "s2": 1, "delta": 0, "agreement": 1.0, "unstable": False},
             "idiom_fit": {"s1": 2, "s2": 2, "delta": 0, "agreement": 1.0, "unstable": False},
             "testability": {"s1": 2, "s2": 2, "delta": 0, "agreement": 1.0, "unstable": False},
             "simplicity": {"s1": 1, "s2": 4, "delta": -3, "agreement": 0.2, "unstable": True},
         },
         "mean_score": 2.1, "unstable_axes": ["clarity", "simplicity"]},
    ]
    prefs = {"posteriors": {
        "daniel:PY-M1-001": {"alpha": 8, "beta": 2},
        "daniel:PY-M1-002": {"alpha": 3, "beta": 7},
        "daniel:PY-M1-003": {"alpha": 5, "beta": 5},
    }}
    rubric_cfg = {"version": "1.0", "axes": ["clarity", "correctness_at_glance",
                                             "idiom_fit", "testability", "simplicity"]}
    shared_learnings = {"entries": [
        {"code": "F06", "date": "2026-04-20", "note": "edited fixture before reading"},
        {"code": "F12", "date": "2026-04-21", "note": "degeneration loop on clarity axis"},
    ]}

    with (tmp_root / "plugins" / "mantis-verdict" / "state" / "verdict.jsonl").open("w", encoding="utf-8") as f:
        for r in verdicts:
            f.write(json.dumps(r) + "\n")
    with (tmp_root / "plugins" / "mantis-core" / "state" / "review-flags.jsonl").open("w", encoding="utf-8") as f:
        for r in flags:
            f.write(json.dumps(r) + "\n")
    with (tmp_root / "plugins" / "mantis-sandbox" / "state" / "run-log.jsonl").open("w", encoding="utf-8") as f:
        for r in sandbox_runs:
            f.write(json.dumps(r) + "\n")
    with (tmp_root / "plugins" / "mantis-rubric" / "state" / "kappa-log.jsonl").open("w", encoding="utf-8") as f:
        for r in kappa:
            f.write(json.dumps(r) + "\n")
    (tmp_root / "plugins" / "mantis-preference" / "state" / "learnings.json").write_text(
        json.dumps(prefs), encoding="utf-8")
    (tmp_root / "plugins" / "mantis-rubric" / "config" / "rubric-v1.json").write_text(
        json.dumps(rubric_cfg), encoding="utf-8")
    (tmp_root / "shared" / "learnings.json").write_text(
        json.dumps(shared_learnings), encoding="utf-8")


class GenerateReportTest(unittest.TestCase):
    """Verdict-report HTML generation, seeded state, no PDF step."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mantis-report-"))
        seed_state(self.tmp)
        # re-import generate with repo root pointed at the tmp tree
        if "generate" in sys.modules:
            del sys.modules["generate"]
        import generate  # type: ignore
        self.generate = generate
        # Override paths to point at tmp
        generate.REPO_ROOT = self.tmp
        generate.STATE_FILES = {
            "verdict":  self.tmp / "plugins" / "mantis-verdict"    / "state" / "verdict.jsonl",
            "flags":    self.tmp / "plugins" / "mantis-core"       / "state" / "review-flags.jsonl",
            "sandbox":  self.tmp / "plugins" / "mantis-sandbox"    / "state" / "run-log.jsonl",
            "kappa":    self.tmp / "plugins" / "mantis-rubric"     / "state" / "kappa-log.jsonl",
            "prefs":    self.tmp / "plugins" / "mantis-preference" / "state" / "learnings.json",
            "rubric_cfg": self.tmp / "plugins" / "mantis-rubric"   / "config" / "rubric-v1.json",
            "shared_learnings": self.tmp / "shared" / "learnings.json",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_html_contains_all_sections(self) -> None:
        html = self.generate.build_html(self.tmp)
        # Verdict counts
        self.assertIn(">1<", html, "deploy/hold/fail counts should be 1 each")
        # Verdict chips
        self.assertIn("chip-deploy", html)
        self.assertIn("chip-hold", html)
        self.assertIn("chip-fail", html)
        # Dark-theme CSS
        self.assertIn("#0c0e13", html)  # bg
        self.assertIn("#5ed0c2", html)  # teal
        self.assertIn("#f2b77c", html)  # amber
        self.assertIn("#ea6767", html)  # clay
        # Engine sections
        self.assertIn("M1 &mdash; Cousot", html)
        self.assertIn("M5 &mdash; Bounded", html)
        self.assertIn("M6 &mdash; Bayesian", html)
        self.assertIn("M7 &mdash; Zheng", html)
        # M1 rule bars for PY-M1-001
        self.assertIn("PY-M1-001", html)
        # M5 pie has each of the 4 outcome classes
        for status in ("confirmed", "timeout", "no-bug", "platform-unsupported"):
            self.assertIn(status, html)
        # Kappa unstable marker
        self.assertIn("UNSTABLE", html)
        # Learnings — F-code rendered
        self.assertIn("F06", html)
        self.assertIn("F12", html)
        # Appendix dump contains the file paths
        self.assertIn("src/clean.py", html)
        self.assertIn("src/broken.py", html)

    def test_no_unresolved_placeholders(self) -> None:
        html = self.generate.build_html(self.tmp)
        # After substitution, no ${...} placeholder should remain
        import re
        stray = re.findall(r"\$\{[a-zA-Z_][a-zA-Z0-9_]*\}", html)
        self.assertEqual(stray, [], f"unresolved template placeholders: {stray}")

    def test_html_only_cli(self) -> None:
        out_html = self.tmp / "out" / "report.html"
        rc = self.generate.main(["--html-only", "--out", str(out_html)])
        self.assertEqual(rc, 0)
        self.assertTrue(out_html.exists())
        body = out_html.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("Mantis Review Report", body)
        # HTML-only must not contain the literal "PDF render failed"
        self.assertNotIn("PDF render failed", body)

    def test_m5_pie_zero_case(self) -> None:
        # Wipe sandbox runs — pie should degrade to a "no runs" stub
        (self.tmp / "plugins" / "mantis-sandbox" / "state" / "run-log.jsonl").write_text("", encoding="utf-8")
        html = self.generate.build_html(self.tmp)
        self.assertIn("no runs", html)

    def test_verdict_row_html_shape(self) -> None:
        """One verdict row should surface file, chip, confidence, engines, reason."""
        verdict = {
            "verdict": "FAIL", "confidence": "reduced", "file": "src/broken.py",
            "engines": [{"engine": "M1", "status": "ran"}, {"engine": "M5", "status": "ran"}],
            "reasons": ["[M5] confirmed div-zero witness n=0"],
        }
        rows_html = self.generate.render_verdict_rows([verdict])
        self.assertIn('class="file">src/broken.py', rows_html)
        self.assertIn("chip-fail", rows_html)
        self.assertIn("reduced", rows_html)
        self.assertIn("M1:ran", rows_html)
        self.assertIn("confirmed div-zero", rows_html)

    @unittest.skipUnless(shutil.which("node"), "node not on PATH; skipping PDF-render smoke")
    def test_pdf_render_invokable(self) -> None:
        """Smoke-check: render.js exists and its usage line triggers without puppeteer installed."""
        renderer = ARCH_DIR / "render.js"
        self.assertTrue(renderer.exists())
        # No args → usage exit 2
        proc = subprocess.run(
            ["node", str(renderer)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
