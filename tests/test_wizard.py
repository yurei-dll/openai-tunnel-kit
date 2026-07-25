import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.config import ConfigError, read_environment, set_tunnel_id
from openai_tunnel_kit.wizard import (
    _admin_key_for_payload, _html, _target_config, _wizard_tunnel_arguments, run_setup,
    run_setup_with_admin_credential,
)
from openai_tunnel_kit.platform_admin import ServiceAccountCredential


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
            "credential_mode": "existing",
            "install_service": True,
        }

    def test_url_target_requires_https_except_loopback(self):
        with self.assertRaisesRegex(ConfigError, "HTTPS"):
            _target_config({"target_mode": "url", "mcp_url": "http://example.com/mcp"})
        self.assertEqual(
            _target_config({"target_mode": "url", "mcp_url": "https://example.com/mcp"}),
            {"url": "https://example.com/mcp"},
        )

    def test_wizard_adds_per_profile_ephemeral_health_endpoint(self):
        health_file = self.root / "health" / "demo.url"
        self.assertEqual(
            _wizard_tunnel_arguments({}, health_file),
            (
                "--health.listen-addr", "127.0.0.1:0",
                "--health.url-file", str(health_file),
            ),
        )

    def test_wizard_preserves_explicit_health_arguments(self):
        existing = {
            "TUNNEL_CLIENT_ARGS": (
                '"--health.listen-addr" "127.0.0.1:9090" '
                '"--health.url-file=/tmp/custom-health.url"'
            )
        }
        self.assertEqual(
            _wizard_tunnel_arguments(existing, self.root / "health" / "demo.url"),
            (
                "--health.listen-addr", "127.0.0.1:9090",
                "--health.url-file=/tmp/custom-health.url",
            ),
        )

    def test_wizard_url_is_flushed_for_portable_launchers(self):
        source = Path(__file__).resolve().parents[1] / "src/openai_tunnel_kit/wizard.py"
        self.assertIn('print(f"Wizard: {url}", flush=True)', source.read_text())

    def test_setup_requires_confirmation(self):
        payload = self.payload()
        payload["confirm"] = False
        with self.assertRaisesRegex(ConfigError, "confirmation"):
            run_setup(payload)

    def test_required_fields_have_visible_red_markers(self):
        html = _html("test-token")
        self.assertIn('.req{color:#ff5c5c', html)
        self.assertIn('Profile name <span class="req"', html)
        self.assertIn('<input name="profile" required>', html)
        self.assertNotIn('value="personal-access-tool"', html)
        self.assertLess(html.index('name="mcp_file"'), html.index('name="profile"'))
        self.assertIn("const names=Object.keys(servers);if(names.length===1)", html)
        self.assertIn("form.elements.profile.value=names[0]", html)
        self.assertIn('At least one organization or workspace ID is required', html)
        self.assertIn('Save entered key globally in system wallet', html)
        self.assertIn('Forget saved key', html)
        self.assertIn('<legend>1. Admin access and auto-populate</legend>', html)
        self.assertIn('id="auto-populate">Auto-populate</button>', html)
        self.assertIn("Promise.all([fetch('/api/projects'", html)
        self.assertNotIn('id="load-projects"', html)
        self.assertIn('data-req="mcp-url"', html)
        self.assertIn("setRequiredMarker('mcp-url',!hasFile&&mode==='url')", html)
        self.assertIn("setRequiredMarker('runtime-key',credentialMode==='existing')", html)
        self.assertIn("setRequiredMarker('tunnel-id',tunnelMode==='existing')", html)
        self.assertIn("setRequiredMarker(name,needsScope&&!hasScope)", html)
        self.assertNotIn('Tunnel name <span class="req"', html)

    @patch("openai_tunnel_kit.wizard.load_admin_key", return_value="sk-saved-admin")
    def test_saved_admin_key_is_global_and_resolved_in_backend(self, load_key):
        key, should_save = _admin_key_for_payload({"use_saved_admin_key": True})
        self.assertEqual(key, "sk-saved-admin")
        self.assertFalse(should_save)
        load_key.assert_called_once_with()

    @patch("openai_tunnel_kit.wizard.load_admin_key")
    def test_entered_admin_key_overrides_saved_key(self, load_key):
        key, should_save = _admin_key_for_payload({
            "admin_api_key": "sk-new-admin",
            "use_saved_admin_key": True,
            "save_admin_key": True,
        })
        self.assertEqual(key, "sk-new-admin")
        self.assertTrue(should_save)
        load_key.assert_not_called()

    @patch("openai_tunnel_kit.wizard.save_admin_key")
    @patch("openai_tunnel_kit.wizard.list_projects", return_value=[])
    @patch("openai_tunnel_kit.wizard.run_setup", return_value={"ok": True})
    def test_setup_validates_and_saves_admin_key_before_mutation(self, setup, projects, save_key):
        payload = {"admin_api_key": "sk-new-admin", "save_admin_key": True}
        self.assertEqual(run_setup_with_admin_credential(payload), {"ok": True})
        projects.assert_called_once_with("sk-new-admin")
        setup.assert_called_once()
        save_key.assert_called_once_with("sk-new-admin")

    @patch("openai_tunnel_kit.wizard.save_admin_key")
    @patch("openai_tunnel_kit.wizard.list_projects", side_effect=ConfigError("invalid key"))
    @patch("openai_tunnel_kit.wizard.run_setup")
    def test_invalid_admin_key_is_not_saved_or_used_for_setup(self, setup, _projects, save_key):
        with self.assertRaisesRegex(ConfigError, "invalid key"):
            run_setup_with_admin_credential({
                "admin_api_key": "sk-invalid", "save_admin_key": True,
            })
        save_key.assert_not_called()
        setup.assert_not_called()

    @patch("openai_tunnel_kit.wizard.create_service_account")
    def test_missing_tunnel_scope_fails_before_external_mutation(self, create_account):
        payload = self.payload()
        payload.update({
            "credential_mode": "automatic",
            "tunnel_mode": "provision",
            "tunnel_id": "",
            "admin_api_key": "sk-admin",
            "project_id": "proj_1",
            "runtime_api_key": "",
        })
        with self.assertRaisesRegex(ConfigError, "no account, tunnel, or profile was created"):
            run_setup(payload)
        create_account.assert_not_called()
        self.assertFalse((self.root / "profiles" / "demo.env").exists())

    def test_incomplete_profile_has_safe_retry_guidance(self):
        from openai_tunnel_kit.config import initialize_profile
        initialize_profile("demo", root=self.root)
        with self.assertRaisesRegex(ConfigError, "enable 'Replace existing profile'"):
            run_setup(self.payload())

    @patch("openai_tunnel_kit.wizard.install_service")
    @patch("openai_tunnel_kit.wizard.wait_for_service")
    @patch("openai_tunnel_kit.wizard.encrypt_api_key")
    @patch("openai_tunnel_kit.wizard.inspect_tunnel")
    def test_existing_tunnel_setup_removes_plaintext_runtime_key(self, inspect, encrypt, wait, install):
        inspect.return_value = {"id": "tunnel_demo", "organization_ids": ["org_1"]}
        result = run_setup(self.payload())
        profile = read_environment(self.root / "profiles" / "demo.env")
        self.assertNotIn("sk-runtime", (self.root / "profiles" / "demo.env").read_text())
        self.assertEqual(profile["CONTROL_PLANE_TUNNEL_ID"], "tunnel_demo")
        self.assertIn("127.0.0.1:0", profile["TUNNEL_CLIENT_ARGS"])
        self.assertIn("demo.url", profile["TUNNEL_CLIENT_ARGS"])
        self.assertFalse(result["workspace_attached"])
        encrypt.assert_called_once_with("sk-runtime", self.root / "credentials" / "demo.api-key.cred")
        install.assert_called_once_with("demo", start=True)
        wait.assert_called_once_with("demo")

    @patch("openai_tunnel_kit.wizard.create_service_account")
    @patch("openai_tunnel_kit.wizard.install_service")
    @patch("openai_tunnel_kit.wizard.wait_for_service")
    @patch("openai_tunnel_kit.wizard.encrypt_api_key")
    @patch("openai_tunnel_kit.wizard.inspect_tunnel")
    @patch("openai_tunnel_kit.wizard.decrypt_api_key", return_value="sk-reused-runtime")
    def test_replacing_profile_reuses_automatic_runtime_credential(
        self, decrypt, inspect, encrypt, wait, install, create_account,
    ):
        from openai_tunnel_kit.config import initialize_profile
        paths = initialize_profile("demo", root=self.root, tunnel_id="tunnel_demo")
        paths.credential.parent.mkdir(parents=True)
        paths.credential.write_text("encrypted")
        inspect.return_value = {"id": "tunnel_demo", "organization_ids": ["org_1"]}
        payload = self.payload()
        payload.update({
            "credential_mode": "automatic",
            "runtime_api_key": "",
            "admin_api_key": "",
            "project_id": "",
            "replace_profile": True,
        })

        result = run_setup(payload)

        self.assertEqual(result["runtime_credential_action"], "reused")
        decrypt.assert_called_once_with(self.root / "credentials" / "demo.api-key.cred")
        create_account.assert_not_called()
        inspect.assert_called_once_with("demo", "sk-reused-runtime")
        encrypt.assert_called_once_with(
            "sk-reused-runtime", self.root / "credentials" / "demo.api-key.cred"
        )

    @patch("openai_tunnel_kit.wizard.install_service")
    @patch("openai_tunnel_kit.wizard.wait_for_service")
    @patch("openai_tunnel_kit.wizard.encrypt_api_key")
    @patch("openai_tunnel_kit.wizard.provision_tunnel")
    @patch("openai_tunnel_kit.wizard.create_service_account")
    def test_provision_uses_explicit_scopes_and_returns_handoff(self, create_account, provision, _encrypt, _wait, _install):
        def create(profile, *_args, **_kwargs):
            set_tunnel_id(profile, "tunnel_new")
            return {"id": "tunnel_new", "workspace_ids": ["ws_1"]}

        provision.side_effect = create
        create_account.return_value = ServiceAccountCredential("svc_1", "key_1", "sk-generated")
        payload = self.payload()
        payload.update({
            "credential_mode": "automatic",
            "tunnel_mode": "provision",
            "tunnel_id": "",
            "admin_api_key": "sk-admin",
            "project_id": "proj_1",
            "runtime_api_key": "",
            "workspace_ids": "ws_1",
        })
        result = run_setup(payload)
        self.assertTrue(result["workspace_attached"])
        self.assertIn("select this tunnel", result["next"])
        create_account.assert_called_once_with("sk-admin", "proj_1", "openai-tunnel-kit-demo")
        self.assertEqual(provision.call_args.args[2], "demo")
        self.assertNotIn("sk-admin", (self.root / "profiles" / "demo.env").read_text())
        self.assertNotIn("sk-generated", (self.root / "profiles" / "demo.env").read_text())

    @patch("openai_tunnel_kit.wizard.delete_service_account")
    @patch("openai_tunnel_kit.wizard.create_service_account")
    @patch("openai_tunnel_kit.wizard.provision_tunnel")
    def test_failed_provision_rolls_back_new_service_account(self, provision, create_account, delete_account):
        create_account.return_value = ServiceAccountCredential("svc_1", "key_1", "sk-generated")
        provision.side_effect = ConfigError("tunnel create denied")
        payload = self.payload()
        payload.update({
            "credential_mode": "automatic",
            "tunnel_mode": "provision",
            "tunnel_id": "",
            "admin_api_key": "sk-admin",
            "project_id": "proj_1",
            "runtime_api_key": "",
            "organization_ids": "org_1",
        })
        with self.assertRaisesRegex(ConfigError, "tunnel create denied"):
            run_setup(payload)
        delete_account.assert_called_once_with("sk-admin", "proj_1", "svc_1")
        self.assertFalse((self.root / "profiles" / "demo.env").exists())


if __name__ == "__main__":
    unittest.main()
