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

    async def test_dev_mode_allowlist_is_not_a_dead_session(self):
        client = _client()
        client._search = AsyncMock(return_value=(None, "not_registered"))
        row = await client.voice_command("jeff buckley")
        self.assertEqual(row["reason"], "not_registered")
        self.assertIn("kender ikke", row["error"])
        self.assertNotIn("Fandt ikke", row["error"])
        self.assertNotEqual(row["error"], "spotify ikke forbundet")


class SearchHttpReasonTests(unittest.TestCase):
    def test_403_not_registered_is_its_own_reason(self):
        why = spotify.search_http_reason(
            403,
            '{"error":{"message":"The user is not registered for this application."}}',
        )
        self.assertEqual(why, "not_registered")

    def test_401_is_a_dead_session(self):
        self.assertEqual(spotify.search_http_reason(401, "invalid token"), "disconnected")

    def test_200_is_ok(self):
        self.assertEqual(spotify.search_http_reason(200, ""), "")


class SearchAuthErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_403_not_registered_does_not_revoke_the_token(self):
        client = _client()
        client._cfg["auth_error"] = "revoked"
        client._headers = AsyncMock(return_value={"Authorization": "Bearer x"})
        client._clear_auth_error = lambda: client._cfg.pop("auth_error", None)
        resp = AsyncMock()
        resp.status_code = 403
        resp.text = '{"error":{"message":"The user is not registered for this application."}}'
        client._http.get = AsyncMock(return_value=resp)
        body, why = await client._search("jeff buckley")
        self.assertIsNone(body)
        self.assertEqual(why, "not_registered")
        self.assertNotIn("auth_error", client._cfg)

    async def test_401_marks_the_session_revoked(self):
        client = _client()
        client._headers = AsyncMock(return_value={"Authorization": "Bearer x"})
        client._set_auth_error = lambda code: client._cfg.__setitem__("auth_error", code)
        resp = AsyncMock()
        resp.status_code = 401
        resp.text = "invalid token"
        client._http.get = AsyncMock(return_value=resp)
        body, why = await client._search("jeff buckley")
        self.assertIsNone(body)
        self.assertEqual(why, "disconnected")
        self.assertEqual(client._cfg.get("auth_error"), "revoked")

    async def test_200_clears_a_stale_auth_error(self):
        client = _client()
        client._cfg["auth_error"] = "revoked"
        client._headers = AsyncMock(return_value={"Authorization": "Bearer x"})
        client._clear_auth_error = lambda: client._cfg.pop("auth_error", None)
        resp = AsyncMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.content = b"{}"
        resp.json = lambda: {"tracks": {"items": []}}
        client._http.get = AsyncMock(return_value=resp)
        body, why = await client._search("jeff buckley")
        self.assertEqual(why, "")
        self.assertIsInstance(body, dict)
        self.assertNotIn("auth_error", client._cfg)
