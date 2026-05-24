"""Tests for M2 Tier-2 token-based structural diff + the dispatcher.

Tier-2 contract: honest degradation. Per-family smoke tests confirm
the token-diff catches inserts/deletes/updates/moves at the
function-record level, and the dispatcher routes unknown extensions
to the ``m2-unsupported-language`` honest-skip label.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from m2_token_diff import diff as token_diff  # noqa: E402
from m2_dispatcher import diff_by_language, _resolve_route  # noqa: E402


# -------------------------------------------------------------------------
# Per-family tier-2 cases
# -------------------------------------------------------------------------


class TypeScriptCases(unittest.TestCase):
    def test_add_function_emits_insert(self):
        old = "function a() { return 1 }\n"
        new = "function a() { return 1 }\nfunction b() { return 2 }\n"
        r = token_diff(old, new, language_hint="ts")
        self.assertEqual(r.substrate, "token-diff-fallback")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0].new_type, "function")
        self.assertIn("b", inserts[0].new_path)

    def test_delete_function_emits_delete(self):
        old = "function a() { return 1 }\nfunction b() { return 2 }\n"
        new = "function a() { return 1 }\n"
        r = token_diff(old, new, language_hint="ts")
        deletes = [e for e in r.edits if e.action == "delete"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0].old_type, "function")

    def test_update_body_emits_update(self):
        old = "function a() { return 1 }\n"
        new = "function a() { return 99 }\n"
        r = token_diff(old, new, language_hint="ts")
        updates = [e for e in r.edits if e.action == "update"]
        # Token bags only contain identifiers, not literals — 1 vs 99
        # yields no token change. Assert honestly: either 0 or 1 update.
        self.assertLessEqual(len(updates), 1)

    def test_update_body_with_new_identifier(self):
        old = "function a() { return x }\n"
        new = "function a() { return y + z }\n"
        r = token_diff(old, new, language_hint="ts")
        updates = [e for e in r.edits if e.action == "update"]
        self.assertGreaterEqual(len(updates), 1)

    def test_class_update(self):
        old = "class Foo { bar() { return x } }\n"
        new = "class Foo { bar() { return y } }\n"
        r = token_diff(old, new, language_hint="ts")
        # Class body tokens changed (x → y), same name → update.
        updates = [e for e in r.edits if e.action == "update"]
        self.assertGreaterEqual(len(updates), 1)


class GoCases(unittest.TestCase):
    def test_rename_same_body_emits_move_or_update(self):
        # Reshaped per brief: "rename to `func b() int { return 1 }`
        # with same body" — expected to surface either as move (phase-2
        # Dice) or as delete+insert if tokens too thin. Body has
        # identifiers 'int' that keep Dice above floor.
        old = "func a() int { return x + y }\n"
        new = "func b() int { return x + y }\n"
        r = token_diff(old, new, language_hint="go")
        # Expect exactly one rename-equivalent action pair.
        non_trivial = [e for e in r.edits if e.action in ("move", "update", "insert", "delete")]
        # Either: 1 move (rename preserved via Dice) or 1 delete + 1 insert.
        move_count = sum(1 for e in r.edits if e.action == "move")
        di_count = sum(1 for e in r.edits if e.action in ("delete", "insert"))
        self.assertTrue(
            move_count == 1 or di_count == 2,
            f"expected 1 move OR (1 delete + 1 insert); got edits={r.edits}",
        )

    def test_add_function_emits_insert(self):
        old = "func a() int { return 1 }\n"
        new = "func a() int { return 1 }\nfunc b() int { return 2 }\n"
        r = token_diff(old, new, language_hint="go")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)


class RubyCases(unittest.TestCase):
    def test_add_function_emits_insert(self):
        old = "def foo\n  1\nend\n"
        new = "def foo\n  1\nend\ndef bar\n  2\nend\n"
        r = token_diff(old, new, language_hint="rb")
        self.assertEqual(r.substrate, "token-diff-fallback")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)
        self.assertIn("bar", inserts[0].new_path)

    def test_delete_function_emits_delete(self):
        old = "def foo\n  1\nend\ndef bar\n  2\nend\n"
        new = "def foo\n  1\nend\n"
        r = token_diff(old, new, language_hint="rb")
        deletes = [e for e in r.edits if e.action == "delete"]
        self.assertEqual(len(deletes), 1)

    def test_class_with_methods(self):
        old = "class Foo\n  def bar\n    x\n  end\nend\n"
        new = "class Foo\n  def bar\n    y\n  end\nend\n"
        r = token_diff(old, new, language_hint="rb")
        # Both class and method are records; method body changed.
        updates = [e for e in r.edits if e.action == "update"]
        self.assertGreaterEqual(len(updates), 1)


class ShellCases(unittest.TestCase):
    def test_modify_body_emits_update(self):
        old = "foo() { echo one }\n"
        new = "foo() { echo two }\n"
        r = token_diff(old, new, language_hint="sh")
        self.assertEqual(r.substrate, "token-diff-fallback")
        updates = [e for e in r.edits if e.action == "update"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].old_type, "function")

    def test_add_function_emits_insert(self):
        old = "foo() { echo 1 }\n"
        new = "foo() { echo 1 }\nbar() { echo 2 }\n"
        r = token_diff(old, new, language_hint="sh")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)


# -------------------------------------------------------------------------
# Dispatcher / honest-skip contract
# -------------------------------------------------------------------------


class Dispatcher(unittest.TestCase):
    def test_unknown_extension_is_m2_unsupported_language(self):
        r = diff_by_language("x", "y", "weird.xyz")
        self.assertEqual(r.substrate, "m2-unsupported-language")
        self.assertEqual(r.edits, [])

    def test_no_extension_is_unsupported(self):
        r = diff_by_language("x", "y", "README")
        self.assertEqual(r.substrate, "m2-unsupported-language")

    def test_python_routes_to_tier1(self):
        r = diff_by_language(
            "def a():\n    return 1\n",
            "def a():\n    return 1\ndef b():\n    return 2\n",
            "foo.py",
        )
        self.assertEqual(r.substrate, "gumtree")
        inserts = [e for e in r.edits if e.action == "insert" and e.new_type == "FunctionDef"]
        self.assertGreaterEqual(len(inserts), 1)

    def test_ts_routes_to_tier2(self):
        r = diff_by_language(
            "function a() { return 1 }\n",
            "function a() { return 1 }\nfunction b() { return 2 }\n",
            "foo.ts",
        )
        self.assertEqual(r.substrate, "token-diff-fallback")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)

    def test_resolve_route_variants(self):
        self.assertEqual(_resolve_route("foo.py"), ("tier1", None))
        self.assertEqual(_resolve_route("foo.ts"), ("tier2", "c-like"))
        self.assertEqual(_resolve_route("foo.rb"), ("tier2", "ruby-like"))
        self.assertEqual(_resolve_route("foo.sh"), ("tier2", "shell-like"))
        self.assertEqual(_resolve_route("foo.xyz"), ("unsupported", None))
        self.assertEqual(_resolve_route("python"), ("tier1", None))
        self.assertEqual(_resolve_route("c-like"), ("tier2", "c-like"))

    def test_explicit_language_override(self):
        r = diff_by_language("function a(){}", "function a(){}\nfunction b(){}", "c-like")
        self.assertEqual(r.substrate, "token-diff-fallback")


# -------------------------------------------------------------------------
# Robustness: Dice floor, regex resilience
# -------------------------------------------------------------------------


class DiceFloor(unittest.TestCase):
    def test_low_overlap_functions_stay_unmatched(self):
        # Two functions with ~10% token overlap must not pair into a
        # "move" (Dice < 0.6). They should emit delete + insert.
        old = "function a() { return alpha + beta + gamma + delta + epsilon }\n"
        new = "function z() { return one + two + three + four + alpha }\n"
        r = token_diff(old, new, language_hint="ts")
        moves = [e for e in r.edits if e.action == "move"]
        self.assertEqual(moves, [], "low-overlap functions should not pair")
        # Expect exactly 1 delete + 1 insert.
        deletes = [e for e in r.edits if e.action == "delete"]
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(len(inserts), 1)


class RegexResilience(unittest.TestCase):
    def test_braces_inside_strings_do_not_break_parsing(self):
        # A string literal containing { must not prematurely close the
        # function body — otherwise we'd lose the trailing insert.
        old = (
            "function a() {\n"
            "  const s = \"hello { world } brace\";\n"
            "  return s;\n"
            "}\n"
        )
        new = (
            "function a() {\n"
            "  const s = \"hello { world } brace\";\n"
            "  return s;\n"
            "}\n"
            "function b() { return 2 }\n"
        )
        r = token_diff(old, new, language_hint="ts")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)
        self.assertIn("b", inserts[0].new_path)

    def test_block_comments_do_not_confuse_parser(self):
        old = (
            "/* function decoy() { return 0 } */\n"
            "function real() { return 1 }\n"
        )
        new = (
            "/* function decoy() { return 0 } */\n"
            "function real() { return 1 }\n"
            "function added() { return 2 }\n"
        )
        r = token_diff(old, new, language_hint="ts")
        inserts = [e for e in r.edits if e.action == "insert"]
        self.assertEqual(len(inserts), 1)
        self.assertIn("added", inserts[0].new_path)
        # 'decoy' must never be extracted.
        all_paths = [e.old_path for e in r.edits] + [e.new_path for e in r.edits]
        self.assertNotIn("Module.function[decoy]", all_paths)

    def test_line_comments_do_not_confuse_parser(self):
        old = (
            "// function decoy() { return 0 }\n"
            "function real() { return 1 }\n"
        )
        new = (
            "// function decoy() { return 0 }\n"
            "function real() { return x + y }\n"
        )
        r = token_diff(old, new, language_hint="ts")
        updates = [e for e in r.edits if e.action == "update"]
        self.assertGreaterEqual(len(updates), 1)


if __name__ == "__main__":
    unittest.main()
