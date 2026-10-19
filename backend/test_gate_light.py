import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import gate_light

TZ = ZoneInfo("Europe/Copenhagen")


def _dt(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


class GateSceneTests(unittest.TestCase):
    def test_the_button_walks_cycles_and_skips_the_old_off_scene(self):
        scene = 1
        seen = []
        for _ in range(gate_light.SCENES + 1):
            seen.append(scene)
            scene = gate_light.next_scene(scene)
        self.assertEqual(seen, [1, 2, 3, 1])

    def test_empty_cycle_starts_at_one_only_when_tapped(self):
        self.assertEqual(gate_light.next_scene(0), 1)
        self.assertEqual(gate_light.next_scene(None), 1)

    def test_publish_packet_names_the_topic_and_the_scene(self):
        body = b"have_laage"
        packet = gate_light._packet(0x30, len(body).to_bytes(2, "big") + body + b"2")
        self.assertIn(b"have_laage", packet)
        self.assertTrue(packet.endswith(b"2"))

    def test_retained_command_uses_the_retain_flag(self):
        body = b"have_laage" + b"0"
        packet = gate_light._packet(0x31, len(b"have_laage").to_bytes(2, "big") + body)
        self.assertEqual(packet[0], 0x31)


class GateScheduleTests(unittest.TestCase):
    def test_auto_is_on_after_sunset_and_off_at_two(self):
        sunset_15 = _dt(18, 10)
        sunset_14 = _dt(18, 12, day=14)
        sunset_16 = _dt(18, 8, day=16)
        self.assertTrue(gate_light.auto_should_be_on(_dt(18, 11), sunset_15, sunset_14))
        self.assertTrue(gate_light.auto_should_be_on(_dt(1, 59, day=16), sunset_16, sunset_15))
        self.assertFalse(gate_light.auto_should_be_on(_dt(2, 0, day=16), sunset_16, sunset_15))
        self.assertFalse(gate_light.auto_should_be_on(_dt(15, 0), sunset_15, sunset_14))

    def test_auto_before_midnight_uses_todays_sunset(self):
        sunset = _dt(18, 10)
        self.assertFalse(gate_light.auto_should_be_on(_dt(17, 0), sunset, None))
        self.assertTrue(gate_light.auto_should_be_on(_dt(20, 0), sunset, None))

    def test_tænd_and_sluk_ignore_the_sun(self):
        sunset = _dt(18, 10)
        noon = _dt(12)
        self.assertTrue(gate_light.desired_on("on", noon, sunset, None))
        self.assertFalse(gate_light.desired_on("off", _dt(21), sunset, None))

    def test_auto_stays_off_when_the_clock_is_untrusted(self):
        sunset = _dt(18, 10)
        self.assertFalse(
            gate_light.desired_on("auto", _dt(21), sunset, None, clock_trusted=False)
        )

    def test_command_never_invents_a_cycle(self):
        sunset = _dt(18, 10)
        night = _dt(21)
        self.assertEqual(
            gate_light.desired_command("on", None, night, sunset, None), 0
        )
        self.assertEqual(
            gate_light.desired_command("auto", 4, night, sunset, None), 0
        )
        self.assertEqual(
            gate_light.desired_command("auto", 2, night, sunset, None), 2
        )
        self.assertEqual(
            gate_light.desired_command("off", 2, night, sunset, None), 0
        )
        self.assertEqual(
            gate_light.desired_command("on", 3, _dt(12), sunset, None), 3
        )


class GateControllerTests(unittest.TestCase):
    def setUp(self):
        self.published = []
        self.writes = []
        self.ctrl = gate_light.GateController(
            publish=self.published.append,
            persist=self.writes.append,
            clock_trusted=lambda: True,
        )

    def test_hydrate_does_not_write_or_invent_a_cycle(self):
        self.ctrl.hydrate({"mode": "off"})
        self.assertEqual(self.ctrl.mode, "off")
        self.assertIsNone(self.ctrl.cycle)
        self.assertEqual(self.writes, [])
        self.ctrl.hydrate({"mode": "on", "cycle": 2})
        self.assertEqual(self.ctrl.cycle, 2)

    def test_tænd_publishes_the_last_cycle_and_sluk_keeps_it(self):
        night = _dt(21)
        self.ctrl.hydrate({"mode": "auto", "cycle": 2})
        self.ctrl.set_mode("on", night)
        self.assertEqual(self.published, [2])
        self.ctrl.set_mode("off", night)
        self.assertEqual(self.published, [2, 0])
        self.assertEqual(self.ctrl.cycle, 2)
        cycle_writes = [row for row in self.writes if "cycle" in row]
        self.assertEqual(cycle_writes, [])

    def test_next_cycle_is_the_only_writer_of_cycle(self):
        self.ctrl.next_cycle(_dt(12))
        self.assertEqual(self.ctrl.cycle, 1)
        self.assertIn({"cycle": 1}, self.writes)
        self.ctrl.set_mode("on", _dt(12))
        self.assertEqual(self.ctrl.cycle, 1)
        self.assertEqual([row for row in self.writes if "cycle" in row], [{"cycle": 1}])

    def test_auto_turns_on_after_sunset_and_off_at_two(self):
        self.ctrl.hydrate({"mode": "auto", "cycle": 1})
        self.ctrl.apply(_dt(17))
        self.assertEqual(self.published, [0])
        self.ctrl.apply(_dt(21))
        self.assertEqual(self.published, [0, 1])
        self.ctrl.apply(_dt(2, 0, day=16))
        self.assertEqual(self.published[-1], 0)

    def test_apply_does_not_repeat_the_same_command(self):
        self.ctrl.hydrate({"mode": "on", "cycle": 3})
        self.ctrl.apply(_dt(12))
        self.ctrl.apply(_dt(12))
        self.assertEqual(self.published, [3])

    def test_coming_online_republishes_without_touching_cycle(self):
        self.ctrl.hydrate({"mode": "on", "cycle": 2})
        self.ctrl.apply(_dt(12))
        self.ctrl.apply(_dt(12), force=True)
        self.assertEqual(self.published, [2, 2])
        self.assertEqual([row for row in self.writes if "cycle" in row], [])

    def test_stale_heartbeat_marks_the_gate_offline(self):
        self.ctrl.set_online(True, at=10.0)
        self.assertTrue(self.ctrl.online)
        self.assertFalse(self.ctrl.mark_stale_if_needed(now=40.0, stale_s=75))
        self.assertTrue(self.ctrl.mark_stale_if_needed(now=90.0, stale_s=75))
        self.assertFalse(self.ctrl.online)
        self.assertIn({"online": False}, self.writes)

    def test_status_payload_is_the_mqtt_word(self):
        self.assertTrue(self.ctrl.note_status(b"online"))
        self.assertFalse(self.ctrl.note_status("offline"))
        self.assertIsNone(self.ctrl.note_status(b"2"))

    def test_snapshot_shows_offline_not_a_scene_number(self):
        self.ctrl.hydrate({"mode": "on", "cycle": 1})
        snap = self.ctrl.snapshot()
        self.assertEqual(snap["scene"], 1)
        self.assertEqual(snap["scenes"], 3)
        self.assertEqual(snap["status"], "offline")
        self.ctrl.set_online(True)
        self.assertEqual(self.ctrl.snapshot()["status"], "")

    def test_reconnect_uses_lan_even_when_mqtt_is_offline(self):
        self.ctrl = gate_light.GateController(
            publish=self.published.append,
            persist=self.writes.append,
            clock_trusted=lambda: True,
            probe_lan=lambda: {"ip": gate_light.GATE_IP, "mode": 0, "mqtt": False},
            peek_status=lambda: False,
        )
        self.ctrl.hydrate({"mode": "on", "cycle": 2})
        snap = self.ctrl.reconnect(_dt(12))
        self.assertTrue(snap["online"])
        self.assertEqual(self.published, [2])

    def test_reconnect_trusts_mqtt_when_lan_is_dark(self):
        self.ctrl = gate_light.GateController(
            publish=self.published.append,
            persist=self.writes.append,
            clock_trusted=lambda: True,
            probe_lan=lambda: None,
            peek_status=lambda: True,
        )
        snap = self.ctrl.reconnect(_dt(12))
        self.assertTrue(snap["online"])

    def test_reconnect_stays_offline_when_both_are_dark(self):
        self.ctrl = gate_light.GateController(
            publish=self.published.append,
            persist=self.writes.append,
            clock_trusted=lambda: True,
            probe_lan=lambda: None,
            peek_status=lambda: False,
        )
        snap = self.ctrl.reconnect(_dt(12))
        self.assertFalse(snap["online"])
        self.assertEqual(snap["status"], "offline")
        self.assertEqual(self.published, [])


class GateProbeParseTests(unittest.TestCase):
    def test_netprog_line(self):
        self.assertEqual(
            gate_light.parse_netprog("laage 192.168.8.146 mode 0 mqtt 1\n"),
            {"ip": "192.168.8.146", "mode": 0, "mqtt": True},
        )
        self.assertIsNone(gate_light.parse_netprog("no"))


if __name__ == "__main__":
    unittest.main()
