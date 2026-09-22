import unittest
from unittest.mock import patch

import kiosk_status


class ProbeTests(unittest.TestCase):
    def test_no_serial(self):
        row = kiosk_status.probe("")
        self.assertFalse(row["adb"])
        self.assertEqual(row["error"], "no serial")

    def test_remember_is_cached(self):
        kiosk_status.remember({"adb": True, "serial": "1:5555"})
        self.assertTrue(kiosk_status.cached()["adb"])

    def test_unreachable(self):
        def fake_run(cmd, timeout):
            return {"out": "unknown", "err": "No route to host"}

        with patch.object(kiosk_status, "_run", side_effect=fake_run):
            with patch.object(kiosk_status.kiosk_battery, "read_via_adb", return_value=None):
                row = kiosk_status.probe("192.168.8.135:5555")
        self.assertFalse(row["adb"])
        self.assertIn("No route", row["error"])
