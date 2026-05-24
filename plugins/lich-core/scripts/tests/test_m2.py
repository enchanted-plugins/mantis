"""Tests for M2 Falleri Structural Diff (GumTree-lite)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from m2_structural_diff import diff  # noqa: E402


class M2Basics(unittest.TestCase):
    def test_identical_no_function_edits(self):
        src = "def a():\n    return 1\n"
        r = diff(src, src)
        self.assertEqual(r.substrate, "gumtree")
        func_edits = [e for e in r.edits if e.old_type == "FunctionDef" or e.new_type == "FunctionDef"]
        self.assertEqual(func_edits, [], f"unexpected function-level edits: {func_edits}")

    def test_add_function_emits_insert(self):
        old = "def a():\n    return 1\n"
        new = "def a():\n    return 1\ndef b():\n    return 2\n"
        r = diff(old, new)
        self.assertEqual(r.substrate, "gumtree")
        inserts = [e for e in r.edits if e.action == "insert" and e.new_type == "FunctionDef"]
        self.assertGreaterEqual(len(inserts), 1)

    def test_delete_function_emits_delete(self):
        old = "def a():\n    return 1\ndef b():\n    return 2\n"
        new = "def a():\n    return 1\n"
        r = diff(old, new)
        deletes = [e for e in r.edits if e.action == "delete" and e.old_type == "FunctionDef"]
        self.assertGreaterEqual(len(deletes), 1)

    def test_syntax_error_returns_parse_failed(self):
        r = diff("def broken(:\n", "def a(): pass\n")
        self.assertEqual(r.substrate, "parse-failed")
        self.assertEqual(r.edits, [])

    def test_result_is_json_serializable(self):
        import json
        from dataclasses import asdict
        r = diff("def a(): return 1\n", "def a(): return 2\n")
        serialized = json.dumps({"substrate": r.substrate, "edits": [asdict(e) for e in r.edits]})
        self.assertIn("substrate", serialized)


if __name__ == "__main__":
    unittest.main()
