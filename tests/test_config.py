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
        paths = initialize_profile("work", "/opt/bin/tunnel-client", ["--listen", "127.0.0.1:9000"], root=self.root)

        environment = read_environment(paths.env)
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
        paths.mcp.write_text('{"mcpServers":{"mine":{}}}\n')
        initialize_profile("work", force=True, root=self.root)
        self.assertIn("mine", paths.mcp.read_text())

    def test_register_mcp_validates_and_normalizes_json(self):
        paths = initialize_profile("work", root=self.root)
        source = self.root / "source.json"
        source.write_text('{"z": 1, "a": 2}')
        register_mcp("work", source, root=self.root)
        self.assertEqual(paths.mcp.read_text(), '{\n  "a": 2,\n  "z": 1\n}\n')

    def test_rejects_unsafe_profile_name(self):
        with self.assertRaises(ConfigError):
            initialize_profile("../escape", root=self.root)


if __name__ == "__main__":
    unittest.main()
