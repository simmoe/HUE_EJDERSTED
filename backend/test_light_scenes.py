import unittest

import light_scenes


class FestFrameTests(unittest.TestCase):
    def test_stays_in_sunrise_band(self):
        hues = [light_scenes.fest_frame(t).hue for t in (0, 1.8, 3.7, 7.4, 11)]
        self.assertTrue(all(8 <= h <= 38 for h in hues))
        sats = [light_scenes.fest_frame(t).sat for t in hues]
        self.assertTrue(all(70 <= s <= 95 for s in sats))

    def test_named_whites(self):
        self.assertEqual(light_scenes.command_for("kraftig").brightness, 100)
        self.assertEqual(light_scenes.command_for("dæmpet").brightness, 22)
        self.assertFalse(light_scenes.command_for("off").on)


if __name__ == "__main__":
    unittest.main()
