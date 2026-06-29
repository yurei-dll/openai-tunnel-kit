import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openai_tunnel_kit.cli import main


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
        code, output, error = self.invoke(["init", "demo"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Next: openai-tunnel-kit install-service demo", output)

        code, output, error = self.invoke(["print-mcp", "demo"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn('"mcpServers"', output)

    def test_missing_profile_has_actionable_error(self):
        code, _, error = self.invoke(["print-mcp", "missing"])
        self.assertEqual(code, 2)
        self.assertIn("openai-tunnel-kit init missing", error)

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

