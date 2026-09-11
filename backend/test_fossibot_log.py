import unittest
from datetime import datetime, timezone

import fossibot_log


class FossibotLogTests(unittest.TestCase):
    def test_sample_id_floors_to_five_minutes(self):
        stamp = datetime(2026, 8, 30, 12, 37, 44, tzinfo=timezone.utc)
        self.assertEqual(fossibot_log.sample_id(stamp), "20260830T1235")

    def test_should_log_first_sample_and_port_change(self):
        status = {"online": True, "acOn": True, "usbOn": False, "charging": True}
        self.assertTrue(
            fossibot_log.should_log(status, last_at=0, last_key=None, now=10, interval_sec=300)
        )
        key = fossibot_log.event_key(status)
        self.assertFalse(
            fossibot_log.should_log(status, last_at=10, last_key=key, now=20, interval_sec=300)
        )
        flipped = {**status, "acOn": False}
        self.assertTrue(
            fossibot_log.should_log(flipped, last_at=10, last_key=key, now=20, interval_sec=300)
        )

    def test_should_log_heartbeat(self):
        status = {"online": True, "acOn": True, "usbOn": True, "charging": False}
        key = fossibot_log.event_key(status)
        self.assertTrue(
            fossibot_log.should_log(status, last_at=0, last_key=key, now=300, interval_sec=300)
        )

    def test_policy_event_ac_off_at_low_soc(self):
        stamp = datetime(2026, 9, 11, 21, 4, 9, tzinfo=timezone.utc)
        fields = fossibot_log.policy_event_fields(
            {"socPercent": 14.6, "acOn": True, "solarWatts": 0, "outWatts": 31},
            want_ac_on=False,
            threshold_percent=15.0,
            pressed=True,
            source="floor",
            now=stamp,
        )
        self.assertEqual(fossibot_log.event_id(stamp), "20260911T210409")
        self.assertEqual(fields["kind"], "ac_off")
        self.assertEqual(fields["source"], "floor")
        self.assertEqual(fields["socPercent"], 14.6)
        self.assertEqual(fields["thresholdPercent"], 15.0)
        self.assertTrue(fields["acOnBefore"])
        self.assertTrue(fields["pressed"])
        self.assertEqual(fields["error"], "")

    def test_policy_event_failed_press_keeps_error(self):
        fields = fossibot_log.policy_event_fields(
            {"socPercent": 26.0, "acOn": False},
            want_ac_on=True,
            threshold_percent=25.0,
            pressed=False,
            error="Kunne ikke nå SwitchBot",
        )
        self.assertEqual(fields["kind"], "ac_on")
        self.assertFalse(fields["pressed"])
        self.assertEqual(fields["error"], "Kunne ikke nå SwitchBot")

    def test_sample_fields_are_thin(self):
        fields = fossibot_log.sample_fields(
            {
                "online": True,
                "socPercent": 14.2,
                "solarWatts": 87,
                "outWatts": 22,
                "usbOn": True,
                "acOn": False,
                "charging": True,
                "error": "ignore me",
            },
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(fields["ts"], "2026-08-30T12:00:00Z")
        self.assertEqual(fields["socPercent"], 14.2)
        self.assertNotIn("error", fields)
