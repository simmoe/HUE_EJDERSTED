import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import audio_log


class AudioLogTests(unittest.TestCase):
    def setUp(self):
        audio_log.reset()
        audio_log._path = None
        audio_log._site = ""

    def test_recent_keeps_order_and_limit(self):
        audio_log.log("podcast.clear", source="sr", title="Sommarens sista suck")
        audio_log.log("spotify.play-uris", ok=True, n=12)
        events = audio_log.recent(1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "spotify.play-uris")
        self.assertTrue(events[0]["ok"])
        self.assertEqual(len(audio_log.recent(40)), 2)

    def test_empty_fields_are_dropped(self):
        row = audio_log.log("dlna.stop", detail="", source="sr")
        self.assertNotIn("detail", row)
        self.assertEqual(row["source"], "sr")

    def test_configure_reloads_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.jsonl"
            audio_log.configure(path, site="home")
            audio_log.log("podcast.play", title="Sommarens sista suck", ok=True)
            audio_log.reset()
            audio_log.configure(path, site="home")
            events = audio_log.recent(10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "podcast.play")
            self.assertEqual(events[0]["site"], "home")
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_event_doc_id_is_stable(self):
        row = {"t": "2026-09-03T06:05:00+00:00", "event": "spotify.play-uris", "uri": "spotify:track:abc"}
        self.assertIn("spotify-play-uris", audio_log.event_doc_id(row))


class AudioLogRemoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_persist_remote_patches_site_collection(self):
        http = AsyncMock()
        http.patch = AsyncMock(return_value=type("R", (), {"status_code": 200})())
        row = {"t": "2026-09-03T06:05:00+00:00", "event": "podcast.clear", "site": "home"}
        ok = await audio_log.persist_remote(
            row,
            firebase_config={"projectId": "p5-diary-ca5f7", "apiKey": "x"},
            http_client=http,
            site="home",
        )
        self.assertTrue(ok)
        url = http.patch.call_args.args[0]
        self.assertIn("/documents/ejdersted/audio_home/events/", url)
        body = http.patch.call_args.kwargs["json"]
        self.assertEqual(body["fields"]["event"]["stringValue"], "podcast.clear")
