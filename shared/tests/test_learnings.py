"""Stdlib-unittest tests for shared/learnings.py (Gauss Accumulation).

Covers: append round-trip per plugin, chronological read_all, export
dedup by (ts, plugin, code), rejection of non-F01..F14 codes, empty
export shape.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# Make `shared/` importable regardless of CWD.
_HERE = Path(__file__).resolve().parent         # shared/tests/
_SHARED = _HERE.parent                           # shared/
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import learnings as L  # noqa: E402


class _IsolatedRepoTest(unittest.TestCase):
    """Base: each test gets a fresh temp `repo` with plugins/*/state/."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "plugins").mkdir()
        (self.repo / "shared").mkdir()

        # Redirect module-level paths at runtime.
        self._orig_root = L._REPO_ROOT
        self._orig_agg = L._AGG_PATH
        L._REPO_ROOT = self.repo
        L._AGG_PATH = self.repo / "shared" / "learnings.json"

    def tearDown(self) -> None:
        L._REPO_ROOT = self._orig_root
        L._AGG_PATH = self._orig_agg
        self._tmp.cleanup()

    def _seed_plugin(self, name: str) -> Path:
        state = self.repo / "plugins" / name / "state"
        state.mkdir(parents=True, exist_ok=True)
        return state


class TestAppendRoundTrip(_IsolatedRepoTest):
    def test_append_and_read_one_plugin(self) -> None:
        self._seed_plugin("mantis-core")
        L.append("mantis-core", L.Learning(
            plugin="mantis-core", code="F02",
            hypothesis="fab-check", outcome="ok", counter="verify first",
        ))
        got = L.read_all("mantis-core")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].plugin, "mantis-core")
        self.assertEqual(got[0].code, "F02")
        self.assertEqual(got[0].hypothesis, "fab-check")

    def test_append_corrects_mismatched_plugin_attribution(self) -> None:
        self._seed_plugin("mantis-core")
        # Caller claims mantis-core but entry says mantis-rubric — the
        # write path forces consistency.
        L.append("mantis-core", L.Learning(
            plugin="mantis-rubric", code="F13",
            hypothesis="h", outcome="o", counter="c",
        ))
        got = L.read_all("mantis-core")
        self.assertEqual(got[0].plugin, "mantis-core")

    def test_append_creates_state_dir(self) -> None:
        # Do not pre-create the state dir — append must make it.
        L.append("mantis-newplugin", L.Learning(
            plugin="mantis-newplugin", code="F01",
            hypothesis="h", outcome="o", counter="c",
        ))
        self.assertTrue(
            (self.repo / "plugins" / "mantis-newplugin" / "state"
             / "learnings.jsonl").exists()
        )


class TestReadAllChronological(_IsolatedRepoTest):
    def test_file_order_preserved(self) -> None:
        self._seed_plugin("mantis-core")
        for i in range(5):
            L.append("mantis-core", L.Learning(
                plugin="mantis-core", code="F03",
                hypothesis=f"h{i}", outcome=f"o{i}", counter="c",
                ts=f"2026-04-20T00:00:0{i}+00:00",
            ))
        got = L.read_all("mantis-core")
        self.assertEqual([g.hypothesis for g in got],
                         ["h0", "h1", "h2", "h3", "h4"])

    def test_skips_corrupt_lines(self) -> None:
        state = self._seed_plugin("mantis-core")
        path = state / "learnings.jsonl"
        path.write_text(
            '{"plugin":"mantis-core","code":"F01","hypothesis":"ok",'
            '"outcome":"o","counter":"c","axis":"","ts":"t1"}\n'
            'not-json-at-all\n'
            '{"plugin":"mantis-core","code":"F02","hypothesis":"ok2",'
            '"outcome":"o","counter":"c","axis":"","ts":"t2"}\n',
            encoding="utf-8",
        )
        got = L.read_all("mantis-core")
        self.assertEqual(len(got), 2)
        self.assertEqual([g.hypothesis for g in got], ["ok", "ok2"])


class TestInvalidCode(_IsolatedRepoTest):
    def test_rejects_non_taxonomy_code(self) -> None:
        with self.assertRaises(ValueError):
            L.Learning(plugin="mantis-core", code="F99",
                       hypothesis="", outcome="", counter="")

    def test_rejects_empty_code(self) -> None:
        with self.assertRaises(ValueError):
            L.Learning(plugin="mantis-core", code="",
                       hypothesis="", outcome="", counter="")

    def test_safe_emit_swallows_invalid_code(self) -> None:
        # safe_emit must never raise, even on invalid input.
        self._seed_plugin("mantis-core")
        L.safe_emit(
            plugin="mantis-core", code="NOPE",
            hypothesis="h", outcome="o", counter="c",
        )
        # Nothing got logged because Learning() raised inside the try.
        self.assertEqual(L.read_all("mantis-core"), [])


class TestExportAggregated(_IsolatedRepoTest):
    def test_dedup_by_ts_plugin_code(self) -> None:
        self._seed_plugin("mantis-core")
        self._seed_plugin("mantis-sandbox")
        # Duplicate key: same ts + plugin + code written twice.
        for _ in range(2):
            L.append("mantis-core", L.Learning(
                plugin="mantis-core", code="F02",
                hypothesis="h", outcome="o", counter="c",
                ts="2026-04-20T00:00:00+00:00",
            ))
        # Different code at same ts — kept.
        L.append("mantis-core", L.Learning(
            plugin="mantis-core", code="F03",
            hypothesis="h", outcome="o", counter="c",
            ts="2026-04-20T00:00:00+00:00",
        ))
        # Different plugin, same ts + code — kept.
        L.append("mantis-sandbox", L.Learning(
            plugin="mantis-sandbox", code="F02",
            hypothesis="h", outcome="o", counter="c",
            ts="2026-04-20T00:00:00+00:00",
        ))
        snap = L.export_aggregated()
        # 3 unique, 1 duplicate dropped.
        self.assertEqual(len(snap["entries"]), 3)
        keys = {(e["ts"], e["plugin"], e["code"]) for e in snap["entries"]}
        self.assertEqual(keys, {
            ("2026-04-20T00:00:00+00:00", "mantis-core", "F02"),
            ("2026-04-20T00:00:00+00:00", "mantis-core", "F03"),
            ("2026-04-20T00:00:00+00:00", "mantis-sandbox", "F02"),
        })

    def test_empty_export_has_valid_schema(self) -> None:
        # No plugin dirs — snapshot still has the schema.
        snap = L.export_aggregated()
        self.assertIn("generated_at", snap)
        self.assertEqual(snap["entries"], [])
        # On-disk JSON parses and matches.
        loaded = json.loads(L._AGG_PATH.read_text(encoding="utf-8"))
        self.assertIn("generated_at", loaded)
        self.assertEqual(loaded["entries"], [])

    def test_export_snapshot_is_pretty_json(self) -> None:
        self._seed_plugin("mantis-core")
        L.append("mantis-core", L.Learning(
            plugin="mantis-core", code="F01",
            hypothesis="h", outcome="o", counter="c",
            ts="2026-04-20T00:00:00+00:00",
        ))
        L.export_aggregated()
        text = L._AGG_PATH.read_text(encoding="utf-8")
        # Pretty JSON = multiple lines with indentation.
        self.assertGreater(text.count("\n"), 3)
        self.assertIn("  ", text)

    def test_export_stable_sort(self) -> None:
        self._seed_plugin("mantis-core")
        self._seed_plugin("mantis-verdict")
        # Append in reverse chronological order.
        L.append("mantis-verdict", L.Learning(
            plugin="mantis-verdict", code="F11",
            hypothesis="h", outcome="o", counter="c",
            ts="2026-04-20T02:00:00+00:00",
        ))
        L.append("mantis-core", L.Learning(
            plugin="mantis-core", code="F14",
            hypothesis="h", outcome="o", counter="c",
            ts="2026-04-20T01:00:00+00:00",
        ))
        snap = L.export_aggregated()
        ts_order = [e["ts"] for e in snap["entries"]]
        self.assertEqual(ts_order, sorted(ts_order))


class TestCLI(_IsolatedRepoTest):
    def test_export_cli(self) -> None:
        self._seed_plugin("mantis-core")
        L.append("mantis-core", L.Learning(
            plugin="mantis-core", code="F04",
            hypothesis="drift", outcome="o", counter="c",
        ))
        rc = L.main(["export"])
        self.assertEqual(rc, 0)
        self.assertTrue(L._AGG_PATH.exists())

    def test_tail_cli_limits_to_n(self) -> None:
        self._seed_plugin("mantis-core")
        for i in range(5):
            L.append("mantis-core", L.Learning(
                plugin="mantis-core", code="F01",
                hypothesis=f"h{i}", outcome="o", counter="c",
            ))
        # Capture stdout.
        import io
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            rc = L.main(["tail", "--plugin", "mantis-core", "--n", "2"])
        self.assertEqual(rc, 0)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
