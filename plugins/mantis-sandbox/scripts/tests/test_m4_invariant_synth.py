"""Tests for M4 Type-Reflected Invariant Synthesis.

Each test writes a synthetic target `.py` into a tmpdir and runs
`synthesize_typed` against it. No external deps; stdlib unittest only.
"""

from __future__ import annotations

import math
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# Add scripts/ to sys.path so `import m4_invariant_synth` works.
_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import m4_invariant_synth as m4  # noqa: E402


def _write(tmpdir: Path, name: str, source: str) -> str:
    path = tmpdir / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(path)


class M4TestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


class TestPrimitives(M4TestBase):
    def test_int_arg_produces_int_boundaries(self):
        p = _write(self.tmp, "m.py", """
            def f(x: int) -> int:
                return x + 1
        """)
        w = m4.synthesize_typed(p, "f", "")
        values = [witness["args"][0] for witness in w]
        self.assertIn(0, values)
        self.assertIn(-1, values)
        self.assertIn(2**31, values)
        self.assertIn(-(2**31), values)

    def test_float_arg_produces_float_boundaries(self):
        p = _write(self.tmp, "m.py", """
            def f(x: float) -> float:
                return x * 2
        """)
        w = m4.synthesize_typed(p, "f", "")
        values = [witness["args"][0] for witness in w]
        self.assertIn(0.0, values)
        self.assertTrue(any(v == math.inf for v in values))
        # NaN needs identity / math.isnan check (NaN != NaN).
        self.assertTrue(any(isinstance(v, float) and math.isnan(v) for v in values))

    def test_str_arg_produces_str_boundaries(self):
        p = _write(self.tmp, "m.py", """
            def f(x: str) -> int:
                return len(x)
        """)
        w = m4.synthesize_typed(p, "f", "")
        values = [witness["args"][0] for witness in w]
        self.assertIn("", values)
        self.assertIn("\x00", values)
        # Long-string boundary present.
        self.assertTrue(any(isinstance(v, str) and len(v) >= 1024 for v in values))


class TestContainers(M4TestBase):
    def test_list_int_produces_empty_and_nonempty(self):
        p = _write(self.tmp, "m.py", """
            def f(xs: list[int]) -> int:
                return sum(xs)
        """)
        w = m4.synthesize_typed(p, "f", "")
        first_args = [witness["args"][0] for witness in w]
        self.assertIn([], first_args)
        self.assertTrue(any(v == [0] for v in first_args))

    def test_dict_produces_empty_and_nonempty(self):
        p = _write(self.tmp, "m.py", """
            def f(d: dict[str, int]) -> int:
                return len(d)
        """)
        w = m4.synthesize_typed(p, "f", "")
        first_args = [witness["args"][0] for witness in w]
        self.assertIn({}, first_args)
        # At least one non-empty dict boundary.
        self.assertTrue(any(isinstance(v, dict) and len(v) >= 1 for v in first_args))


class TestOptionalUnion(M4TestBase):
    def test_optional_includes_none(self):
        p = _write(self.tmp, "m.py", """
            from typing import Optional
            def f(x: Optional[int]) -> int:
                return 0 if x is None else x
        """)
        w = m4.synthesize_typed(p, "f", "")
        values = [witness["args"][0] for witness in w]
        self.assertIn(None, values)
        # Should also include at least one int boundary from inner type.
        self.assertTrue(any(isinstance(v, int) for v in values))

    def test_pep604_union_none(self):
        # PEP 604 syntax requires Python 3.10+. Skip gracefully.
        if sys.version_info < (3, 10):
            self.skipTest("PEP 604 union syntax requires Python 3.10+")
        p = _write(self.tmp, "m.py", """
            def f(x: int | None) -> int:
                return 0 if x is None else x
        """)
        w = m4.synthesize_typed(p, "f", "")
        values = [witness["args"][0] for witness in w]
        self.assertIn(None, values)
        self.assertTrue(any(isinstance(v, int) for v in values))


class TestDataclassRecursion(M4TestBase):
    def test_dataclass_recurses_fields(self):
        p = _write(self.tmp, "m.py", """
            from dataclasses import dataclass
            @dataclass(frozen=True)
            class User:
                id: int
                name: str
            def f(u: User) -> int:
                return u.id
        """)
        w = m4.synthesize_typed(p, "f", "")
        self.assertTrue(len(w) >= 1, "expected at least one dataclass witness")
        inst = w[0]["args"][0]
        # Validate the dataclass instance has boundary-field values.
        self.assertEqual(getattr(inst, "id", None), 0)
        self.assertEqual(getattr(inst, "name", None), "")


class TestFallbackPaths(M4TestBase):
    def test_string_forward_ref_falls_back(self):
        p = _write(self.tmp, "m.py", """
            def f(x: "SomeClass") -> int:  # forward ref, never defined
                return 0
        """)
        # Should not raise — returns [] so generic path takes over.
        w = m4.synthesize_typed(p, "f", "")
        self.assertEqual(w, [])

    def test_no_annotations_falls_back(self):
        p = _write(self.tmp, "m.py", """
            def f(x):
                return x
        """)
        w = m4.synthesize_typed(p, "f", "")
        self.assertEqual(w, [])

    def test_unresolvable_type_falls_back(self):
        # References a name not importable / not defined in scope.
        p = _write(self.tmp, "m.py", """
            def f(x: UndefinedName) -> int:  # noqa
                return 0
        """)
        w = m4.synthesize_typed(p, "f", "")
        # Never raises; fallback is an empty list.
        self.assertEqual(w, [])


class TestFlagClassPriority(M4TestBase):
    def test_boundary_values_match_flag_class(self):
        p = _write(self.tmp, "m.py", """
            def divide(x: int, n: int) -> float:
                return x / n
        """)
        w = m4.synthesize_typed(p, "divide", "div-zero")
        self.assertTrue(len(w) >= 1)
        # Divisor heuristic: `n` is the divisor; first witness should
        # set n=0.
        first = w[0]
        # args positional: [x, n]; n is at index 1.
        self.assertEqual(first["args"][1], 0)
        self.assertIn("n=", first["reason"])


class TestDogfood(M4TestBase):
    """Dogfood: exercise high_level.py's parse_pair and confirm non-empty output."""

    def test_parse_pair_produces_string_boundaries(self):
        fixture = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "quality-ladder" / "high_level.py"
        if not fixture.exists():
            self.skipTest(f"fixture not present: {fixture}")
        w = m4.synthesize_typed(str(fixture), "parse_pair", "")
        self.assertTrue(len(w) >= 3, f"expected >=3 witnesses, got {len(w)}: {w}")
        first_args = [witness["args"][0] for witness in w]
        self.assertIn("", first_args)
        self.assertIn("\x00", first_args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
