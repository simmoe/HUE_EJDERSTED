import unittest
from unittest.mock import AsyncMock, patch

import bo_dlna


class MozartPauseResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_uses_mozart_when_it_answers(self):
        resp = AsyncMock()
        resp.status_code = 200
        resp.text = ""
        with patch.object(bo_dlna, "_http") as http, patch.object(bo_dlna, "_soap", AsyncMock()) as soap:
            http.post = AsyncMock(return_value=resp)
            ok, detail = await bo_dlna.pause()
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        http.post.assert_awaited_once()
        soap.assert_not_called()

    async def test_pause_falls_back_to_dlna_when_mozart_fails(self):
        resp = AsyncMock()
        resp.status_code = 500
        resp.text = "nope"
        with patch.object(bo_dlna, "_http") as http, patch.object(
            bo_dlna, "_soap", AsyncMock(return_value=(True, ""))
        ) as soap:
            http.post = AsyncMock(return_value=resp)
            ok, detail = await bo_dlna.pause()
        self.assertTrue(ok)
        soap.assert_awaited_once()
