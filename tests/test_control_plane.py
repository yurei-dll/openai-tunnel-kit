import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.config import ConfigError, initialize_profile, read_environment
from openai_tunnel_kit.control_plane import attach_tunnel, inspect_tunnel, provision_tunnel


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"OPENAI_TUNNEL_KIT_CONFIG_DIR": str(self.root)})
        self.environment.start()
        self.mcp = self.root / "mcp.json"
        self.mcp.write_text('{"mcpServers":{"demo":{"command":"server"}}}')
        initialize_profile(
            "demo", binary="tunnel-client", mcp_source=self.mcp,
            tunnel_id="tunnel_old", api_key="sk-runtime", root=self.root,
        )

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    @patch("openai_tunnel_kit.control_plane.shutil.which", return_value="/usr/bin/tunnel-client")
    @patch("openai_tunnel_kit.control_plane.subprocess.run")
    def test_inspect_uses_runtime_key_without_printing_it(self, run, _which):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"id": "tunnel_old", "organization_ids": ["org_1"]})
        run.return_value.stderr = ""
        payload = inspect_tunnel("demo")
        self.assertEqual(payload["id"], "tunnel_old")
        self.assertEqual(run.call_args.kwargs["env"]["CONTROL_PLANE_API_KEY"], "sk-runtime")
        self.assertNotIn("sk-runtime", run.call_args.args[0])

    @patch("openai_tunnel_kit.control_plane.inspect_tunnel")
    @patch("openai_tunnel_kit.control_plane._run_json")
    def test_attach_verifies_workspace_round_trip(self, run_json, inspect):
        inspect.side_effect = [
            {"id": "tunnel_old", "organization_ids": ["org_1"]},
            {"id": "tunnel_old", "organization_ids": ["org_1"], "workspace_ids": ["ws_1"]},
        ]
        payload = attach_tunnel("demo", self.root / "admin.key", workspace_ids=["ws_1"])
        self.assertEqual(payload["workspace_ids"], ["ws_1"])
        self.assertIn("--workspace-id", run_json.call_args.args[1])

    @patch("openai_tunnel_kit.control_plane.inspect_tunnel")
    @patch("openai_tunnel_kit.control_plane._run_json")
    def test_provision_updates_profile_tunnel_id(self, run_json, inspect):
        run_json.return_value = {"id": "tunnel_new"}
        inspect.return_value = {"id": "tunnel_new", "workspace_ids": ["ws_1"]}
        provision_tunnel(
            "demo", self.root / "admin.key", "Demo", "Managed",
            workspace_ids=["ws_1"],
        )
        environment = read_environment(self.root / "profiles" / "demo.env")
        self.assertEqual(environment["CONTROL_PLANE_TUNNEL_ID"], "tunnel_new")
        self.assertEqual(environment["CONTROL_PLANE_API_KEY"], "sk-runtime")

    @patch("openai_tunnel_kit.control_plane.inspect_tunnel")
    @patch("openai_tunnel_kit.control_plane._run_json")
    def test_attach_rejects_backend_scope_mismatch(self, _run_json, inspect):
        inspect.side_effect = [
            {"id": "tunnel_old"},
            {"id": "tunnel_old", "workspace_ids": []},
        ]
        with self.assertRaisesRegex(ConfigError, "did not retain"):
            attach_tunnel("demo", self.root / "admin.key", workspace_ids=["ws_missing"])


if __name__ == "__main__":
    unittest.main()
