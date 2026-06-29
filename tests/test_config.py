import json
import tempfile
import unittest
from pathlib import Path

from openai_tunnel_kit.config import ConfigError, initialize_profile, read_environment, register_mcp


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_initialize_profile_is_explicit_and_reproducible(self):
        paths = initialize_profile(
            "work",
            "/opt/bin/tunnel-client",
            ["--listen", "127.0.0.1:9000"],
            root=self.root,
            tunnel_id="tunnel_example",
            api_key="sk-example",
        )

        environment = read_environment(paths.env)
        self.assertEqual(environment["CONTROL_PLANE_TUNNEL_ID"], "tunnel_example")
        self.assertEqual(environment["CONTROL_PLANE_API_KEY"], "sk-example")
        self.assertEqual(environment["TUNNEL_CLIENT_BIN"], "/opt/bin/tunnel-client")
        self.assertIn("127.0.0.1:9000", environment["TUNNEL_CLIENT_ARGS"])
        self.assertNotIn("MCP_CONFIG", environment)
        self.assertEqual(paths.env.stat().st_mode & 0o777, 0o600)
        self.assertIn("mcpServers", json.loads(paths.mcp.read_text()))

    def test_existing_profile_requires_force(self):
        initialize_profile("work", root=self.root)
        with self.assertRaisesRegex(ConfigError, "already exists"):
            initialize_profile("work", root=self.root)

    def test_force_preserves_existing_mcp(self):
        paths = initialize_profile("work", root=self.root)
        paths.mcp.write_text(
            '{"mcpServers":{"mine":{"command":"node","args":["/repo/server.js"]}}}\n'
        )
        initialize_profile("work", force=True, root=self.root)
        self.assertIn("mine", paths.mcp.read_text())
        self.assertIn("--mcp.command", read_environment(paths.env)["TUNNEL_CLIENT_MCP_ARGS"])

    def test_register_mcp_validates_and_normalizes_json(self):
        paths = initialize_profile("work", root=self.root)
        source = self.root / "source.json"
        source.write_text(
            '{"mcpServers":{"mine":{"command":"node","args":["/repo/dist/index.js"],"env":{"MODE":"test"}}}}'
        )
        register_mcp("work", source, root=self.root)
        environment = read_environment(paths.env)
        self.assertIn("--mcp.command", environment["TUNNEL_CLIENT_MCP_ARGS"])
        self.assertIn("/repo/dist/index.js", environment["TUNNEL_CLIENT_MCP_ARGS"])
        self.assertEqual(environment["MODE"], "test")

    def test_mcp_file_configures_launch_during_init(self):
        source = self.root / "source.json"
        source.write_text('{"mcpServers":{"mine":{"command":"node","args":["/repo/server.js"]}}}')
        paths = initialize_profile("work", mcp_source=source, root=self.root)
        environment = read_environment(paths.env)
        self.assertIn("--mcp.command", environment["TUNNEL_CLIENT_MCP_ARGS"])

    def test_desktop_environment_preference_is_stored(self):
        paths = initialize_profile("desktop", root=self.root, pass_desktop_environment=True)
        environment = read_environment(paths.env)
        self.assertEqual(environment["OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT"], "true")

    def test_rejects_unsafe_profile_name(self):
        with self.assertRaises(ConfigError):
            initialize_profile("../escape", root=self.root)


if __name__ == "__main__":
    unittest.main()
