"""Unit tests for the M1 walker.

Run (from repo root):
    python plugins/mantis-core/scripts/tests/test_m1_walker.py
or:
    python -m unittest discover -s plugins/mantis-core/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import sys
import textwrap
import unittest

# Make the sibling `scripts/` directory importable regardless of cwd.
# Parent package `mantis-core` is not a valid Python identifier (hyphen),
# so path manipulation is the only portable way.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPTS)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from m1_walker import analyze, analyze_path  # noqa: E402


FIXTURE_DIR = os.path.join(_REPO_ROOT, "tests", "fixtures", "quality-ladder")


def _rule_ids(flags) -> list[str]:
    return [f.rule_id for f in flags]


def _by_rule(flags) -> dict:
    out: dict = {}
    for f in flags:
        out.setdefault(f.rule_id, []).append(f)
    return out


class DivZeroTests(unittest.TestCase):
    def test_positive_len_denominator(self):
        src = textwrap.dedent("""
            def avg(xs):
                return sum(xs) / len(xs)
        """)
        flags = analyze(src, "<t>")
        ids = _rule_ids(flags)
        self.assertIn("PY-M1-001", ids)

    def test_negative_guarded_len(self):
        src = textwrap.dedent("""
            def avg(xs):
                if not xs:
                    return 0.0
                return sum(xs) / len(xs)
        """)
        flags = analyze(src, "<t>")
        self.assertNotIn("PY-M1-001", _rule_ids(flags))


class IndexOobTests(unittest.TestCase):
    def test_positive_listcomp_index(self):
        src = textwrap.dedent("""
            def first_match(users, uid):
                return [u for u in users if u.id == uid][0]
        """)
        flags = analyze(src, "<t>")
        self.assertIn("PY-M1-002", _rule_ids(flags))

    def test_positive_split_index(self):
        src = textwrap.dedent("""
            def pair(s):
                parts = s.split(",")
                return int(parts[0]) + int(parts[1])
        """)
        flags = analyze(src, "<t>")
        self.assertIn("PY-M1-002", _rule_ids(flags))

    def test_negative_guarded_split(self):
        src = textwrap.dedent("""
            def pair(s):
                parts = s.split(",", maxsplit=1)
                if len(parts) != 2:
                    return None
                return int(parts[0]) + int(parts[1])
        """)
        flags = analyze(src, "<t>")
        # After guard, parts is no longer flagged.
        self.assertNotIn("PY-M1-002", _rule_ids(flags))


class NullDerefTests(unittest.TestCase):
    def test_positive_dict_get_then_attr(self):
        src = textwrap.dedent("""
            def greet(store, uid):
                u = store.get(uid)
                return u.name
        """)
        flags = analyze(src, "<t>")
        self.assertIn("PY-M1-003", _rule_ids(flags))

    def test_positive_next_then_attr(self):
        src = textwrap.dedent("""
            def find(xs):
                u = next((x for x in xs if x.active), None)
                return u.name
        """)
        flags = analyze(src, "<t>")
        self.assertIn("PY-M1-003", _rule_ids(flags))

    def test_negative_guarded_none(self):
        src = textwrap.dedent("""
            def greet(store, uid):
                u = store.get(uid)
                if u is None:
                    return "?"
                return u.name
        """)
        flags = analyze(src, "<t>")
        self.assertNotIn("PY-M1-003", _rule_ids(flags))


class FixtureTests(unittest.TestCase):
    def test_bad_py_has_expected_flags(self):
        path = os.path.join(FIXTURE_DIR, "bad.py")
        flags = analyze_path(path)
        self.assertGreaterEqual(
            len(flags), 4,
            msg=f"expected >=4 flags on bad.py, got {len(flags)}: "
                f"{[(f.line, f.rule_id) for f in flags]}",
        )
        by_rule = _by_rule(flags)
        # Div-zero at L2 (len(nums) denominator)
        self.assertTrue(
            any(f.line == 2 and f.rule_id == "PY-M1-001"
                for f in by_rule.get("PY-M1-001", [])),
            msg=f"expected div-zero at L2; flags: {[(f.line, f.rule_id) for f in flags]}",
        )
        # Index-oob at L6 (listcomp[0])
        self.assertTrue(
            any(f.line == 6 and f.rule_id == "PY-M1-002"
                for f in by_rule.get("PY-M1-002", [])),
        )
        # Index-oob at L11 (parts[0]/parts[1] from split)
        self.assertTrue(
            any(f.line == 11 and f.rule_id == "PY-M1-002"
                for f in by_rule.get("PY-M1-002", [])),
        )

    def test_high_level_py_has_zero_flags(self):
        path = os.path.join(FIXTURE_DIR, "high_level.py")
        flags = analyze_path(path)
        self.assertEqual(
            len(flags), 0,
            msg=f"expected 0 flags on high_level.py, got "
                f"{[(f.line, f.rule_id, f.function) for f in flags]}",
        )


if __name__ == "__main__":
    unittest.main()
