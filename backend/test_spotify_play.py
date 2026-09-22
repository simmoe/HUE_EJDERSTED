import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import spotify


def _client() -> spotify.Spotify:
    client = spotify.Spotify.__new__(spotify.Spotify)
    client._http = AsyncMock()
    client._last_connect_restart = 0.0
    client._m5_device_id = ""
    client._m5_device_at = 0.0
    client._spotify_user_id_cache = ""
    return client


HOME_DEVICES = [
    {"id": "phone", "name": "iPhone", "type": "Smartphone", "is_active": True},
    {"id": "kiosk", "name": "Ejdersted", "type": "Computer", "is_active": False},
    {"id": "m5", "name": "Beoplay M5", "type": "Speaker", "is_active": False},
]


class FindSpeakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_picks_m5_never_phone_or_kiosk(self):
        client = _client()
        with (
            patch.object(client, "devices", AsyncMock(return_value=HOME_DEVICES)),
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(spotify.hub_config, "spotify_connect_device", return_value=""),
        ):
            self.assertEqual(await client._find_speaker_device_id(), "m5")

    async def test_home_without_m5_is_none_if_wake_fails(self):
        client = _client()
        only_phone = [d for d in HOME_DEVICES if d["id"] != "m5"]
        with (
            patch.object(client, "devices", AsyncMock(return_value=only_phone)),
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(spotify.hub_config, "spotify_connect_device", return_value=""),
            patch.object(spotify.hub_config, "bo_speakers_enabled", return_value=True),
            patch.object(client, "_bind_m5", AsyncMock(return_value=None)),
        ):
            self.assertIsNone(await client._find_speaker_device_id())

    async def test_home_wakes_m5_when_web_api_hides_it(self):
        client = _client()
        only_phone = [d for d in HOME_DEVICES if d["id"] != "m5"]
        with (
            patch.object(client, "devices", AsyncMock(return_value=only_phone)),
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(spotify.hub_config, "spotify_connect_device", return_value=""),
            patch.object(spotify.hub_config, "bo_speakers_enabled", return_value=True),
            patch.object(client, "_bind_m5", AsyncMock(return_value="m5-zeroconf")),
        ):
            self.assertEqual(await client._find_speaker_device_id(), "m5-zeroconf")

    async def test_home_uses_cached_m5_without_waking(self):
        client = _client()
        client._remember_m5("cached-m5")
        bind = AsyncMock(return_value="should-not-run")
        with (
            patch.object(client, "devices", AsyncMock(return_value=[])),
            patch.object(client, "_bind_m5", bind),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            self.assertEqual(await client._find_speaker_device_id(), "cached-m5")
        bind.assert_not_called()

    async def test_target_is_house_speaker_not_active_phone(self):
        client = _client()
        with patch.object(client, "_find_speaker_device_id", AsyncMock(return_value="m5")):
            self.assertEqual(await client._target_device_id(), "m5")


class PlayUrisQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_without_speaker_does_not_call_spotify(self):
        client = _client()
        client._http.put = AsyncMock()
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_find_speaker_device_id", AsyncMock(return_value=None)),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            ok, detail, duration = await client.play_uris_queue(["spotify:track:abc"])
        self.assertFalse(ok)
        self.assertEqual(detail, "Højttaleren er ikke på Spotify")
        self.assertEqual(duration, 0)
        client._http.put.assert_not_called()

    async def test_home_ok_only_when_m5_plays_that_uri(self):
        client = _client()
        put = MagicMock()
        put.status_code = 204
        put.text = ""
        client._http.put = AsyncMock(return_value=put)
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch.object(client, "_playback_matches", AsyncMock(return_value=True)),
            patch.object(client, "_beolink_expand", AsyncMock()),
            patch.object(client, "_track_duration", AsyncMock(return_value=180000)),
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            ok, detail, duration = await client.play_uris_queue(["spotify:track:abc"])
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(duration, 180000)
        params = client._http.put.call_args.kwargs["params"]
        self.assertEqual(params, {"device_id": "m5"})
        body = client._http.put.call_args.kwargs["json"]
        self.assertEqual(body["uris"], ["spotify:track:abc"])

    async def test_home_sends_remaining_queue_not_one_track(self):
        client = _client()
        put = MagicMock()
        put.status_code = 204
        put.text = ""
        client._http.put = AsyncMock(return_value=put)
        uris = ["spotify:track:aaa", "spotify:track:bbb", "spotify:track:ccc"]
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch.object(client, "_playback_matches", AsyncMock(return_value=True)),
            patch.object(client, "_beolink_expand", AsyncMock()),
            patch.object(client, "_track_duration", AsyncMock(return_value=1000)),
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            ok, detail, _ = await client.play_uris_queue(uris)
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        body = client._http.put.call_args.kwargs["json"]
        self.assertEqual(body["uris"], uris)
        self.assertEqual(body["offset"], {"position": 0})

    async def test_home_rebinds_after_device_not_found(self):
        client = _client()
        missing = MagicMock()
        missing.status_code = 404
        missing.text = '{"error":{"message":"Device not found"}}'
        ok = MagicMock()
        ok.status_code = 204
        ok.text = ""
        client._http.put = AsyncMock(side_effect=[missing, ok])
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_find_speaker_device_id", AsyncMock(side_effect=["stale", "fresh"])),
            patch.object(client, "_playback_matches", AsyncMock(return_value=True)),
            patch.object(client, "_beolink_expand", AsyncMock()),
            patch.object(client, "_track_duration", AsyncMock(return_value=1000)),
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            success, detail, _ = await client.play_uris_queue(["spotify:track:abc"])
        self.assertTrue(success)
        self.assertEqual(detail, "")
        self.assertEqual(client._http.put.call_args.kwargs["params"], {"device_id": "fresh"})
        self.assertEqual(client._m5_device_id, "fresh")

    async def test_home_rejects_play_on_wrong_device_or_track(self):
        client = _client()
        put = MagicMock()
        put.status_code = 204
        put.text = ""
        client._http.put = AsyncMock(return_value=put)
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_find_speaker_device_id", AsyncMock(return_value="m5")),
            patch.object(client, "_playback_matches", AsyncMock(return_value=False)),
            patch.object(client, "_beolink_expand", AsyncMock()),
            patch.object(spotify.asyncio, "sleep", AsyncMock()),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            ok, detail, duration = await client.play_uris_queue(["spotify:track:abc"])
        self.assertFalse(ok)
        self.assertEqual(detail, "Højttaleren startede ikke sangen")
        self.assertEqual(duration, 0)


class GardenLocalPlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_garden_play_uses_local_player(self):
        client = _client()
        local = AsyncMock(return_value=(True, "", 1000))
        client._http.put = AsyncMock()
        with (
            patch.object(spotify.hub_config, "site", return_value="garden"),
            patch.object(client, "_play_garden_uris", local),
        ):
            ok, detail, duration = await client.play_uris_queue(["spotify:track:abc"])
        local.assert_awaited_once()
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(duration, 1000)
        client._http.put.assert_not_called()

    async def test_garden_player_down_is_silent(self):
        client = _client()
        client._http.put = AsyncMock()
        with (
            patch.object(spotify.hub_config, "site", return_value="garden"),
            patch.object(client, "_play_garden_uris", AsyncMock(return_value=(False, "", 0))),
        ):
            ok, detail, _ = await client.play_uris_queue(["spotify:track:abc"])
        self.assertFalse(ok)
        self.assertEqual(detail, "")
        self.assertNotIn("offline", detail.lower())
        client._http.put.assert_not_called()

    async def test_garden_pause_hits_local_player(self):
        client = _client()
        post = AsyncMock(return_value=True)
        with (
            patch.object(spotify.hub_config, "site", return_value="garden"),
            patch.object(client, "_garden_player_post", post),
        ):
            self.assertTrue(await client.pause())
        post.assert_awaited_with("/player/pause")


class PauseTargetsHouseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_without_house_speaker_still_stops_active_player(self):
        client = _client()
        put = MagicMock()
        put.status_code = 204
        client._http.put = AsyncMock(return_value=put)
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_target_device_id", AsyncMock(return_value=None)),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            self.assertTrue(await client.pause())
        self.assertEqual(client._http.put.call_count, 1)
        self.assertEqual(client._http.put.call_args.kwargs.get("params"), None)

    async def test_pause_stops_active_player_then_house_speaker(self):
        client = _client()
        put = MagicMock()
        put.status_code = 204
        client._http.put = AsyncMock(return_value=put)
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(client, "_target_device_id", AsyncMock(return_value="m5")),
            patch.object(spotify.hub_config, "site", return_value="home"),
        ):
            self.assertTrue(await client.pause())
        self.assertEqual(client._http.put.call_count, 2)
        self.assertEqual(client._http.put.call_args_list[1].kwargs["params"], {"device_id": "m5"})


class PickBestTrackTests(unittest.TestCase):
    def test_prefers_title_and_artist_in_the_spoken_query(self):
        tracks = [
            {"uri": "spotify:track:wrong", "name": "Keep Going", "artists": [{"name": "Someone Else"}]},
            {"uri": "spotify:track:right", "name": "Keep Going", "artists": [{"name": "This Is The Kit"}]},
        ]
        picked = spotify.pick_best_track("keep going this is the kit", tracks)
        self.assertEqual(picked["uri"], "spotify:track:right")

    def test_falls_back_to_first_track_when_nothing_matches(self):
        tracks = [
            {"uri": "spotify:track:a", "name": "Alpha", "artists": [{"name": "A"}]},
            {"uri": "spotify:track:b", "name": "Beta", "artists": [{"name": "B"}]},
        ]
        self.assertEqual(spotify.pick_best_track("xyz", tracks)["uri"], "spotify:track:a")


if __name__ == "__main__":
    unittest.main()
