import unittest

import gate_light


class GateSceneTests(unittest.TestCase):
    def test_the_button_walks_through_every_scene_and_back(self):
        scene = 1
        seen = []
        for _ in range(gate_light.SCENES + 1):
            seen.append(scene)
            scene = gate_light.next_scene(scene)
        self.assertEqual(seen, [1, 2, 3, 4, 1])

    def test_publish_packet_names_the_topic_and_the_scene(self):
        body = b"have_laage" 
        packet = gate_light._packet(0x30, len(body).to_bytes(2, "big") + body + b"2")
        self.assertIn(b"have_laage", packet)
        self.assertTrue(packet.endswith(b"2"))
