import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import switchbot_bot as sb
import main


class FakeBackend:
    def __init__(self, bots=None, fail=None):
        self.bots = list(bots or [])
        self.fail = fail
        self.pressed = []

    async def discover(self, timeout: float):
        return list(self.bots)

    async def press(self, address: str):
        if self.fail:
            raise RuntimeError(self.fail)
        self.pressed.append(sb.normalize_mac(address))


class SwitchbotHelperTests(unittest.TestCase):
    def test_normalize_mac_accepts_dashed_and_bare(self):
        self.assertEqual(sb.normalize_mac("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(sb.normalize_mac("aabbccddeeff"), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(sb.normalize_mac("not-a-mac"), "")

    def test_bot_advert_from_model_byte(self):
        self.assertTrue(sb._is_bot_advert("", {"0000fd3d-0000-1000-8000-00805f9b34fb": bytes([0x48, 0x00])}, []))
        self.assertTrue(sb._is_bot_advert("WoHand", {}, []))
        self.assertFalse(sb._is_bot_advert("Meter", {}, []))


class SwitchbotControllerTests(unittest.IsolatedAsyncioTestCase):
    def _ctrl(self, tmp: str, backend: FakeBackend, mac: str = "") -> sb.SwitchbotController:
        return sb.SwitchbotController(
            state_path=Path(tmp) / "switchbot.json",
            backend=backend,
            mac=mac,
            scan_timeout=0.01,
        )

    async def test_press_uses_configured_mac(self):
        bot = sb.DiscoveredBot(mac="11:22:33:44:55:66", name="WoHand")
        backend = FakeBackend([bot])
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = self._ctrl(tmp, backend, mac="11:22:33:44:55:66")
            status = await ctrl.press()
        self.assertEqual(backend.pressed, ["11:22:33:44:55:66"])
        self.assertTrue(status["ready"])
        self.assertFalse(status["pressing"])
        self.assertIsNotNone(status["lastPressAt"])
        self.assertIsNone(status["lastError"])

    async def test_press_adopts_the_only_nearby_bot(self):
        bot = sb.DiscoveredBot(mac="AA:BB:CC:DD:EE:01", name="WoHand")
        backend = FakeBackend([bot])
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = self._ctrl(tmp, backend)
            self.assertFalse(ctrl.mac)
            await ctrl.press()
            self.assertEqual(ctrl.mac, "AA:BB:CC:DD:EE:01")
            stored = (Path(tmp) / "switchbot.json").read_text(encoding="utf-8")
            self.assertIn("AA:BB:CC:DD:EE:01", stored)

    async def test_press_fails_when_none_found(self):
        backend = FakeBackend([])
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = self._ctrl(tmp, backend)
            with self.assertRaisesRegex(RuntimeError, "Ingen SwitchBot"):
                await ctrl.press()
            self.assertIn("Ingen SwitchBot", ctrl.last_error or "")

    async def test_refuses_second_press_while_busy(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowBackend(FakeBackend):
            async def press(self, address: str):
                started.set()
                await release.wait()
                await super().press(address)

        bot = sb.DiscoveredBot(mac="11:22:33:44:55:66", name="WoHand")
        backend = SlowBackend([bot])
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = self._ctrl(tmp, backend, mac="11:22:33:44:55:66")
            task = asyncio.create_task(ctrl.press())
            await started.wait()
            with self.assertRaisesRegex(RuntimeError, "allerede i gang"):
                await ctrl.press()
            release.set()
            await task

    async def test_status_disabled_helper(self):
        status = sb.disabled_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["ready"])


class SwitchbotApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_press_disabled_is_404(self):
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=False),
            patch.object(main, "switchbot_ctrl", None),
        ):
            result = await main.press_switchbot()
        self.assertEqual(result.status_code, 404)

    async def test_press_ok_broadcasts_status(self):
        ctrl = AsyncMock()
        ctrl.press.return_value = {"enabled": True, "ready": True, "mac": "11:22:33:44:55:66"}
        broadcast = AsyncMock()
        with (
            patch.object(main, "switchbot_ctrl", ctrl),
            patch.object(main.manager, "broadcast", broadcast),
        ):
            result = await main.press_switchbot()
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["mac"], "11:22:33:44:55:66")
        broadcast.assert_awaited()


if __name__ == "__main__":
    unittest.main()
