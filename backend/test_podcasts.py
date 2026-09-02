import unittest
from unittest.mock import AsyncMock, patch

import main
import spotify


class SpotifyEpisodeSanitizationTests(unittest.TestCase):
    def test_drops_null_and_incomplete_spotify_items(self):
        items = spotify._usable_spotify_episodes(
            [
                None,
                {"id": "", "uri": "spotify:episode:x", "name": "blank"},
                {"id": "ep1", "uri": "spotify:episode:ep1", "name": "Flyt nu!"},
                "not-an-episode",
            ]
        )
        self.assertEqual([item["id"] for item in items], ["ep1"])


class PodcastEpisodeListTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_show_does_not_query_spotify(self):
        with patch.object(
            main.spotify,
            "get_show_episodes",
            AsyncMock(side_effect=AssertionError("spotify must not be a podcast fallback")),
        ):
            data = await main.list_show_episodes("not-in-catalog", limit=20, offset=0)
        self.assertEqual(data["episodes"], [])
        self.assertFalse(data["has_more"])


class SpotifyClientEpisodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_skips_null_items_from_spotify(self):
        class FakeResp:
            status_code = 200

            def json(self):
                return {
                    "items": [
                        None,
                        {"id": "ep1", "uri": "spotify:episode:ep1", "name": "Flyt nu!"},
                    ],
                    "next": None,
                }

        client = spotify.Spotify.__new__(spotify.Spotify)
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=FakeResp())
        with patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})):
            items, has_more = await client.get_show_episodes("6FVyoDMn4GKxveMegJ2Yih")

        self.assertEqual([item["id"] for item in items], ["ep1"])
        self.assertFalse(has_more)


class KioskPodcastCatalogTests(unittest.TestCase):
    def test_catalog_plays_direct_streams_only(self):
        sources = {sh["source"] for sh in main.PODCAST_SHOWS}
        self.assertTrue(sources <= {"rss", "sr"})
        self.assertNotIn("spotify", sources)

    def test_prompt_is_in_the_catalog(self):
        sh = main._find_show("rss:prompt") or main._find_show("prompt")
        self.assertIsNotNone(sh)
        self.assertEqual(sh["source"], "rss")
        self.assertEqual(sh.get("order"), "latest")
        self.assertIn("/feeds/prompt", sh["feed"])

    def test_spotify_ids_are_aliases_not_playback(self):
        fodbold = main._find_show("6FVyoDMn4GKxveMegJ2Yih")
        kapitel = main._find_show("5d4yba4KbcBTtwZ8glscZZ")
        self.assertEqual(fodbold["source"], "rss")
        self.assertEqual(kapitel["source"], "rss")
        self.assertIn("omnycontent.com", kapitel["feed"])

    def test_sveriges_radio_and_dr_are_both_first_class_sources(self):
        by_id = {sh["id"]: sh["source"] for sh in main.PODCAST_SHOWS}
        self.assertEqual(by_id["prompt"], "rss")
        self.assertEqual(by_id["fodboldlisten"], "rss")
        self.assertEqual(by_id["4914"], "sr")
        self.assertEqual(by_id["2488"], "sr")


class CatalogStreamPlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_spotify_alias_lists_rss_uris(self):
        queue = [
            {
                "id": "rss:fodboldlisten:0",
                "uri": "rss:fodboldlisten:0",
                "name": "Flyt nu!",
                "release_date": "2026-08-25",
                "duration_ms": 3600000,
            }
        ]
        with patch.object(main, "_rss_meta_and_queue", AsyncMock(return_value=({}, queue))):
            data = await main.list_show_episodes("6FVyoDMn4GKxveMegJ2Yih", limit=20, offset=0)
        self.assertEqual(data["episodes"][0]["uri"], "rss:fodboldlisten:0")

    async def test_leftover_spotify_uri_plays_catalog_stream(self):
        ep = {"id": "rss:fodboldlisten:0", "uri": "rss:fodboldlisten:0", "name": "VM er slut!"}
        queue = [{**ep, "media_url": "http://example/ep.mp3"}]
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=True),
            patch.object(main.spotify, "play_episode", AsyncMock(return_value=(True, "should not run"))),
            patch.object(main, "_rss_meta_and_queue", AsyncMock(return_value=({}, queue))),
            patch.object(main, "_play_rss_index", AsyncMock(return_value=(True, "ok", ep))) as play_rss,
        ):
            result = await main.play_specific_episode(
                {
                    "episode_uri": "spotify:episode:abc",
                    "episode_title": "VM er slut!",
                    "show_id": "6FVyoDMn4GKxveMegJ2Yih",
                }
            )
        play_rss.assert_awaited()
        self.assertTrue(result["ok"])
        self.assertEqual(result["episode"]["uri"], "rss:fodboldlisten:0")

    async def test_unmapped_spotify_uri_does_not_lecture_about_bang_olufsen(self):
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=True),
            patch.object(main, "_play_spotify_episode_as_rss", AsyncMock(return_value=None)),
            patch.object(
                main.spotify,
                "play_episode",
                AsyncMock(return_value=(False, "Spotify-podcasts spiller ikke på B&O. Brug en podcast med direkte stream.")),
            ) as play_ep,
        ):
            result = await main.play_specific_episode({"episode_uri": "spotify:episode:abc"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail"], "ingen RSS-match for spotify:episode URI")
        play_ep.assert_not_called()


class SpotifyNowPlayingTests(unittest.IsolatedAsyncioTestCase):
    async def test_null_item_does_not_crash(self):
        class FakeResp:
            status_code = 200
            content = b'{"item":null}'

            def json(self):
                return {"item": None, "is_playing": True, "progress_ms": 12}

        client = spotify.Spotify.__new__(spotify.Spotify)
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=FakeResp())
        with patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})):
            data = await client.now_playing()
        self.assertEqual(data["name"], "")
        self.assertEqual(data["artist"], "")


class SpotifyEpisodeTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_does_not_pretend_to_play_on_computer(self):
        client = spotify.Spotify.__new__(spotify.Spotify)
        client._http = AsyncMock()
        with (
            patch.object(client, "_headers", AsyncMock(return_value={"Authorization": "Bearer x"})),
            patch.object(
                client,
                "devices",
                AsyncMock(return_value=[{"id": "abc", "name": "Ejdersted", "type": "Computer"}]),
            ),
            patch.object(spotify.hub_config, "site", return_value="home"),
            patch.object(spotify.hub_config, "spotify_connect_device", return_value=""),
        ):
            ok, detail = await client.play_episode("spotify:episode:x")
        self.assertFalse(ok)
        self.assertEqual(detail, "Beoplay M5 er ikke på Spotify Connect")


class DlnaDidlTests(unittest.TestCase):
    def test_protocol_info_follows_url_suffix(self):
        import bo_dlna
        self.assertEqual(bo_dlna._protocol_info("http://x/ep.mp3"), "http-get:*:audio/mpeg:*")
        self.assertEqual(bo_dlna._protocol_info("http://x/ep.m4a"), "http-get:*:audio/mp4:*")
        self.assertEqual(
            bo_dlna._protocol_info("https://api.dr.dk/podcasts/v1/assets/urn:x"),
            "http-get:*:*:*",
        )


class BeoNowPlayingMergeTests(unittest.TestCase):
    def setUp(self):
        self._prev = dict(main.now_playing_cache)
        main.now_playing_cache.clear()

    def tearDown(self):
        main.now_playing_cache.clear()
        main.now_playing_cache.update(self._prev)

    def test_stored_music_uses_speaker_title_not_queue(self):
        state = main._merge_beo_now_playing(
            "m5",
            {
                "type": "NOW_PLAYING_STORED_MUSIC",
                "data": {
                    "name": "Lover, You Should've Come Over",
                    "artist": "Jeff Buckley",
                    "album": "Grace",
                },
            },
        )
        self.assertEqual(state["name"], "Lover, You Should've Come Over")
        self.assertEqual(state["artist"], "Jeff Buckley")
        self.assertTrue(state["playing"])

    def test_net_radio_uses_title_and_station(self):
        state = main._merge_beo_now_playing(
            "m5",
            {
                "type": "NOW_PLAYING_NET_RADIO",
                "data": {
                    "title": "Under äppelträden",
                    "station": "P2",
                },
            },
        )
        self.assertEqual(state["name"], "Under äppelträden")
        self.assertEqual(state["artist"], "P2")
        self.assertTrue(state["playing"])

    def test_progress_pauses_without_dropping_title(self):
        main.now_playing_cache["m5"] = {
            "name": "Lover, You Should've Come Over",
            "artist": "Jeff Buckley",
            "album": "Grace",
            "playing": True,
        }
        state = main._merge_beo_now_playing(
            "m5",
            {"type": "PROGRESS_INFORMATION", "data": {"state": "pause", "position": 12}},
        )
        self.assertEqual(state["name"], "Lover, You Should've Come Over")
        self.assertFalse(state["playing"])


class DlnaSeekFormatTests(unittest.TestCase):
    def test_rel_time_pads_minutes_and_seconds(self):
        import bo_dlna
        self.assertEqual(bo_dlna.rel_time(0), "0:00:00")
        self.assertEqual(bo_dlna.rel_time(94_000), "0:01:34")
        self.assertEqual(bo_dlna.rel_time(3_661_000), "1:01:01")


class PodcastHomeSeekTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_rss_seek_calls_dlna(self):
        prev = dict(main.podcast_player_state)
        main.podcast_player_state.update({
            "active": True,
            "source": "rss",
            "playing": True,
            "positionMs": 10_000,
            "durationMs": 3_600_000,
            "updatedAt": 0,
        })
        try:
            with patch.object(main.hub_config, "site", return_value="home"), patch.object(
                main.bo_dlna, "seek", AsyncMock(return_value=(True, ""))
            ) as seek:
                data = await main.podcast_player_seek({"positionSeconds": 90})
            seek.assert_awaited_once_with(90_000)
            self.assertTrue(data["ok"])
            self.assertEqual(data["player"]["positionMs"], 90_000)
        finally:
            main.podcast_player_state.clear()
            main.podcast_player_state.update(prev)


class SpeakerDedupeTests(unittest.TestCase):
    def setUp(self):
        self._prev = dict(main.devices)
        main.devices.clear()
        main.devices.update({
            "192_168_86_153": {
                "id": "192_168_86_153",
                "name": "BeoPlay A9",
                "ip": "192.168.86.153",
                "auto_discovered": True,
            },
            "192_168_86_188": {
                "id": "192_168_86_188",
                "name": "Beoplay M5",
                "ip": "192.168.86.188",
                "auto_discovered": True,
            },
            "192_168_86_20": {
                "id": "192_168_86_20",
                "name": "BeoPlay A9",
                "ip": "192.168.86.20",
                "auto_discovered": True,
            },
            "192_168_86_21": {
                "id": "192_168_86_21",
                "name": "Beoplay M5",
                "ip": "192.168.86.21",
                "auto_discovered": True,
            },
        })

    def tearDown(self):
        main.devices.clear()
        main.devices.update(self._prev)

    def test_drops_old_dhcp_addresses_for_same_speakers(self):
        removed = main._prune_duplicate_speakers()
        ips = {d["ip"] for d in main.devices.values()}
        self.assertEqual(ips, {"192.168.86.20", "192.168.86.21"})
        self.assertEqual(set(removed), {"192_168_86_153", "192_168_86_188"})


class PlaybackSessionClaimTests(unittest.IsolatedAsyncioTestCase):
    """Home and garden share one session: claiming music must stop the podcast
    engine without wiping queue/position the kiosk still displays."""

    def setUp(self):
        self._prev = dict(main.podcast_player_state)
        self._engine = main._active_podcast_engine
        main.podcast_player_state.update({
            "active": True,
            "source": "rss",
            "showId": "rss:fodboldlisten",
            "showTitle": "Fodboldlisten",
            "episodeId": "ep-1",
            "episodeUri": "rss:fodboldlisten:0",
            "episodeTitle": "VM er slut!",
            "episodeIndex": 0,
            "queue": [{"uri": "rss:fodboldlisten:0", "name": "VM er slut!"}],
            "playing": True,
            "positionMs": 12_000,
            "durationMs": 3_600_000,
            "updatedAt": 0,
            "error": "",
        })
        main._active_podcast_engine = "dlna"

    def tearDown(self):
        main.podcast_player_state.clear()
        main.podcast_player_state.update(self._prev)
        main._active_podcast_engine = self._engine

    async def test_home_spotify_claim_stops_dlna_and_keeps_queue(self):
        with (
            patch.object(main.hub_config, "site", return_value="home"),
            patch.object(main.hub_config, "bo_speakers_enabled", return_value=True),
            patch.object(main.bo_dlna, "stop", AsyncMock(return_value=(True, ""))) as stop,
        ):
            detail = await main._claim_spotify_audio()
        self.assertEqual(detail, "")
        stop.assert_awaited()
        self.assertEqual(main._active_podcast_engine, "")
        self.assertFalse(main.podcast_player_state["active"])
        self.assertFalse(main.podcast_player_state["playing"])
        self.assertEqual(main.podcast_player_state["episodeTitle"], "VM er slut!")
        self.assertEqual(main.podcast_player_state["positionMs"], 12_000)
        self.assertEqual(main.podcast_player_state["queue"][0]["uri"], "rss:fodboldlisten:0")

    async def test_garden_spotify_claim_still_requires_the_speaker(self):
        with (
            patch.object(main.hub_config, "site", return_value="garden"),
            patch.object(main.hub_config, "bo_speakers_enabled", return_value=False),
            patch.object(
                main,
                "_garden_bluealsa_device",
                AsyncMock(side_effect=RuntimeError("Gå hen og tænd højttaleren")),
            ),
        ):
            detail = await main._claim_spotify_audio()
        self.assertEqual(detail, "Gå hen og tænd højttaleren")

    async def test_release_keeps_queue_clear_wipes(self):
        with (
            patch.object(main.hub_config, "bo_speakers_enabled", return_value=True),
            patch.object(main.bo_dlna, "stop", AsyncMock(return_value=(True, ""))),
        ):
            released = await main.podcast_player_release()
            self.assertTrue(released["ok"])
            self.assertEqual(released["player"]["episodeTitle"], "VM er slut!")
            self.assertFalse(released["player"]["active"])
            self.assertEqual(main._active_podcast_engine, "")
            cleared = await main.podcast_player_clear()
        self.assertEqual(cleared["player"]["episodeTitle"], "")
        self.assertEqual(cleared["player"]["queue"], [])

    async def test_play_uris_claims_spotify_on_home(self):
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=True),
            patch.object(main.hub_config, "site", return_value="home"),
            patch.object(main.hub_config, "bo_speakers_enabled", return_value=True),
            patch.object(main.bo_dlna, "stop", AsyncMock(return_value=(True, ""))) as stop,
            patch.object(main.spotify, "play_uris_queue", AsyncMock(return_value=(True, "", 180_000))),
        ):
            result = await main.spotify_play_uris({"uris": ["spotify:track:abc"], "offset": 0})
        stop.assert_awaited()
        self.assertTrue(result["ok"])
        self.assertFalse(main.podcast_player_state["active"])
        self.assertEqual(main.podcast_player_state["episodeTitle"], "VM er slut!")

    async def test_play_latest_podcast_pauses_spotify(self):
        ep = {"id": "rss:fodboldlisten:0", "uri": "rss:fodboldlisten:0", "name": "VM er slut!"}
        with (
            patch.object(main.hub_config, "feature_enabled", return_value=True),
            patch.object(main.spotify, "pause", AsyncMock(return_value=True)) as pause,
            patch.object(main, "_play_latest_rss", AsyncMock(return_value=(True, "ok", ep))),
        ):
            result = await main.play_latest_podcast({"show_id": "rss:fodboldlisten"})
        pause.assert_awaited()
        self.assertTrue(result["ok"])
