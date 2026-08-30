import unittest

import garden_lights


class GardenLightDpsTests(unittest.TestCase):
    def test_modern_rgbw_dps(self):
        on, bri = garden_lights.dps_to_light({"20": True, "22": 540})
        self.assertTrue(on)
        self.assertEqual(bri, 54)

    def test_off_is_zero_brightness_in_public_state(self):
        state = garden_lights.public_light(
            {"id": "abc", "name": "Flare", "localKey": "k"},
            dps={"20": False, "22": 800},
            online=True,
        )
        self.assertFalse(state["on"])
        self.assertEqual(state["brightness"], 0)
        self.assertTrue(state["online"])

    def test_missing_key_is_offline(self):
        state = garden_lights.public_light({"id": "abc", "name": "Flare", "localKey": ""})
        self.assertFalse(state["online"])
        self.assertEqual(state["error"], "mangler nøgle")

    def test_unpaired_placeholder(self):
        state = garden_lights.public_light({"id": "", "name": "Flare"})
        self.assertEqual(state["id"], "flare")
        self.assertEqual(state["error"], "ikke parret")

    def test_colour_dps_roundtrip(self):
        encoded = garden_lights.encode_colour_dps(230, 75, 80)
        self.assertEqual(encoded, "00e602ee0320")
        color = garden_lights.dps_to_color({"21": "colour", "24": encoded})
        self.assertTrue(color["has_color"])
        self.assertEqual(color["mode"], "colour")
        self.assertEqual(color["hue"], 230)
        self.assertEqual(color["sat"], 75)

    def test_white_mode_has_color_capability(self):
        color = garden_lights.dps_to_color({"21": "white", "22": 500, "24": "001e032003e8"})
        self.assertTrue(color["has_color"])
        self.assertEqual(color["mode"], "white")
        self.assertEqual(color["hue"], 30)

    def test_reconnect_without_devices_is_unpaired(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garden_lights.json"
            path.write_text('{"devices":[]}')
            old = garden_lights.CONFIG_FILE
            garden_lights.CONFIG_FILE = path
            garden_lights.reset_poll_state()
            try:
                state = garden_lights.reconnect()
                self.assertEqual(state["error"], "ikke parret")
                self.assertFalse(state["online"])
            finally:
                garden_lights.CONFIG_FILE = old

    def test_failed_read_keeps_last_good_within_grace(self):
        garden_lights.reset_poll_state()
        garden_lights._remember({
            "id": "abc",
            "name": "Flare",
            "online": True,
            "on": True,
            "any_on": True,
            "brightness": 40,
        })
        kept = garden_lights._or_last_good(
            {"id": "abc"},
            {"id": "abc", "online": False, "error": "unreachable"},
        )
        self.assertTrue(kept["online"])
        self.assertEqual(kept["brightness"], 40)

    def test_poll_snapshot_skips_when_too_soon(self):
        garden_lights.reset_poll_state()
        garden_lights._last_poll_at = garden_lights.time.monotonic()
        garden_lights._last_poll_ok = True
        self.assertIsNone(garden_lights.poll_snapshot())

    def test_adopt_fills_vacant_instead_of_duplicating(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garden_lights.json"
            path.write_text('{"devices":[{"id":"","name":"Flare","ip":"","localKey":"","version":"3.3"}]}')
            old = garden_lights.CONFIG_FILE
            garden_lights.CONFIG_FILE = path
            try:
                garden_lights.adopt_scan([{"id": "bf123", "ip": "192.168.8.138", "version": "3.3"}])
                devices = garden_lights.configured_devices()
                self.assertEqual(len(devices), 1)
                self.assertEqual(devices[0]["id"], "bf123")
                self.assertEqual(devices[0]["ip"], "192.168.8.138")
            finally:
                garden_lights.CONFIG_FILE = old
