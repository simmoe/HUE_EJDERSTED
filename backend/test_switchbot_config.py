import unittest
from unittest.mock import patch

import hub_config


class SwitchbotConfigTests(unittest.TestCase):
    def test_public_config_hides_address(self):
        with patch.object(hub_config, "switchbot_address", return_value="ED:0F:02:06:46:56"):
            pub = hub_config.public_config()
        self.assertTrue(pub["switchbot"]["configured"])
        self.assertNotIn("ED:0F:02:06:46:56", str(pub))

    def test_public_config_unconfigured(self):
        with patch.object(hub_config, "switchbot_address", return_value=""):
            pub = hub_config.public_config()
        self.assertFalse(pub["switchbot"]["configured"])


if __name__ == "__main__":
    unittest.main()
