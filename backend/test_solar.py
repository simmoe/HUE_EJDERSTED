import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import solar


def _controller(tmp: str) -> solar.SolarController:
    cfg = {
        "gpioPin": 17,
        "activeHigh": True,
        "lat": 55.6761,
        "lon": 12.5683,
        "sunriseOffsetMin": 30,
        "sunsetOffsetMin": 90,
        "tz": "Europe/Copenhagen",
    }
    return solar.SolarController(cfg, Path(tmp) / "solar_state.json")


class SolarClockFailOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        solar.reset_clock_cache()

    def test_auto_stays_off_at_night_when_clock_is_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = _controller(tmp)
            ctrl.mode = "auto"
            night = datetime(2026, 8, 30, 1, 0, tzinfo=ctrl.tz)
            self.assertFalse(ctrl.desired_on(night, clock_trusted=True))

    def test_auto_fail_opens_when_clock_is_untrusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = _controller(tmp)
            ctrl.mode = "auto"
            night = datetime(2026, 8, 30, 1, 0, tzinfo=ctrl.tz)
            self.assertTrue(ctrl.desired_on(night, clock_trusted=False))

    def test_manual_off_wins_even_without_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = _controller(tmp)
            ctrl.mode = "off"
            night = datetime(2026, 8, 30, 1, 0, tzinfo=ctrl.tz)
            self.assertFalse(ctrl.desired_on(night, clock_trusted=False))
