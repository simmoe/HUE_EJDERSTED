import unittest

import player_state


class PlayerStateCodecTests(unittest.TestCase):
    def test_roundtrip_nested_maps_and_arrays(self):
        patch = player_state.player_patch(
            {
                "active": True,
                "source": "sr",
                "showId": "sr:4914",
                "showTitle": "Text och musik med Eric Schüldt",
                "episodeId": "ep1",
                "episodeUri": "sr:episode:1",
                "episodeTitle": "Vila i vaggande våg",
                "episodeIndex": 0,
                "queue": [{"id": "ep1", "uri": "sr:episode:1", "name": "Vila i vaggande våg", "duration_ms": 3600000}],
                "playing": True,
                "positionMs": 12000,
                "durationMs": 3600000,
                "updatedAt": 1_700_000_000,
            },
            "dlna",
        )
        encoded = player_state.encode_patch(patch)
        decoded = player_state.decode_document(encoded)
        self.assertEqual(decoded["transport"]["active"], "podcast")
        self.assertEqual(decoded["podcasts"]["episodeTitle"], "Vila i vaggande våg")
        self.assertEqual(decoded["podcasts"]["engine"], "dlna")
        self.assertEqual(decoded["podcasts"]["queue"][0]["uri"], "sr:episode:1")

    def test_hydrate_from_player_doc(self):
        restored = player_state.state_from_doc(
            {
                "transport": {"active": "podcast"},
                "podcasts": {
                    "showTitle": "Text och musik med Eric Schüldt",
                    "episodeTitle": "Vila i vaggande våg",
                    "playing": True,
                    "source": "sr",
                    "showId": "sr:4914",
                    "episodeUri": "sr:episode:1",
                    "index": 0,
                    "queue": [],
                    "updatedAt": 1_700_000_000_000,
                },
            },
            "home",
        )
        self.assertIsNotNone(restored)
        state, engine = restored
        self.assertTrue(state["active"])
        self.assertEqual(state["episodeTitle"], "Vila i vaggande våg")
        self.assertEqual(state["updatedAt"], 1_700_000_000.0)
        self.assertEqual(engine, "dlna")

    def test_home_defaults_to_dlna_without_uri(self):
        restored = player_state.state_from_doc(
            {
                "transport": {"active": "podcast"},
                "podcasts": {"episodeTitle": "Vila i vaggande våg", "playing": True},
            },
            "home",
        )
        self.assertIsNotNone(restored)
        _, engine = restored
        self.assertEqual(engine, "dlna")

    def test_spotify_transport_does_not_restore_podcast(self):
        self.assertIsNone(
            player_state.state_from_doc(
                {
                    "transport": {"active": "spotify"},
                    "podcasts": {"episodeTitle": "leftover"},
                },
                "home",
            )
        )

    def test_clear_patch_empties_podcasts(self):
        patch = player_state.player_patch({"active": False, "updatedAt": 1_700_000_000}, "")
        self.assertEqual(patch["transport"]["active"], "")
        self.assertEqual(patch["podcasts"]["episodeTitle"], "")
        self.assertFalse(patch["podcasts"]["playing"])
