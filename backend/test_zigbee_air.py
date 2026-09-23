import unittest

import zigbee_air


class ScaleReadingTests(unittest.TestCase):
    def test_temp_hundredths(self):
        self.assertEqual(zigbee_air.scale_reading(1950, 100), 19.5)

    def test_pm25_is_raw(self):
        self.assertEqual(zigbee_air.scale_reading(12, 1), 12)

    def test_invalid_sentinel(self):
        self.assertIsNone(zigbee_air.scale_reading(0x8000, 100))
        self.assertIsNone(zigbee_air.scale_reading(None, 1))


class AirModelTests(unittest.TestCase):
    def test_vindstyrka(self):
        self.assertTrue(zigbee_air.is_air("VINDSTYRKA"))
        self.assertFalse(zigbee_air.is_air("VALLHORN Wireless Motion Sensor"))


class PublicStatusTests(unittest.TestCase):
    def test_offline_default(self):
        row = zigbee_air.public_status()
        self.assertTrue(row["ok"])
        self.assertFalse(row["online"])
        self.assertIsNone(row["pm25"])


if __name__ == "__main__":
    unittest.main()
