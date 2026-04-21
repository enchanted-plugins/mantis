"""Tests for the local JSON-lines event bus.

Exercises:
  * publish/subscribe round-trip
  * topic prefix filtering (``mantis.`` vs ``reaper.``)
  * ``since`` timestamp filter
  * concurrent publishes (threads) — every event lands on its own line
  * persisted-line schema — five required fields, valid JSON

Stdlib-only. Safe to invoke with ``python shared/events/tests/test_bus.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


# Make the package importable when running this file as a script.
_HERE = Path(__file__).resolve()
_EVENTS_DIR = _HERE.parent.parent
_REPO_ROOT = _EVENTS_DIR.parent.parent
sys.path.insert(0, str(_REPO_ROOT))  # so `from shared.events import bus`
sys.path.insert(0, str(_EVENTS_DIR))  # so `import bus` works too

import bus as _bus  # noqa: E402  (script-mode import)


REQUIRED_FIELDS = {"topic", "payload", "ts", "source", "uuid"}


class BusPathMixin:
    """Give each test its own tmp bus file so runs cannot collide."""

    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.bus_path = Path(self._tmp.name) / "bus.jsonl"

    def tearDown(self) -> None:  # type: ignore[override]
        self._tmp.cleanup()


class TestPublishSubscribe(BusPathMixin, unittest.TestCase):

    def test_round_trip(self) -> None:
        ev = _bus.publish(
            "mantis.review.completed",
            {"file": "a.py", "verdict": "DEPLOY"},
            source="mantis-verdict",
            bus_path=self.bus_path,
        )
        self.assertEqual(ev.topic, "mantis.review.completed")
        self.assertEqual(ev.payload["verdict"], "DEPLOY")
        self.assertTrue(ev.uuid)
        self.assertTrue(ev.ts)

        received = list(_bus.subscribe(bus_path=self.bus_path))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].uuid, ev.uuid)
        self.assertEqual(received[0].payload, {"file": "a.py",
                                                 "verdict": "DEPLOY"})

    def test_multiple_events_preserve_order(self) -> None:
        for i in range(5):
            _bus.publish("mantis.x", {"i": i}, "test",
                         bus_path=self.bus_path)
        received = list(_bus.subscribe(bus_path=self.bus_path))
        self.assertEqual([e.payload["i"] for e in received], [0, 1, 2, 3, 4])

    def test_latest_returns_most_recent(self) -> None:
        for i in range(3):
            _bus.publish("mantis.rule.disabled",
                         {"rule": f"R{i}"}, "test",
                         bus_path=self.bus_path)
        latest = _bus.latest("mantis.rule.disabled", bus_path=self.bus_path)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.payload["rule"], "R2")

    def test_latest_returns_none_when_empty(self) -> None:
        self.assertIsNone(_bus.latest("nothing.here", bus_path=self.bus_path))


class TestTopicFiltering(BusPathMixin, unittest.TestCase):

    def test_prefix_match(self) -> None:
        _bus.publish("mantis.review.completed", {}, "src",
                     bus_path=self.bus_path)
        _bus.publish("mantis.sandbox.failed", {}, "src",
                     bus_path=self.bus_path)
        _bus.publish("reaper.vuln.detected", {}, "src",
                     bus_path=self.bus_path)

        mantis_only = list(_bus.subscribe(topic="mantis.",
                                           bus_path=self.bus_path))
        self.assertEqual(len(mantis_only), 2)
        self.assertTrue(all(e.topic.startswith("mantis.")
                             for e in mantis_only))

        reaper_only = list(_bus.subscribe(topic="reaper.",
                                           bus_path=self.bus_path))
        self.assertEqual(len(reaper_only), 1)

    def test_exact_topic_match(self) -> None:
        _bus.publish("mantis.review.completed", {}, "src",
                     bus_path=self.bus_path)
        _bus.publish("mantis.sandbox.failed", {}, "src",
                     bus_path=self.bus_path)

        exact = list(_bus.subscribe(topic="mantis.review.completed",
                                     bus_path=self.bus_path))
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].topic, "mantis.review.completed")

    def test_no_filter_returns_all(self) -> None:
        _bus.publish("a.b", {}, "src", bus_path=self.bus_path)
        _bus.publish("x.y", {}, "src", bus_path=self.bus_path)
        all_events = list(_bus.subscribe(bus_path=self.bus_path))
        self.assertEqual(len(all_events), 2)


class TestSinceFilter(BusPathMixin, unittest.TestCase):

    def test_since_filter(self) -> None:
        e1 = _bus.publish("t.one", {}, "src", bus_path=self.bus_path)
        # Ensure distinct timestamps on fast machines.
        time.sleep(0.01)
        e2 = _bus.publish("t.two", {}, "src", bus_path=self.bus_path)
        time.sleep(0.01)
        e3 = _bus.publish("t.three", {}, "src", bus_path=self.bus_path)

        after_e1 = list(_bus.subscribe(since=e1.ts, bus_path=self.bus_path))
        ids = [e.uuid for e in after_e1]
        self.assertIn(e2.uuid, ids)
        self.assertIn(e3.uuid, ids)
        self.assertNotIn(e1.uuid, ids)


class TestConcurrentPublishes(BusPathMixin, unittest.TestCase):
    """Concurrent writers must never produce a corrupted line."""

    def test_two_thread_publish_no_corruption(self) -> None:
        per_thread = 50
        def _pub(name: str) -> None:
            for i in range(per_thread):
                _bus.publish(f"concurrent.{name}", {"i": i}, source=name,
                             bus_path=self.bus_path)

        t1 = threading.Thread(target=_pub, args=("a",))
        t2 = threading.Thread(target=_pub, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Every non-empty line must be valid JSON with the 5 required fields.
        lines = [ln for ln in self.bus_path.read_text(encoding="utf-8")
                 .splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2 * per_thread)
        for ln in lines:
            data = json.loads(ln)  # must not raise
            self.assertEqual(set(data.keys()), REQUIRED_FIELDS)

        # Count matches per source — both threads landed.
        sources = [json.loads(ln)["source"] for ln in lines]
        self.assertEqual(sources.count("a"), per_thread)
        self.assertEqual(sources.count("b"), per_thread)


class TestSchema(BusPathMixin, unittest.TestCase):

    def test_every_line_has_required_fields(self) -> None:
        _bus.publish("s.a", {"k": 1}, "src", bus_path=self.bus_path)
        _bus.publish("s.b", {"k": 2}, "src2", bus_path=self.bus_path)
        _bus.publish("s.c", {}, "src3", bus_path=self.bus_path)

        for ln in self.bus_path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            data = json.loads(ln)
            self.assertEqual(set(data.keys()), REQUIRED_FIELDS)
            self.assertIsInstance(data["topic"], str)
            self.assertIsInstance(data["payload"], dict)
            self.assertIsInstance(data["ts"], str)
            self.assertIsInstance(data["source"], str)
            self.assertIsInstance(data["uuid"], str)

    def test_payload_must_be_dict(self) -> None:
        with self.assertRaises(TypeError):
            _bus.publish("bad", "not a dict", "src",  # type: ignore[arg-type]
                         bus_path=self.bus_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
