import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.config import ConfigError, read_environment, set_tunnel_id
from openai_tunnel_kit.wizard import _target_config, run_setup


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"OPENAI_TUNNEL_KIT_CONFIG_DIR": str(self.root)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def payload(self):
        return {
            "confirm": True,
            "profile": "demo",
            "target_mode": "command",
            "command": "node /repo/dist/index.js",
            "tunnel_mode": "existing",
            "tunnel_id": "tunnel_demo",
            "runtime_api_key": "sk-runtime",
            "install_service": True,
        }

    def test_url_target_requires_https_except_loopback(self):
        with self.assertRaisesRegex(ConfigError, "HTTPS"):
            _target_config({"target_mode": "url", "mcp_url": "http://example.com/mcp"})
        self.assertEqual(
            _target_config({"target_mode": "url", "mcp_url": "https://example.com/mcp"}),
            {"url": "https://example.com/mcp"},
        )

    def test_setup_requires_confirmation(self):
        payload = self.payload()
        payload["confirm"] = False
        with self.assertRaisesRegex(ConfigError, "confirmation"):
            run_setup(payload)

    @patch("openai_tunnel_kit.wizard.install_service")
    @patch("openai_tunnel_kit.wizard.encrypt_api_key")
    @patch("openai_tunnel_kit.wizard.inspect_tunnel")
    def test_existing_tunnel_setup_removes_plaintext_runtime_key(self, inspect, encrypt, install):
        inspect.return_value = {"id": "tunnel_demo", "organization_ids": ["org_1"]}
        result = run_setup(self.payload())
        profile = read_environment(self.root / "profiles" / "demo.env")
        self.assertNotIn("sk-runtime", (self.root / "profiles" / "demo.env").read_text())
        self.assertEqual(profile["CONTROL_PLANE_TUNNEL_ID"], "tunnel_demo")
        self.assertFalse(result["workspace_attached"])
        encrypt.assert_called_once_with("sk-runtime", self.root / "credentials" / "demo.api-key.cred")
        install.assert_called_once_with("demo", start=True)

    @patch("openai_tunnel_kit.wizard.install_service")
    @patch("openai_tunnel_kit.wizard.encrypt_api_key")
    @patch("openai_tunnel_kit.wizard.provision_tunnel")
    def test_provision_uses_explicit_scopes_and_returns_handoff(self, provision, _encrypt, _install):
        def create(profile, *_args, **_kwargs):
            set_tunnel_id(profile, "tunnel_new")
            return {"id": "tunnel_new", "workspace_ids": ["ws_1"]}

        provision.side_effect = create
        payload = self.payload()
        payload.update({
            "tunnel_mode": "provision",
            "tunnel_id": "",
            "admin_api_key": "sk-admin",
            "workspace_ids": "ws_1",
        })
        result = run_setup(payload)
        self.assertTrue(result["workspace_attached"])
        self.assertIn("select this tunnel", result["next"])
        self.assertNotIn("sk-admin", (self.root / "profiles" / "demo.env").read_text())


if __name__ == "__main__":
    unittest.main()
