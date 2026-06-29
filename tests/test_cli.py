import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.cli import main
from openai_tunnel_kit.config import ConfigError


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"OPENAI_TUNNEL_KIT_CONFIG_DIR": str(self.root)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def invoke(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_init_then_print_mcp(self):
        code, output, error = self.invoke(
            ["init", "demo", "--tunnel-id", "tunnel_example", "--api-key", "sk-example"]
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Next: openai-tunnel-kit install-service demo", output)

        code, output, error = self.invoke(["print-mcp", "demo"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn('"mcpServers"', output)

    def test_init_points_out_missing_credentials(self):
        code, output, error = self.invoke(["init", "demo"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("CONTROL_PLANE_TUNNEL_ID", output)
        self.assertIn("CONTROL_PLANE_API_KEY", output)

    def test_init_reads_api_key_file(self):
        key_file = self.root / "runtime.key"
        key_file.write_text("sk-from-file\n")
        code, _, error = self.invoke(
            ["init", "demo", "--tunnel-id", "tunnel_example", "--api-key-file", str(key_file)]
        )
        self.assertEqual((code, error), (0, ""))
        profile = (self.root / "profiles" / "demo.env").read_text()
        self.assertIn('CONTROL_PLANE_API_KEY="sk-from-file"', profile)

    def test_missing_profile_has_actionable_error(self):
        code, _, error = self.invoke(["print-mcp", "missing"])
        self.assertEqual(code, 2)
        self.assertIn("openai-tunnel-kit init missing", error)

    def test_list_profiles_is_sorted(self):
        self.invoke(["init", "zebra"])
        self.invoke(["init", "alpha"])
        code, output, error = self.invoke(["list-profiles"])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output, "alpha\nzebra\n")

    @patch("openai_tunnel_kit.cli.uninstall_service")
    def test_remove_profile_removes_service_before_files(self, uninstall):
        self.invoke(["init", "demo"])
        profile = self.root / "profiles" / "demo.env"
        uninstall.side_effect = lambda _: self.assertTrue(profile.exists())
        code, output, error = self.invoke(["remove-profile", "demo"])
        self.assertEqual((code, error), (0, ""))
        uninstall.assert_called_once_with("demo")
        self.assertFalse(profile.exists())
        self.assertIn("Disabled service and deleted profile demo", output)

    @patch("openai_tunnel_kit.cli.uninstall_service")
    def test_remove_profile_keeps_files_when_service_removal_fails(self, uninstall):
        self.invoke(["init", "demo"])
        profile = self.root / "profiles" / "demo.env"
        uninstall.side_effect = ConfigError("systemd failure")
        code, _, error = self.invoke(["remove-profile", "demo"])
        self.assertEqual(code, 2)
        self.assertIn("systemd failure", error)
        self.assertTrue(profile.exists())

    @patch("openai_tunnel_kit.cli.install_service")
    def test_install_service_starts_by_default(self, install):
        self.invoke(["init", "demo"])
        install.return_value = self.root / "tunnel-client@.service"
        code, output, _ = self.invoke(["install-service", "demo"])
        self.assertEqual(code, 0)
        install.assert_called_once_with("demo", True)
        self.assertIn("enabled and started", output)


if __name__ == "__main__":
    unittest.main()
