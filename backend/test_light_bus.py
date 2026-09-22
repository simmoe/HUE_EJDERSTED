import unittest
from unittest.mock import patch

import light_bus
from light_bus import LightCommand


class AfterAcPolicyTests(unittest.TestCase):
    def test_rising_edge_by_rule(self):
        self.assertTrue(
            light_bus.should_force_off_after_ac(
                by_rule=True, ac_was_on=False, ac_on=True, online=True
            )
        )

    def test_home_manual_ac_leaves_lights(self):
        self.assertFalse(
            light_bus.should_force_off_after_ac(
                by_rule=False, ac_was_on=False, ac_on=True, online=True
            )
        )

    def test_already_on_is_not_a_restore(self):
        self.assertFalse(
            light_bus.should_force_off_after_ac(
                by_rule=True, ac_was_on=True, ac_on=True, online=True
            )
        )

    def test_offline_fossibot_does_not_fire(self):
        self.assertFalse(
            light_bus.should_force_off_after_ac(
                by_rule=True, ac_was_on=False, ac_on=True, online=False
            )
        )


class ApplyRoutingTests(unittest.TestCase):
    def test_off_uses_tuya_brightness_zero(self):
        dev = {"id": "bf1", "name": "Flare", "protocol": "tuya", "localKey": "k"}
        with patch.object(light_bus.garden_lights, "set_brightness", return_value={"id": "bf1", "on": False}) as fn:
            light_bus.apply(dev, light_bus.OFF)
        fn.assert_called_once_with(dev, 0)

    def test_color_uses_tuya_set_color(self):
        dev = {"id": "bf1", "protocol": "flare"}
        cmd = LightCommand(on=True, hue=230, sat=75, brightness=40)
        with patch.object(light_bus.garden_lights, "set_color", return_value={"id": "bf1"}) as fn:
            light_bus.apply(dev, cmd)
        fn.assert_called_once_with(dev, 230, 75, 40)

    def test_unknown_protocol_is_offline(self):
        state = light_bus.apply({"id": "x", "protocol": "matter"}, LightCommand(on=False))
        self.assertFalse(state["online"])
        self.assertIn("ukendt protokol", state["error"])

    def test_zigbee_without_hub_is_offline(self):
        state = light_bus.apply({"id": "seng", "protocol": "zigbee"}, LightCommand(on=False))
        self.assertFalse(state["online"])
        self.assertEqual(state["protocol"], "zigbee")

    def test_apply_logs_source(self):
        dev = {"id": "bf1", "name": "Flare", "protocol": "tuya", "localKey": "k"}
        with patch.object(light_bus.garden_lights, "set_brightness", return_value={"id": "bf1", "on": False, "online": True, "brightness": 0}):
            with patch.object(light_bus.lights_log, "log") as log:
                light_bus.apply(dev, light_bus.OFF, source="kiosk.brightness")
        events = [call.args[0] for call in log.call_args_list]
        self.assertIn("apply", events)
        self.assertIn("apply.result", events)
        self.assertEqual(log.call_args_list[0].kwargs["source"], "kiosk.brightness")

    def test_all_off_and_online(self):
        self.assertTrue(
            light_bus.all_off_and_online(
                [{"id": "a", "online": True, "on": False, "any_on": False}]
            )
        )
        self.assertFalse(
            light_bus.all_off_and_online(
                [{"id": "a", "online": True, "on": True, "any_on": True}]
            )
        )
        self.assertFalse(light_bus.all_off_and_online([]))


if __name__ == "__main__":
    unittest.main()
