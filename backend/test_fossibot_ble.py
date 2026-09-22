import unittest

import fossibot_ble


class FossibotBleParseTests(unittest.TestCase):
    def test_status_poll_crc_matches_known_vector(self):
        body = fossibot_ble.STATUS_POLL[:-2]
        crc = fossibot_ble.crc16_modbus(body)
        self.assertEqual(body, bytes.fromhex("110400000050"))
        self.assertEqual(crc.to_bytes(2, "big"), fossibot_ble.STATUS_POLL[-2:])

    def test_parse_soc_from_padded_status_frame(self):
        frame = bytearray(6 + 80 * 2)
        frame[0:2] = b"\x11\x04"
        offset = 6 + 56 * 2
        frame[offset : offset + 2] = (830).to_bytes(2, "big")
        self.assertEqual(fossibot_ble.parse_soc(bytes(frame)), 830)

    def test_parse_soc_finds_header_after_junk(self):
        junk = b"\x00\x01"
        frame = bytearray(6 + 80 * 2)
        frame[0:2] = b"\x11\x04"
        offset = 6 + 56 * 2
        frame[offset : offset + 2] = (12).to_bytes(2, "big")
        self.assertEqual(fossibot_ble.parse_soc(junk + bytes(frame)), 12)

    def test_parse_soc_waits_for_enough_bytes(self):
        self.assertIsNone(fossibot_ble.parse_soc(b"\x11\x04\x00\x00"))

    def test_name_filter(self):
        self.assertTrue(fossibot_ble.looks_like_station("POWER-0084"))
        self.assertTrue(fossibot_ble.looks_like_station("FOSSiBOT F2400"))
        self.assertFalse(fossibot_ble.looks_like_station("NowGo Storm Lite"))

    def test_parse_status_reads_solar_and_outputs(self):
        frame = bytearray(6 + 80 * 2)
        frame[0:2] = b"\x11\x04"

        def put(index: int, value: int) -> None:
            offset = 6 + index * 2
            frame[offset : offset + 2] = value.to_bytes(2, "big")

        put(3, 410)
        put(4, 187)
        put(6, 597)
        put(20, 42)
        put(24, 1)
        put(25, 0)
        put(26, 1)
        put(41, 0)
        put(48, 0x8000)
        put(56, 830)
        put(58, 90)
        put(59, 0)
        status = fossibot_ble.parse_status(bytes(frame), address="AA", name="POWER-0084")
        assert status is not None
        self.assertEqual(status.solar_watts, 187)
        self.assertEqual(status.ac_in_watts, 410)
        self.assertEqual(status.total_in_watts, 597)
        self.assertEqual(status.soc_percent, 83.0)
        self.assertTrue(status.usb_on)
        self.assertFalse(status.dc_on)
        self.assertTrue(status.ac_on)
        self.assertTrue(status.charging)
        self.assertEqual(status.raw_registers[4], 187)
        public = status.to_public()
        self.assertEqual(public["socPercent"], 83.0)
        self.assertEqual(public["solarWatts"], 187)
        self.assertEqual(public["acInWatts"], 410)
        self.assertEqual(public["inWatts"], 597)
        self.assertFalse(public["dcOn"])
        self.assertTrue(public["online"])

    def test_ac_on_from_capability_bit_when_toggle_is_zero(self):
        frame = bytearray(6 + 80 * 2)
        frame[0:2] = b"\x11\x04"
        offset = 6 + 41 * 2
        frame[offset : offset + 2] = (2148).to_bytes(2, "big")
        soc = 6 + 56 * 2
        frame[soc : soc + 2] = (22).to_bytes(2, "big")
        status = fossibot_ble.parse_status(bytes(frame))
        assert status is not None
        self.assertTrue(status.ac_on)
        self.assertFalse(status.usb_on)

    def test_solar_input_counts_as_charging(self):
        frame = bytearray(6 + 80 * 2)
        frame[0:2] = b"\x11\x04"
        offset = 6 + 4 * 2
        frame[offset : offset + 2] = (76).to_bytes(2, "big")
        status = fossibot_ble.parse_status(bytes(frame))
        assert status is not None
        self.assertTrue(status.charging)

    def test_bluez_device_path_uses_adapter_and_mac(self):
        self.assertEqual(
            fossibot_ble.bluez_device_path("/org/bluez/hci0", "F0:9E:9E:A5:D2:E6"),
            "/org/bluez/hci0/dev_F0_9E_9E_A5_D2_E6",
        )


if __name__ == "__main__":
    unittest.main()
