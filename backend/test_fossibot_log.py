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
                "acInWatts": 410,
                "inWatts": 497,
                "outWatts": 22,
                "usbOn": True,
                "dcOn": True,
                "acOn": False,
                "charging": True,
                "error": "ignore me",
            },
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(fields["ts"], "2026-08-30T12:00:00Z")
        self.assertEqual(fields["socPercent"], 14.2)
        self.assertEqual(fields["acInWatts"], 410)
        self.assertEqual(fields["inWatts"], 497)
        self.assertTrue(fields["dcOn"])
        self.assertNotIn("error", fields)
        self.assertIsNone(fields["kioskBatteryPercent"])
        self.assertIsNone(fields["kioskCharging"])


class _FakeHttp:
    def __init__(self, fail_n: int = 0) -> None:
        self.urls: list[str] = []
        self.fail_n = fail_n

    async def patch(self, url, **kwargs):
        self.urls.append(url)
        if self.fail_n > 0:
            self.fail_n -= 1
            raise OSError("Temporary failure in name resolution")

        class _Resp:
            status_code = 200

        return _Resp()


class FossibotOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fossibot_outbox.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_sample_survives_reload(self):
        stamp = datetime(2026, 9, 16, 4, 54, tzinfo=timezone.utc)
        fields = fossibot_log.sample_fields(
            {"online": True, "socPercent": 14.8, "acOn": False},
            now=stamp,
        )
        sid = fossibot_log.sample_id(stamp)
        fossibot_log.queue_sample(self.path, fields, sid=sid)
        box = fossibot_log.load_outbox(self.path)
        self.assertEqual(box["samples"][sid]["socPercent"], 14.8)
        self.assertFalse(box["samples"][sid]["acOn"])

    async def test_persist_queues_when_firestore_is_down(self):
        http = _FakeHttp(fail_n=1)
        wrote = await fossibot_log.persist(
            {"online": True, "socPercent": 15.0, "acOn": True},
            firebase_config={"projectId": "p5-diary-ca5f7"},
            http_client=http,
            now=datetime(2026, 9, 16, 4, 54, tzinfo=timezone.utc),
            outbox_path=self.path,
        )
        self.assertFalse(wrote)
        box = fossibot_log.load_outbox(self.path)
        self.assertEqual(box["samples"]["20260916T0450"]["socPercent"], 15.0)

    async def test_flush_replays_queued_sample_and_event(self):
        stamp = datetime(2026, 9, 16, 4, 54, 20, tzinfo=timezone.utc)
        fossibot_log.queue_sample(
            self.path,
            fossibot_log.sample_fields({"socPercent": 15.0, "acOn": False}, now=stamp),
            sid=fossibot_log.sample_id(stamp),
        )
        fossibot_log.queue_event(
            self.path,
            fossibot_log.policy_event_fields(
                {"socPercent": 15.0, "acOn": True},
                want_ac_on=False,
                threshold_percent=15.0,
                pressed=True,
                source="floor",
                now=stamp,
            ),
            eid=fossibot_log.event_id(stamp),
        )
        http = _FakeHttp()
        ok = await fossibot_log.flush_outbox(
            self.path,
            firebase_config={"projectId": "p5-diary-ca5f7"},
            http_client=http,
        )
        self.assertTrue(ok)
        self.assertEqual(fossibot_log.load_outbox(self.path), {"samples": {}, "events": {}})
        joined = " ".join(http.urls)
        self.assertIn("/events/20260916T045420", joined)
        self.assertIn("/samples/20260916T0450", joined)
        self.assertEqual(sum(1 for u in http.urls if u.endswith("/fossibot_garden")), 0)

    async def test_flush_keeps_outbox_when_still_offline(self):
        stamp = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)
        fossibot_log.queue_sample(
            self.path,
            fossibot_log.sample_fields({"socPercent": 18.0}, now=stamp),
            sid=fossibot_log.sample_id(stamp),
        )
        http = _FakeHttp(fail_n=99)
        ok = await fossibot_log.flush_outbox(
            self.path,
            firebase_config={"projectId": "p5-diary-ca5f7"},
            http_client=http,
        )
        self.assertFalse(ok)
        self.assertIn("20260916T0800", fossibot_log.load_outbox(self.path)["samples"])
