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


class EvidenceStillTests(unittest.TestCase):
    def jpeg(self, width: int, height: int, color: tuple[int, int, int]) -> bytes:
        from PIL import Image

        buf = __import__("io").BytesIO()
        Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def test_stamp_uses_copenhagen_clock(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ts = datetime(2026, 9, 15, 9, 47, tzinfo=ZoneInfo("Europe/Copenhagen")).timestamp()
        self.assertEqual(camera_presence.format_evidence_stamp(ts), "15. sep. 2026  09:47")

    def test_crop_zooms_onto_the_person_block(self):
        from PIL import Image, ImageDraw

        buf = __import__("io").BytesIO()
        img = Image.new("RGB", (400, 200), (0, 0, 0))
        ImageDraw.Draw(img).rectangle((240, 20, 380, 180), fill=(220, 220, 220))
        img.save(buf, format="JPEG", quality=95)
        cropped = camera_presence.crop_to_person(buf.getvalue(), [240, 20, 380, 180])
        with Image.open(__import__("io").BytesIO(cropped)) as out:
            self.assertEqual(out.size[0], out.size[1])
            self.assertLess(out.size[0], 400)
            cx, cy = out.size[0] // 2, out.size[1] // 2
            self.assertGreater(out.getpixel((cx, cy))[0], 160)


class EvidenceCaptureTests(unittest.IsolatedAsyncioTestCase):
    def jpeg(self, width: int, height: int, color: tuple[int, int, int]) -> bytes:
        from PIL import Image

        buf = __import__("io").BytesIO()
        Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    async def test_keeps_the_larger_person_frame(self):
        from PIL import Image, ImageDraw

        def frame(person: tuple[int, int, int, int]) -> bytes:
            buf = __import__("io").BytesIO()
            img = Image.new("RGB", (400, 200), (0, 0, 0))
            ImageDraw.Draw(img).rectangle(person, fill=(220, 220, 220))
            img.save(buf, format="JPEG", quality=95)
            return buf.getvalue()

        small = frame((10, 10, 40, 50))
        large = frame((220, 20, 380, 180))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = CameraPresenceStateMachineTests().make_service(root)
            service.motion = FakeMotionGate()
            service.detector = FakeDetector([
                camera_presence.PersonResult(ok=True, confidence=0.9, bbox=[10, 10, 40, 50], status="ready"),
                camera_presence.PersonResult(ok=True, confidence=0.7, bbox=[220, 20, 380, 180], status="ready"),
            ])
            with patch("camera_presence._now", return_value=3000.0):
                await service.process_snapshot({}, small)
            with patch("camera_presence._now", return_value=3002.0):
                second = await service.process_snapshot({}, large)
            self.assertEqual(second["presence"], "home")
            snaps = list(root.glob("events/*/snapshot.jpg"))
            self.assertEqual(len(snaps), 1)
            with Image.open(snaps[0]) as out:
                cx, cy = out.size[0] // 2, out.size[1] // 2
                self.assertGreater(out.getpixel((cx, cy))[0], 160)


if __name__ == "__main__":
    unittest.main()
