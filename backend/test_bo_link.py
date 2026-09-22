import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import bo_link


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = b"{}" if payload is not None else b""
    r.json.return_value = payload or {}
    return r


class EnsureM5SpotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_selects_spotify_and_returns_connect_id(self):
        http = AsyncMock()
        http.put = AsyncMock(return_value=_resp())
        http.post = AsyncMock(return_value=_resp())
        http.get = AsyncMock(
            side_effect=[
                _resp(payload={"volume": {"speaker": {"level": 28}}}),
                _resp(
                    payload={
                        "deviceID": "m5-connect",
                        "spotifyError": 0,
                        "remoteName": "Beoplay M5 + BeoPlay A9",
                    }
                ),
            ]
        )
        add_user = AsyncMock(return_value=True)
        with (
            patch.object(bo_link, "_http", http),
            patch.object(bo_link.asyncio, "sleep", AsyncMock()),
            patch.object(bo_link, "add_spotify_user", add_user),
        ):
            device_id = await bo_link.ensure_m5_spotify("user", "token")
        self.assertEqual(device_id, "m5-connect")
        add_user.assert_awaited_once()
        source = http.post.call_args.args[0]
        self.assertIn("ActiveSourceType", source)
        self.assertEqual(http.post.call_args.kwargs["json"], {"sourceType": {"type": "SPOTIFY"}})

    async def test_add_user_posts_access_token_blob(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=_resp(payload={"status": 101, "spotifyError": 0}))
        with patch.object(bo_link, "_http", http):
            self.assertTrue(await bo_link.add_spotify_user("simon", "tok"))
        posted = http.post.call_args.kwargs["data"]
        self.assertEqual(posted["action"], "addUser")
        self.assertEqual(posted["userName"], "simon")
        self.assertEqual(posted["tokenType"], "accesstoken")
        self.assertEqual(posted["blob"], "tok")
        self.assertEqual(posted["loginId"], "ejdersted")

    async def test_returns_none_when_speaker_has_no_connect_id(self):
        http = AsyncMock()
        http.put = AsyncMock(return_value=_resp())
        http.post = AsyncMock(return_value=_resp())
        http.get = AsyncMock(return_value=_resp(payload={"deviceID": "", "spotifyError": 1}))
        with patch.object(bo_link, "_http", http), patch.object(bo_link.asyncio, "sleep", AsyncMock()):
            self.assertIsNone(await bo_link.ensure_m5_spotify())


class ExpandToA9Tests(unittest.IsolatedAsyncioTestCase):
    async def test_wakes_a9_before_joining_m5(self):
        http = AsyncMock()
        http.put = AsyncMock(return_value=_resp())
        http.post = AsyncMock(return_value=_resp())
        http.get = AsyncMock(return_value=_resp(payload={"volume": {"speaker": {"level": 35}}}))
        with patch.object(bo_link, "_http", http), patch.object(bo_link.asyncio, "sleep", AsyncMock()):
            await bo_link.expand_to_a9("dlna")
        wake = http.put.call_args_list[0]
        self.assertIn("powerManagement/standby", wake.args[0])
        self.assertEqual(wake.kwargs["json"], {"standby": {"powerState": "on"}})
        join = http.post.call_args
        self.assertIn("ActiveSources", join.args[0])
        self.assertTrue(join.kwargs["json"]["primaryExperience"]["source"]["id"].startswith("dlna:"))
