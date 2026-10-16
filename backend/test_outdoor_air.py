import time
import unittest
from unittest.mock import AsyncMock

import outdoor_air


class ParseHumidityTests(unittest.TestCase):
    def test_reads_dmi_current(self):
        self.assertEqual(
            outdoor_air.parse_humidity(
                {"current": {"relative_humidity_2m": 91, "temperature_2m": 14.6}}
            ),
            91,
        )

    def test_rejects_junk(self):
        self.assertIsNone(outdoor_air.parse_humidity({}))
        self.assertIsNone(outdoor_air.parse_humidity({"current": {"relative_humidity_2m": 140}}))
        self.assertIsNone(outdoor_air.parse_humidity({"current": {"relative_humidity_2m": "x"}}))


class _Resp:
    def __init__(self, humidity: float, status_code: int = 200):
        self.status_code = status_code
        self._humidity = humidity

    def json(self):
        return {"current": {"relative_humidity_2m": self._humidity}}


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        outdoor_air.reset_cache()

    async def test_uses_the_http_body_once(self):
        http = AsyncMock()
        http.get = AsyncMock(return_value=_Resp(73))
        first = await outdoor_air.snapshot(http)
        second = await outdoor_air.snapshot(http)
        self.assertEqual(first["outdoorHumidity"], 73)
        self.assertEqual(first["outdoorSource"], "dmi")
        self.assertEqual(second["outdoorHumidity"], 73)
        self.assertEqual(http.get.await_count, 1)

    async def test_keeps_last_good_reading_when_the_fetch_fails(self):
        http = AsyncMock()
        http.get = AsyncMock(return_value=_Resp(60))
        await outdoor_air.snapshot(http)
        outdoor_air._cache_at = time.monotonic() - outdoor_air.CACHE_S - 1
        http.get = AsyncMock(side_effect=OSError("down"))
        row = await outdoor_air.snapshot(http)
        self.assertEqual(row["outdoorHumidity"], 60)
