import tempfile
import unittest
from pathlib import Path

import lights_log


class LightsLogTests(unittest.TestCase):
    def setUp(self):
        lights_log.reset()
        lights_log._path = None
        lights_log._site = ""

    def test_recent_keeps_order_and_limit(self):
        lights_log.log("ac.edge", was=False, now=True, sweep=False)
        lights_log.log("apply", id="toilet", source="kiosk.brightness", on=False)
        events = lights_log.recent(1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "apply")
        self.assertEqual(events[0]["id"], "toilet")
        self.assertEqual(len(lights_log.recent(40)), 2)

    def test_empty_fields_are_dropped(self):
        row = lights_log.log("zigbee.bind", detail="", ok=False)
        self.assertNotIn("detail", row)
        self.assertFalse(row["ok"])

    def test_configure_reloads_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lights.jsonl"
            lights_log.configure(path, site="garden")
            lights_log.log("zigbee.read", id="seng", on=True, brightness=55)
            lights_log.reset()
            lights_log.configure(path, site="garden")
            events = lights_log.recent(10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "zigbee.read")
            self.assertEqual(events[0]["site"], "garden")
            self.assertTrue(events[0]["on"])
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
