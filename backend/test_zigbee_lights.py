import unittest

import zigbee_lights


class DurableDbTests(unittest.TestCase):
    def test_path_is_outside_the_repo(self):
        path = zigbee_lights.durable_db_path()
        repo = zigbee_lights.Path(__file__).resolve().parents[1]
        self.assertNotEqual(path, repo / "zigbee.db")
        self.assertTrue(str(path).endswith(".local/share/hue/zigbee.db"))


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

    def test_vindstyrka_is_neither(self):
        self.assertFalse(zigbee_lights.is_lamp("VINDSTYRKA"))
        self.assertFalse(zigbee_lights.is_motion("VINDSTYRKA"))

    def test_hue_product_code_is_lamp(self):
        self.assertTrue(zigbee_lights.is_lamp("LWA001", "Signify Netherlands B.V."))
        self.assertTrue(zigbee_lights.is_lamp("LCT015", "Philips"))
        self.assertTrue(zigbee_lights.is_hue_lamp("LWA001"))
        self.assertFalse(zigbee_lights.is_lamp("RWL021", "Philips"))
        self.assertFalse(zigbee_lights.is_hue_lamp("RWL021", "Philips"))


class AdoptLampTests(unittest.TestCase):
    def test_hue_becomes_loft_and_leaves_toilet(self):
        devices = [
            {"id": "toilet", "ieee": zigbee_lights.KNOWN_TOILET},
            {"id": "seng", "ieee": zigbee_lights.KNOWN_SENG},
            {"id": "loft", "ieee": ""},
        ]
        self.assertEqual(
            zigbee_lights.adopt_lamp_id("LWA001", "Signify Netherlands B.V.", "00:17:88:01:0b:aa:bb:cc", devices),
            "loft",
        )
        self.assertEqual(
            zigbee_lights.adopt_lamp_id("STOFTMOLN ceiling/wall lamp WW24", "IKEA", zigbee_lights.KNOWN_TOILET, devices),
            "toilet",
        )

    def test_second_hue_does_not_steal_loft(self):
        devices = [{"id": "loft", "ieee": "00:17:88:01:0b:aa:bb:cc"}]
        self.assertIsNone(
            zigbee_lights.adopt_lamp_id("LCT015", "Philips", "00:17:88:01:0b:dd:ee:ff", devices)
        )


class LightEndpointTests(unittest.TestCase):
    def test_hue_style_endpoint_11(self):
        try:
            from zigpy.zcl.clusters.general import OnOff
        except ImportError:
            self.skipTest("zigpy missing")

        class Ep:
            def __init__(self, clusters):
                self.in_clusters = clusters

        class Dev:
            endpoints = {0: Ep({}), 11: Ep({OnOff.cluster_id: object()})}

        self.assertIs(zigbee_lights.light_endpoint(Dev()), Dev.endpoints[11])


class AttrValueTests(unittest.TestCase):
    def test_attr_value_reads_name_and_id(self):
        self.assertTrue(zigbee_lights.attr_value(({"on_off": True}, {}), "on_off", False))
        self.assertEqual(zigbee_lights.attr_value({0: 200}, "current_level"), 200)
        self.assertEqual(zigbee_lights.attr_value({0: 1950}, "measured_value"), 1950)


class CachedStateTests(unittest.TestCase):
    def test_report_while_mains_dark_cannot_turn_the_lamp_on(self):
        import light_bus

        light_bus.set_mains_dark(True)
        try:
            hub = zigbee_lights.ZigbeeHub()
            hub._cache["toilet"] = zigbee_lights.public_state(
                {"id": "toilet", "name": "Toilet"}, on=True, brightness=80, online=True
            )
            hub.note_report("toilet", on=True, brightness=80)
            state = hub._cache["toilet"]
            self.assertTrue(state["online"])
            self.assertFalse(state["on"])
            self.assertEqual(state["brightness"], 0)
        finally:
            light_bus.set_mains_dark(False)

    def test_cached_read_while_mains_dark_is_off(self):
        import light_bus

        light_bus.set_mains_dark(True)
        try:
            hub = zigbee_lights.ZigbeeHub()
            hub._cache["toilet"] = zigbee_lights.public_state(
                {"id": "toilet", "name": "Toilet"}, on=True, brightness=40, online=True
            )
            state = hub.cached({"id": "toilet", "name": "Toilet"})
            self.assertFalse(state["on"])
            self.assertEqual(state["brightness"], 0)
        finally:
            light_bus.set_mains_dark(False)

    def test_empty_cache_is_unread_not_off(self):
        hub = zigbee_lights.ZigbeeHub()
        hub._app = object()
        state = hub.cached({"id": "toilet", "name": "Toilet"})
        self.assertFalse(state["on"])
        self.assertFalse(state["online"])
        self.assertEqual(state["error"], "ikke aflæst")


if __name__ == "__main__":
    unittest.main()
