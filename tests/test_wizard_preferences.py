import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.config import ConfigError
from openai_tunnel_kit.wizard_preferences import load_preferences, save_last_organization_id


class WizardPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"OPENAI_TUNNEL_KIT_CONFIG_DIR": str(self.root)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_last_organization_round_trip_is_global_plaintext_metadata(self):
        save_last_organization_id("org_example")
        path = self.root / "wizard-preferences.json"
        self.assertEqual(load_preferences(), {"last_organization_id": "org_example"})
        self.assertEqual(json.loads(path.read_text()), {"last_organization_id": "org_example"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_non_organization_identifier(self):
        with self.assertRaisesRegex(ConfigError, "org_"):
            save_last_organization_id("proj_example")


if __name__ == "__main__":
    unittest.main()
