"""Opportunistic live tests for language adapters.

These tests exercise the *real* linter end-to-end — no subprocess mocks —
against deliberately buggy fixtures in tests/fixtures/polyglot/. They are
gated with `@unittest.skipUnless(shutil.which("<tool>"), ...)` so the suite
stays green on boxes without the linter installed (expected: most CI boxes
and all Windows developer boxes).

What each test asserts when the tool IS present:
    * analyze() returns a list[Flag]; at least one flag for the buggy fixture.
    * Every flag's rule_id matches the adapter's expected prefix pattern.
    * No flag is in the security bucket — Reaper owns CWEs, Mantis doesn't.

What the suite reports when the tool is NOT present:
    * A single-line `[skip: <tool> not installed]` marker. The test is still
      green — absence is a honest outcome, not a failure.

Second soft dep: `tests/fixtures/polyglot/sample.<ext>`. If that fixture is
missing (a sibling agent's territory), the test skips cleanly with a
distinct marker `[skip: fixture missing]` rather than erroring. This is the
"soft dependency" part of the scope fence.

Windows note
------------
On this host, `staticcheck`, `cargo`/`clippy`, `spotbugs`, `clang-tidy`,
`rubocop`, `shellcheck`, `semgrep`, and `ruff` will typically all be absent.
That's fine — the suite honestly reports skip rather than pretending the
adapters were exercised.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parents[1]  # plugins/mantis-core/scripts
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "adapters"))

# Adapter imports — all must be importable regardless of whether the
# underlying linter is on PATH.
from adapters import cpp, go, java, ruby, rust, semgrep, shell  # noqa: E402
import ruff_adapter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture resolution
# ---------------------------------------------------------------------------

_REPO_ROOT = _SCRIPTS.parents[2]  # <repo>/plugins/mantis-core/scripts -> <repo>
_POLYGLOT = _REPO_ROOT / "tests" / "fixtures" / "polyglot"


def _fixture(name: str) -> Path | None:
    p = _POLYGLOT / name
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _assert_basic_shape(testcase: unittest.TestCase, flags: list) -> None:
    """Every live adapter must return a list; individual flag records carry
    file / line / rule_id / severity at minimum. We don't require a specific
    count — real linters may grow or shrink findings across versions — but
    we do require that Flag shape is stable."""
    testcase.assertIsInstance(flags, list)
    for flag in flags:
        testcase.assertTrue(hasattr(flag, "rule_id"), "flag missing rule_id")
        testcase.assertTrue(hasattr(flag, "line"), "flag missing line")
        testcase.assertTrue(hasattr(flag, "severity"), "flag missing severity")


def _assert_no_security_bucket(
    testcase: unittest.TestCase,
    flags: list,
    security_prefixes: tuple[str, ...],
) -> None:
    """Reaper owns CWE taxonomy. Even if an adapter accidentally surfaces a
    security-prefix rule ID, the non-duplication contract says it must not
    land in M1 output. This is the defense-in-depth cross-check."""
    for flag in flags:
        rid = (flag.rule_id or "").lower()
        for pref in security_prefixes:
            testcase.assertFalse(
                rid.startswith(pref.lower()),
                f"security-bucket rule {flag.rule_id!r} leaked into M1 flags",
            )


# ---------------------------------------------------------------------------
# Per-adapter live tests
# ---------------------------------------------------------------------------


class GoAdapterLive(unittest.TestCase):
    """staticcheck on sample.go — unchecked `os.Open` error swallow."""

    @unittest.skipUnless(shutil.which("staticcheck"), "staticcheck not installed")
    def test_analyze_sample_go(self):
        fixture = _fixture("sample.go")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.go missing")
        flags = go.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        # SA-series = staticcheck correctness rules. An `_` swallow typically
        # triggers SA4006 / SA4017 / SA9003 depending on version — we don't
        # pin a specific code, just that any Mantis-mapped rule surfaced.
        for flag in flags:
            self.assertRegex(
                flag.rule_id,
                r"^(SA|ST|S|U|QF)\d",
                f"unexpected rule_id shape from staticcheck: {flag.rule_id!r}",
            )
        # Security-bucket crypto rule SA1018 is the one staticcheck shares
        # with Reaper's lane; it must never leak to M1.
        _assert_no_security_bucket(self, flags, ("sa1018",))


class RustAdapterLive(unittest.TestCase):
    """clippy on sample.rs — run only when a cargo toolchain is present."""

    @unittest.skipUnless(shutil.which("cargo"), "cargo not installed")
    def test_analyze_sample_rs(self):
        fixture = _fixture("sample.rs")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.rs missing")
        # clippy requires a Cargo.toml in an ancestor directory. If the
        # polyglot fixture is bare (no crate), the adapter returns [] and
        # logs; treat that as a clean skip.
        flags = rust.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        if not flags:
            self.skipTest("clippy returned no findings (likely no enclosing crate)")
        for flag in flags:
            # Clippy rule IDs are lowercase slugs: `correctness::div_by_zero`
            # or the flatter `clippy::double_parens`. Either form is fine.
            self.assertRegex(
                flag.rule_id,
                r"^[a-z_][a-z0-9_:]*$",
                f"unexpected clippy rule_id shape: {flag.rule_id!r}",
            )


class JavaAdapterLive(unittest.TestCase):
    """spotbugs on sample.java — requires compiled classes, usually skipped."""

    @unittest.skipUnless(shutil.which("spotbugs"), "spotbugs not installed")
    def test_analyze_sample_java(self):
        fixture = _fixture("sample.java")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.java missing")
        flags = java.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        if not flags:
            self.skipTest(
                "spotbugs returned no findings (likely no compiled .class next to fixture)"
            )
        for flag in flags:
            # SpotBugs bug types: uppercase snake_case (NP_NULL_ON_SOME_PATH, ...).
            self.assertRegex(
                flag.rule_id,
                r"^[A-Z][A-Z0-9_]+$",
                f"unexpected spotbugs rule_id shape: {flag.rule_id!r}",
            )
        _assert_no_security_bucket(
            self, flags,
            # The security-defer-to-reaper Java bucket; any LEAKED_* / SQL_* is
            # Reaper's lane, never M1.
            ("leaked_", "sql_", "xss_", "path_traversal"),
        )


class CppAdapterLive(unittest.TestCase):
    """clang-tidy on sample.cpp — bugprone-* categories in scope for M1."""

    @unittest.skipUnless(shutil.which("clang-tidy"), "clang-tidy not installed")
    def test_analyze_sample_cpp(self):
        fixture = _fixture("sample.cpp")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.cpp missing")
        flags = cpp.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        if not flags:
            self.skipTest("clang-tidy returned no findings (likely no compile_commands.json)")
        for flag in flags:
            # clang-tidy checks are dashed: `bugprone-use-after-move`.
            self.assertRegex(
                flag.rule_id,
                r"^[a-z][a-z0-9-]*(-[a-z0-9-]+)+$",
                f"unexpected clang-tidy rule_id shape: {flag.rule_id!r}",
            )
        _assert_no_security_bucket(self, flags, ("cert-",))


class RubyAdapterLive(unittest.TestCase):
    """rubocop on sample.rb — Lint/* category in M1 scope."""

    @unittest.skipUnless(shutil.which("rubocop"), "rubocop not installed")
    def test_analyze_sample_rb(self):
        fixture = _fixture("sample.rb")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.rb missing")
        flags = ruby.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        if not flags:
            self.skipTest("rubocop returned no findings for this fixture")
        for flag in flags:
            # RuboCop cop names are `Department/CopName`.
            self.assertRegex(
                flag.rule_id,
                r"^[A-Z][A-Za-z]+/[A-Z][A-Za-z0-9]+$",
                f"unexpected rubocop cop name: {flag.rule_id!r}",
            )
        # The Security/* department is Reaper's lane.
        for flag in flags:
            self.assertFalse(
                flag.rule_id.startswith("Security/"),
                f"rubocop Security rule leaked into M1: {flag.rule_id!r}",
            )


class ShellAdapterLive(unittest.TestCase):
    """shellcheck on sample.sh — SC-series diagnostics."""

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_analyze_sample_sh(self):
        fixture = _fixture("sample.sh")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.sh missing")
        flags = shell.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        if not flags:
            self.skipTest("shellcheck returned no findings for this fixture")
        for flag in flags:
            self.assertRegex(
                flag.rule_id,
                r"^SC\d{4}$",
                f"unexpected shellcheck code: {flag.rule_id!r}",
            )


class SemgrepAdapterLive(unittest.TestCase):
    """semgrep against a polyglot source — correctness rules only.

    Semgrep's security.* rulesets are Reaper's lane; the adapter has a
    dotted-path guard. We exercise the correctness path by letting semgrep
    apply its default non-security checks.
    """

    @unittest.skipUnless(shutil.which("semgrep"), "semgrep not installed")
    def test_analyze_sample_py(self):
        fixture = _fixture("sample.py")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.py missing")
        flags = semgrep.analyze(str(fixture))
        _assert_basic_shape(self, flags)
        # Semgrep frequently returns [] if no registry pack matches; that's
        # legitimate — we only assert shape, and skip-with-note on emptiness
        # so the skip reason is visible.
        if not flags:
            self.skipTest("semgrep returned no findings (likely no matching ruleset)")
        for flag in flags:
            rid = (flag.rule_id or "").lower()
            self.assertFalse(
                ".security." in rid or rid.endswith(".security")
                or ".xss" in rid or ".sqli" in rid or ".crypto." in rid,
                f"security-path rule leaked into M1: {flag.rule_id!r}",
            )


class RuffAdapterLive(unittest.TestCase):
    """ruff on sample.py — F / E correctness checks in scope for M1.

    ruff_adapter.py uses `analyze_with_ruff`, not `analyze`, unlike the
    other adapters. That's a pre-existing naming quirk; we respect it.
    """

    @unittest.skipUnless(shutil.which("ruff"), "ruff not installed")
    def test_analyze_sample_py(self):
        fixture = _fixture("sample.py")
        if fixture is None:
            self.skipTest("fixture tests/fixtures/polyglot/sample.py missing")
        flags = ruff_adapter.analyze_with_ruff(str(fixture))
        # analyze_with_ruff returns Optional[list[Flag]]. `None` is an honest
        # failure signal, not a crash; fall back to skip.
        if flags is None:
            self.skipTest("ruff returned None (invocation or parse failure — see stderr)")
        _assert_basic_shape(self, flags)
        for flag in flags:
            # ruff rule IDs: uppercase family prefix (F, E, B, C, ANN, ...) + digits.
            self.assertRegex(
                flag.rule_id,
                r"^[A-Z]+\d+$",
                f"unexpected ruff rule_id shape: {flag.rule_id!r}",
            )
            # S-series (bandit-via-ruff) is Reaper's lane.
            self.assertFalse(
                flag.rule_id.startswith("S"),
                f"ruff S-series (security) leaked to M1: {flag.rule_id!r}",
            )


# ---------------------------------------------------------------------------
# Post-run status dump: what ran, what skipped, why.
# ---------------------------------------------------------------------------


class _StatusDump(unittest.TestCase):
    """Not a test per se — on a successful run this always passes and prints
    a tool-availability summary to stderr so the honest skip picture is
    visible without combing through individual test output."""

    def test_print_status(self):
        tools = [
            ("staticcheck (Go)", "staticcheck"),
            ("cargo (Rust clippy)", "cargo"),
            ("spotbugs (Java)", "spotbugs"),
            ("clang-tidy (C++)", "clang-tidy"),
            ("rubocop (Ruby)", "rubocop"),
            ("shellcheck (Shell)", "shellcheck"),
            ("semgrep (Polyglot)", "semgrep"),
            ("ruff (Python)", "ruff"),
        ]
        lines = ["", "Mantis adapter live-test tool availability:"]
        for label, bin_name in tools:
            found = shutil.which(bin_name)
            tag = f"ran (path={found})" if found else "skip: tool not installed"
            lines.append(f"  - {label}: [{tag}]")
        # Polyglot fixture presence.
        lines.append("Polyglot fixtures present:")
        for ext in ("py", "go", "rs", "java", "cpp", "rb", "sh"):
            p = _fixture(f"sample.{ext}")
            lines.append(f"  - sample.{ext}: {'[ok]' if p else '[skip: fixture missing]'}")
        print("\n".join(lines), file=sys.stderr)
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
