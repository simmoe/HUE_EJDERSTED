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


if __name__ == "__main__":
    unittest.main()
