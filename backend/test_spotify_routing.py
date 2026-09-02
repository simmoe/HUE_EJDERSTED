import unittest
from unittest.mock import AsyncMock, patch

import hub_config
import spotify


def devices(*rows):
    return [
        {"id": did, "name": name, "type": typ, "is_active": active, "is_restricted": False}
        for did, name, typ, active in rows
    ]


class PickConnectDeviceTests(unittest.TestCase):
    def test_home_plays_only_on_m5(self):
        devs = devices(
            ("sdk", "Ejdersted", "Computer", True),
            ("garden", "Ejdersted Garden", "Speaker", True),
            ("phone", "Simons iPhone", "Smartphone", False),
            ("m5", "Beoplay M5", "Speaker", False),
        )
        self.assertEqual(spotify.pick_connect_device(devs, site="home"), "m5")

    def test_home_does_not_fall_back_to_kiosk_or_garden(self):
        devs = devices(
            ("sdk", "Ejdersted", "Computer", True),
            ("garden", "Ejdersted Garden", "Speaker", True),
            ("phone", "Simons iPhone", "Smartphone", True),
        )
        self.assertIsNone(spotify.pick_connect_device(devs, site="home"))

    def test_garden_requires_exact_librespot_name(self):
        devs = devices(
            ("sdk", "Ejdersted", "Computer", True),
            ("m5", "Beoplay M5", "Speaker", True),
            ("garden", "Ejdersted Garden", "Speaker", False),
        )
        self.assertEqual(
            spotify.pick_connect_device(devs, site="garden", preferred="Ejdersted Garden"),
            "garden",
        )
        self.assertIsNone(spotify.pick_connect_device(devs, site="garden"))


class HomePlayRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = spotify.Spotify.__new__(spotify.Spotify)
        self.client._http = AsyncMock()
        self.client._last_connect_restart = 0

    async def test_play_uris_never_uses_active_device_fallback(self):
        calls = []

        async def put(url, headers=None, params=None, json=None):
            calls.append(params)
            class Resp:
                status_code = 204
                text = ""
            return Resp()

        self.client._http.put = put
        with (
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(self.client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(self.client, "_find_speaker_device_id", AsyncMock(return_value=None)),
            patch.object(self.client, "_beolink_expand", AsyncMock()) as expand,
        ):
            ok, detail, _ = await self.client.play_uris_queue(["spotify:track:abc"])
        self.assertFalse(ok)
        self.assertEqual(detail, "Beoplay M5 er ikke på Spotify Connect")
        self.assertEqual(calls, [])
        expand.assert_not_awaited()

    async def test_successful_m5_play_expands_beolink(self):
        class PlayResp:
            status_code = 204
            text = ""

        self.client._http.put = AsyncMock(return_value=PlayResp())
        with (
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(self.client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(self.client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch.object(self.client, "_playback_matches", AsyncMock(return_value=True)),
            patch.object(self.client, "_beolink_expand", AsyncMock()) as expand,
            patch.object(self.client, "_track_duration", AsyncMock(return_value=180_000)),
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
        ):
            ok, detail, duration = await self.client.play_uris_queue(["spotify:track:wanted"])
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(duration, 180_000)
        expand.assert_awaited()
        self.assertEqual(self.client._http.put.await_args.kwargs["params"], {"device_id": "m5"})

    async def test_wrong_track_on_m5_does_not_expand_beolink(self):
        class PlayResp:
            status_code = 204
            text = ""

        self.client._http.put = AsyncMock(return_value=PlayResp())
        with (
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(self.client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(self.client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch.object(self.client, "_playback_matches", AsyncMock(return_value=False)),
            patch.object(self.client, "_beolink_expand", AsyncMock()) as expand,
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
        ):
            ok, detail, _ = await self.client.play_uris_queue(["spotify:track:wanted"])
        self.assertFalse(ok)
        self.assertIn("startede ikke det valgte spor", detail)
        expand.assert_not_awaited()

    async def test_home_pause_targets_m5_not_active_kiosk(self):
        class PauseResp:
            status_code = 204

        self.client._http.put = AsyncMock(return_value=PauseResp())
        with (
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(self.client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(self.client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch("bo_link.stop_speakers", AsyncMock()) as stop,
        ):
            ok = await self.client.pause()
        self.assertTrue(ok)
        stop.assert_awaited()
        self.assertEqual(self.client._http.put.await_args.kwargs["params"], {"device_id": "m5"})


class HubConfigHomeDeviceTests(unittest.TestCase):
    def test_home_has_no_default_garden_connect_name(self):
        with patch.object(hub_config, "site", return_value="home"):
            # spotify_connect_device reads CONFIG, not the patched site() unless empty.
            pass
        self.assertEqual(hub_config.DEFAULT_CONFIG["site"], "home")
        audio = hub_config.DEFAULT_CONFIG.get("audio", {})
        self.assertFalse(audio.get("spotifyDevice"))
