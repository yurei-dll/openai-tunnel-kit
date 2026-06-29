import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from openai_tunnel_kit.config import initialize_profile
from openai_tunnel_kit.systemd import install_service
from openai_tunnel_kit.templates import render_systemd_unit


class SystemdTests(unittest.TestCase):
    def test_unit_is_user_template_with_restart_and_environment_file(self):
        unit = render_systemd_unit(Path("/home/example/.config/openai-tunnel-kit"))
        self.assertIn("EnvironmentFile=/home/example/.config/openai-tunnel-kit/profiles/%i.env", unit)
        self.assertIn('Environment="MCP_CONFIG=/home/example/.config/openai-tunnel-kit/profiles/%i.mcp.json"', unit)
        self.assertIn("ExecStart=/usr/bin/env ${TUNNEL_CLIENT_BIN} $TUNNEL_CLIENT_ARGS", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)

    @patch("openai_tunnel_kit.systemd.write_text_atomic")
    @patch("openai_tunnel_kit.systemd.run_systemctl")
    def test_install_reloads_enables_and_starts(self, systemctl, write):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_profile("demo", root=root)
            install_service("demo", root=root)
        self.assertEqual(systemctl.call_args_list, [call(["daemon-reload"]), call(["enable", "--now", "tunnel-client@demo.service"])])
        self.assertEqual(write.call_args.kwargs["mode"], 0o644)


if __name__ == "__main__":
    unittest.main()
