import unittest
from datetime import datetime, timezone

import air_log
import fossibot_log


class AirLogTests(unittest.TestCase):
    def test_sample_id_shared_with_fossibot(self):
        stamp = datetime(2026, 8, 30, 12, 37, 44, tzinfo=timezone.utc)
        sid = fossibot_log.sample_id(stamp)
        self.assertEqual(sid, "20260830T1235")
        fields = air_log.sample_fields({"online": True}, now=stamp)
        self.assertEqual(fields["ts"], "2026-08-30T12:37:44Z")

    def test_should_log_on_change_and_heartbeat(self):
        status = {
            "online": True,
            "tempC": 15.2,
            "humidity": 66.0,
            "pm25": 3.0,
            "voc": 120.0,
            "outdoorHumidity": 82.0,
            "outdoorSource": "dmi",
        }
        self.assertTrue(
            air_log.should_log(status, last_at=0, last_key=None, now=10, interval_sec=300)
        )
        key = air_log.sample_key(status)
        self.assertFalse(
            air_log.should_log(status, last_at=10, last_key=key, now=20, interval_sec=300)
        )
        warmer = {**status, "tempC": 15.8}
        self.assertTrue(
            air_log.should_log(warmer, last_at=10, last_key=key, now=20, interval_sec=300)
        )
        self.assertTrue(
            air_log.should_log(status, last_at=10, last_key=key, now=310, interval_sec=300)
        )

    def test_sample_fields_are_thin(self):
        fields = air_log.sample_fields(
            {
                "online": True,
                "tempC": 15.0,
                "humidity": 66,
                "pm25": 4,
                "voc": 99,
                "outdoorHumidity": 82,
                "outdoorSource": "dmi",
                "error": "ignore",
                "ok": True,
            },
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(fields["ts"], "2026-08-30T12:00:00Z")
        self.assertEqual(fields["tempC"], 15.0)
        self.assertEqual(fields["outdoorSource"], "dmi")
        self.assertNotIn("error", fields)


if __name__ == "__main__":
    unittest.main()
