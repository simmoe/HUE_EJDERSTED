import unittest

import zigbee_lights


class ClassifyIkeaTests(unittest.TestCase):
    def test_motion_not_lamp(self):
        self.assertTrue(zigbee_lights.is_motion("TRADFRI motion sensor"))
        self.assertFalse(zigbee_lights.is_lamp("TRADFRI motion sensor"))
        self.assertTrue(zigbee_lights.is_motion("VALLHORN Wireless Motion Sensor"))

    def test_bulb_is_lamp(self):
        self.assertTrue(zigbee_lights.is_lamp("TRADFRI bulb E27 WW 806lm"))
        self.assertTrue(zigbee_lights.is_lamp("STOFTMOLN ceiling/wall lamp WW24"))
        self.assertFalse(zigbee_lights.is_motion("TRADFRI bulb E27 WW 806lm"))

    def test_rodret_is_neither(self):
        self.assertFalse(zigbee_lights.is_lamp("RODRET wireless dimmer"))
        self.assertFalse(zigbee_lights.is_motion("RODRET wireless dimmer"))


class LastSeenTests(unittest.TestCase):
    def test_awake_within_window(self):
        class Device:
            last_seen = 1000.0

        self.assertTrue(zigbee_lights.sensor_is_awake(Device(), now=1030.0))
        self.assertFalse(zigbee_lights.sensor_is_awake(Device(), now=1300.0))

    def test_missing_last_seen_is_asleep(self):
        class Device:
            last_seen = None

        self.assertFalse(zigbee_lights.sensor_is_awake(Device()))

    def test_attr_value_reads_name_and_id(self):
        self.assertTrue(zigbee_lights.attr_value(({"on_off": True}, {}), "on_off", False))
        self.assertEqual(zigbee_lights.attr_value({0: 200}, "current_level"), 200)


class CachedStateTests(unittest.TestCase):
    def test_empty_cache_is_unread_not_off(self):
        hub = zigbee_lights.ZigbeeHub()
        hub._app = object()
        state = hub.cached({"id": "toilet", "name": "Toilet"})
        self.assertFalse(state["on"])
        self.assertFalse(state["online"])
        self.assertEqual(state["error"], "ikke aflæst")


if __name__ == "__main__":
    unittest.main()
