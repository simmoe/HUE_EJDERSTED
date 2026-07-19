import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import camera_presence


class FakeMotionGate:
    def __init__(self, candidate: bool = True) -> None:
        self.candidate = candidate
        self.relearned = False

    def analyze(self, body: bytes, *, allow_baseline_update: bool, state: dict):
        return camera_presence.MotionResult(
            score=0.18 if self.candidate else 0.0,
            candidate=self.candidate,
            low_light=False,
            mean_luminance=110.0,
        )

    def relearn(self, body: bytes) -> bool:
        self.relearned = True
        self.candidate = False
        return True


class FakeDetector:
    def __init__(self, results: list[camera_presence.PersonResult]) -> None:
        self.results = results
        self.status = "ready"

    def detect(self, body: bytes) -> camera_presence.PersonResult:
        return self.results.pop(0)


class CameraPresenceStateMachineTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, root: Path) -> camera_presence.CameraPresenceService:
        return camera_presence.CameraPresenceService(
            camera_dir=root,
            snapshot_file=root / "latest.jpg",
            state_file=root / "presence.json",
            baseline_file=root / "baseline.raw",
            model_path=root / "model.onnx",
            checking_timeout_seconds=20,
            detector_health_interval_seconds=0,
        )

    async def test_checking_times_out_and_relearns_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(Path(tmp))
            motion = FakeMotionGate()
            service.motion = motion
            service.detector = FakeDetector([
                camera_presence.PersonResult(ok=False, confidence=0.12, status="ready"),
                camera_presence.PersonResult(ok=False, confidence=0.08, status="ready"),
                camera_presence.PersonResult(ok=False, confidence=0.04, status="ready"),
            ])

            with patch("camera_presence._now", return_value=1000.0):
                first = await service.process_snapshot({}, b"frame")
            with patch("camera_presence._now", return_value=1019.0):
                second = await service.process_snapshot({}, b"frame")
            with patch("camera_presence._now", return_value=1020.1):
                final = await service.process_snapshot({}, b"frame")

            self.assertEqual(first["presence"], "checking")
            self.assertEqual(second["presence"], "checking")
            self.assertEqual(final["presence"], "empty")
            self.assertEqual(final["motionScore"], 0.0)
            self.assertIsNone(final["personBbox"])
            self.assertTrue(motion.relearned)

    async def test_two_confirmations_required_before_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.make_service(Path(tmp))
            service.motion = FakeMotionGate()
            service.detector = FakeDetector([
                camera_presence.PersonResult(ok=True, confidence=0.82, bbox=[1, 2, 3, 4], status="ready"),
                camera_presence.PersonResult(ok=True, confidence=0.86, bbox=[1, 2, 3, 4], status="ready"),
            ])

            with patch("camera_presence._now", return_value=2000.0):
                first = await service.process_snapshot({}, b"frame")
            with patch("camera_presence._now", return_value=2002.0):
                second = await service.process_snapshot({}, b"frame")

            self.assertEqual(first["presence"], "checking")
            self.assertIsNone(first.get("lastPersonAt"))
            self.assertEqual(second["presence"], "home")
            self.assertEqual(second["lastPersonAt"], 2002.0)


if __name__ == "__main__":
    unittest.main()
