import tempfile
import unittest
from pathlib import Path

import power

WALL = 1_700_000_000.0


def hold(ac_on: bool, *, until: float = WALL + 3600, duration: str = "1h") -> power.Hold:
    return power.Hold(ac_on=ac_on, until=until, duration=duration)


class DesiredAcTests(unittest.TestCase):
    def want(self, soc, h=None):
        return power.desired_ac(soc, hold=h, wall=WALL)

    def test_idle_without_soc_and_hold(self):
        self.assertIsNone(self.want(None))

    def test_resume_at_or_above_25(self):
        for soc in (25.0, 25.1, 99.0):
            d = self.want(soc)
            self.assertTrue(d.ac_on)
            self.assertEqual(d.source, power.SOURCE_RULE)
            self.assertEqual(d.threshold, 25.0)

    def test_floor_at_or_below_15(self):
        for soc in (15.0, 14.9, 0.0):
            d = self.want(soc)
            self.assertFalse(d.ac_on)
            self.assertEqual(d.source, power.SOURCE_FLOOR)
            self.assertEqual(d.threshold, 15.0)

    def test_band_has_no_opinion(self):
        for soc in (15.1, 20.0, 24.9):
            self.assertIsNone(self.want(soc))

    def test_hold_off_beats_resume(self):
        d = self.want(80.0, hold(False))
        self.assertFalse(d.ac_on)
        self.assertEqual(d.source, power.SOURCE_HOLD)
        self.assertIsNone(d.threshold)

    def test_hold_on_beats_band(self):
        self.assertTrue(self.want(20.0, hold(True)).ac_on)

    def test_hold_works_without_soc(self):
        self.assertTrue(self.want(None, hold(True)).ac_on)

    def test_floor_beats_hold_on(self):
        d = self.want(12.0, hold(True))
        self.assertFalse(d.ac_on)
        self.assertEqual(d.source, power.SOURCE_FLOOR)

    def test_expired_hold_is_ignored(self):
        expired = hold(False, until=WALL - 1)
        self.assertEqual(self.want(80.0, expired).source, power.SOURCE_RULE)


class DecidePressTests(unittest.TestCase):
    def _press(self, **kwargs):
        base = dict(
            online=True,
            soc=80.0,
            ac_on=False,
            hold=None,
            now=1000.0,
            wall=WALL,
            last_press_at=0.0,
        )
        base.update(kwargs)
        return power.decide_press(**base)

    def test_press_to_turn_ac_on(self):
        self.assertTrue(self._press(soc=80.0, ac_on=False).ac_on)

    def test_press_to_turn_ac_off(self):
        self.assertFalse(self._press(soc=10.0, ac_on=True).ac_on)

    def test_already_correct_is_idle(self):
        self.assertIsNone(self._press(soc=80.0, ac_on=True))
        self.assertIsNone(self._press(soc=10.0, ac_on=False))

    def test_band_never_presses(self):
        self.assertIsNone(self._press(soc=20.0, ac_on=True))
        self.assertIsNone(self._press(soc=20.0, ac_on=False))

    def test_offline_never_presses(self):
        self.assertIsNone(self._press(online=False, soc=10.0, ac_on=True))

    def test_hold_off_presses_off_at_high_soc(self):
        d = self._press(soc=90.0, ac_on=True, hold=hold(False))
        self.assertFalse(d.ac_on)
        self.assertEqual(d.source, power.SOURCE_HOLD)

    def test_cooldown_blocks_repeat(self):
        self.assertIsNone(self._press(now=1000.0, last_press_at=950.0))
        self.assertIsNotNone(self._press(now=1000.0, last_press_at=900.0))


class PowerPolicyTests(unittest.TestCase):
    def test_hold_survives_restart_and_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "power_state.json"
            policy = power.PowerPolicy(path)
            self.assertIsNone(policy.hold)
            policy.set_hold(False, until=WALL + 7200, duration="2h")
            again = power.PowerPolicy(path)
            self.assertEqual(again.hold.duration, "2h")
            self.assertFalse(again.hold.ac_on)
            self.assertIsNotNone(again.active_hold(WALL + 10))
            self.assertIsNone(again.active_hold(WALL + 7201))
            self.assertIsNone(power.PowerPolicy(path).hold)

    def test_old_autonomous_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "power_state.json"
            path.write_text('{"autonomous": true}', encoding="utf-8")
            self.assertIsNone(power.PowerPolicy(path).hold)

    def test_floor_burns_hold_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = power.PowerPolicy(Path(tmp) / "power_state.json")
            policy.set_hold(True, until=WALL + 3600, duration="1h")
            d = policy.decide({"online": True, "socPercent": 14.0, "acOn": True}, now=10.0, wall=WALL)
            self.assertEqual(d.source, power.SOURCE_FLOOR)
            self.assertIsNone(policy.hold)
            # …so climbing back to 16 % does not turn it on again.
            self.assertIsNone(
                policy.decide({"online": True, "socPercent": 16.0, "acOn": False}, now=200.0, wall=WALL)
            )

    def test_decide_uses_cooldown_and_remembers_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = power.PowerPolicy(Path(tmp) / "power_state.json")
            status = {"online": True, "socPercent": 30, "acOn": False}
            d = policy.decide(status, now=10.0, wall=WALL)
            self.assertEqual(d.source, power.SOURCE_RULE)
            policy.note_press(d.source, now=10.0)
            self.assertIsNone(policy.decide(status, now=20.0, wall=WALL))
            self.assertTrue(policy.pressed_recently(power.SOURCE_RULE, now=100.0))
            self.assertFalse(policy.pressed_recently(power.SOURCE_HOLD, now=100.0))
            self.assertFalse(policy.pressed_recently(power.SOURCE_RULE, now=400.0))

    def test_status_reports_hold_and_want(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = power.PowerPolicy(Path(tmp) / "power_state.json")
            s = policy.status({"socPercent": 80}, wall=WALL)
            self.assertIsNone(s["hold"])
            self.assertTrue(s["wantAc"])
            self.assertEqual(s["wantSource"], power.SOURCE_RULE)
            policy.set_hold(False, until=WALL + 3600, duration="1h")
            s = policy.status({"socPercent": 80}, wall=WALL)
            self.assertEqual(s["hold"]["duration"], "1h")
            self.assertFalse(s["wantAc"])
            self.assertEqual(s["wantSource"], power.SOURCE_HOLD)


if __name__ == "__main__":
    unittest.main()
