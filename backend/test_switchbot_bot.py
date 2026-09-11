import unittest

import switchbot_bot


class SwitchbotCommandsTests(unittest.TestCase):
    def test_on_off_are_the_ble_action_bytes(self):
        self.assertEqual(switchbot_bot.COMMANDS["on"], bytes.fromhex("570101"))
        self.assertEqual(switchbot_bot.COMMANDS["off"], bytes.fromhex("570102"))
        self.assertEqual(switchbot_bot.COMMANDS["press"], bytes.fromhex("570100"))


if __name__ == "__main__":
    unittest.main()
