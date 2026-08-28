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
    async def test_spotify_null_items_do_not_500(self):
        payload = [
            None,
            {
                "id": "ep1",
                "uri": "spotify:episode:ep1",
                "name": "Flyt nu! 11 danskere på jagt efter en ny klub",
                "release_date": "2026-08-25",
                "duration_ms": 3600000,
            },
        ]
        with patch.object(
            main.spotify,
            "get_show_episodes",
            AsyncMock(return_value=(payload, False)),
        ):
            data = await main.list_show_episodes("6FVyoDMn4GKxveMegJ2Yih", limit=20, offset=0)

        self.assertEqual(len(data["episodes"]), 1)
        self.assertEqual(data["episodes"][0]["id"], "ep1")
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
