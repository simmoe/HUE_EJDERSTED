import unittest

import kiosk_battery


DUMP = """
Current Battery Service state:
  AC powered: true
  USB powered: false
  Wireless powered: false
  status: 5
  level: 87
  scale: 100
  voltage: 4300
"""


class ParseDumpsysTests(unittest.TestCase):
    def test_ac_fullish(self):
        bat = kiosk_battery.parse_dumpsys(DUMP)
        self.assertEqual(bat["percent"], 87)
        self.assertTrue(bat["charging"])

    def test_usb_counts_as_charging(self):
        text = DUMP.replace("AC powered: true", "AC powered: false").replace(
            "USB powered: false", "USB powered: true"
        )
        bat = kiosk_battery.parse_dumpsys(text)
        self.assertTrue(bat["charging"])

    def test_unplugged(self):
        text = DUMP.replace("AC powered: true", "AC powered: false")
        bat = kiosk_battery.parse_dumpsys(text)
        self.assertEqual(bat["percent"], 87)
        self.assertFalse(bat["charging"])

    def test_missing_level(self):
        self.assertIsNone(kiosk_battery.parse_dumpsys("AC powered: true\n"))


if __name__ == "__main__":
    unittest.main()
