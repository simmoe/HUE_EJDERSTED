import unittest
from unittest.mock import AsyncMock

import podcast_catalog
import player_state


class ParseCatalogTests(unittest.TestCase):
    def test_keeps_rss_and_sr_and_drops_spotify(self):
        shows = podcast_catalog.parse_shows(
            [
                {
                    "source": "rss",
                    "id": "portraetalbum",
                    "fallback_name": "Portrætalbum",
                    "order": "latest",
                    "feed": "https://example.com/feed.rss",
                },
                {"source": "sr", "id": "4914", "fallback_name": "Text och musik"},
                {"source": "spotify", "id": "abc", "fallback_name": "Nope"},
                {"source": "rss", "id": "broken", "fallback_name": "Broken"},
            ]
        )
        self.assertEqual([row["id"] for row in shows], ["portraetalbum", "4914"])

    def test_defaults_include_the_new_music_shows(self):
        by_id = {row["id"]: row for row in podcast_catalog.DEFAULT_SHOWS}
        self.assertEqual(by_id["song-exploder"]["feed"], "https://feed.songexploder.net/SongExploder")
        self.assertIn("/feeds/splittet-til-atomer", by_id["splittet-til-atomer"]["feed"])
        self.assertIn("omnycontent.com", by_id["portraetalbum"]["feed"])


class CatalogCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        podcast_catalog.reset()

    def tearDown(self):
        podcast_catalog.reset()

    def test_current_falls_back_to_defaults(self):
        self.assertEqual(podcast_catalog.current()[0]["id"], "fodboldlisten")
        self.assertIn("portraetalbum", {row["id"] for row in podcast_catalog.current()})

    async def test_empty_firebase_doc_seeds_defaults(self):
        http = AsyncMock()
        http.get = AsyncMock(return_value=type("R", (), {"status_code": 404})())
        http.patch = AsyncMock(return_value=type("R", (), {"status_code": 200})())
        shows = await podcast_catalog.hydrate(
            firebase_config={"projectId": "p", "apiKey": "k"},
            http_client=http,
            force=True,
        )
        self.assertEqual([row["id"] for row in shows], [row["id"] for row in podcast_catalog.DEFAULT_SHOWS])
        http.patch.assert_awaited()

    async def test_firebase_list_replaces_defaults(self):
        encoded = player_state.encode_patch(
            {
                "shows": [
                    {
                        "source": "rss",
                        "id": "portraetalbum",
                        "fallback_name": "Portrætalbum",
                        "order": "latest",
                        "feed": "https://example.com/p.rss",
                    }
                ]
            }
        )

        class Ok:
            status_code = 200

            def json(self):
                return encoded

        http = AsyncMock()
        http.get = AsyncMock(return_value=Ok())
        shows = await podcast_catalog.hydrate(
            firebase_config={"projectId": "p", "apiKey": "k"},
            http_client=http,
            force=True,
        )
        self.assertEqual([row["id"] for row in shows], ["portraetalbum"])
        http.patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
