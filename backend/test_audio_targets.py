import unittest
from unittest.mock import AsyncMock, patch

import audio_targets
import main


class BlueAlsaDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_a2dp_pcm_is_online_without_literal_playback_word(self):
        pcm = (
            "/org/bluealsa/hci0/dev_F4_4E_FD_57_3A_AB/a2dpsnk/source "
            "F4:4E:FD:57:3A:AB a2dp\n"
        )
        with patch.object(audio_targets, "_run", AsyncMock(return_value=(0, pcm, ""))):
            available = await audio_targets._bluealsa_playback_available(
                "F4:4E:FD:57:3A:AB"
            )

        self.assertTrue(available)

    async def test_other_pcm_is_not_mistaken_for_target(self):
        pcm = "/org/bluealsa/hci0/dev_00_11_22_33_44_55/a2dpsnk/source a2dp\n"
        with patch.object(audio_targets, "_run", AsyncMock(return_value=(0, pcm, ""))):
            available = await audio_targets._bluealsa_playback_available(
                "F4:4E:FD:57:3A:AB"
            )

        self.assertFalse(available)


class GardenAudioErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_connected_speaker_reports_a2dp_failure_not_power_failure(self):
        target = audio_targets.AudioTarget(
            id="garden",
            name="Garden speaker",
            type="bluealsa",
            mac="F4:4E:FD:57:3A:AB",
            default=True,
        )
        status = {
            "online": False,
            "connected": True,
            "playback": False,
            "error": "Tilsluttet, men A2DP-lydprofilen mangler",
        }
        with (
            patch.object(main.hub_config, "site", return_value="garden"),
            patch.object(main, "_configured_audio_targets", return_value=[target]),
            patch.object(main.audio_targets, "target_status", AsyncMock(return_value=status)),
            patch.object(main.audio_targets, "connect_target", AsyncMock(return_value=status)),
        ):
            with self.assertRaisesRegex(RuntimeError, "A2DP-lydprofilen mangler"):
                await main._garden_bluealsa_device()


if __name__ == "__main__":
    unittest.main()
