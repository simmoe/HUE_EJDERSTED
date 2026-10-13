import unittest
from unittest.mock import AsyncMock

import spotify


def _client() -> spotify.Spotify:
    client = spotify.Spotify.__new__(spotify.Spotify)
    client._http = AsyncMock()
    client._cfg = {"refresh_token": "x"}
    return client


class VoiceSearchErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_dead_session_is_not_an_empty_search(self):
        client = _client()
        client._search = AsyncMock(return_value=(None, "disconnected"))
        row = await client.voice_command("jeff buckley")
        self.assertEqual(row["reason"], "disconnected")
        self.assertEqual(row["error"], "spotify ikke forbundet")
        self.assertNotIn("Fandt ikke", row["error"])

    async def test_a_dead_api_is_not_an_empty_search(self):
        client = _client()
        client._search = AsyncMock(return_value=(None, "unreachable"))
        row = await client.voice_command("jeff buckley")
        self.assertEqual(row["reason"], "unreachable")
        self.assertEqual(row["error"], "spotify svarede ikke")

    async def test_a_real_miss_still_says_not_found(self):
        client = _client()
        empty = {
            "tracks": {"items": []},
            "artists": {"items": []},
            "albums": {"items": []},
            "playlists": {"items": []},
        }
        client._search = AsyncMock(return_value=(empty, ""))
        row = await client.voice_command("xyzzy-not-a-song")
        self.assertEqual(row["reason"], "empty")
        self.assertIn("Fandt ikke", row["error"])
