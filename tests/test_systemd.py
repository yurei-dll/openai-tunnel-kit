import tempfile
import unittest
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import call, patch

from openai_tunnel_kit.config import initialize_profile
from openai_tunnel_kit.systemd import credential_problem, inspect_service, install_service
from openai_tunnel_kit.templates import render_desktop_environment_drop_in, render_systemd_unit


class SystemdTests(unittest.TestCase):
    @patch("openai_tunnel_kit.systemd.shutil.which", return_value="/usr/bin/systemctl")
    @patch("openai_tunnel_kit.systemd.subprocess.run")
    def test_auto_restart_is_not_reported_as_running(self, run, _which):
        run.return_value.returncode = 0
        run.return_value.stdout = (
            "UnitFileState=enabled\nActiveState=activating\n"
            "SubState=auto-restart\nResult=exit-code\n"
            "ExecMainStatus=1\nNRestarts=37\n"
        )
        state = inspect_service("demo")
        self.assertTrue(state.enabled)
        self.assertFalse(state.running)
        self.assertIn("activating/auto-restart", state.detail)
        self.assertIn("exit=1", state.detail)

    def test_install_refuses_missing_credentials_without_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_profile("demo", root=root)
            with self.assertRaisesRegex(Exception, "credential preflight failed"):
                install_service("demo", root=root)

    @patch("openai_tunnel_kit.systemd.write_text_atomic")
    @patch("openai_tunnel_kit.systemd.run_systemctl")
    def test_force_warns_and_installs_despite_missing_credentials(self, systemctl, _write):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_profile("demo", root=root)
            warning = io.StringIO()
            with redirect_stderr(warning):
                install_service("demo", root=root, force=True)
        self.assertIn("WARNING: credential preflight failed", warning.getvalue())
        self.assertEqual(
            systemctl.call_args_list,
            [call(["daemon-reload"]), call(["enable", "--now", "tunnel-client@demo.service"])],
        )

    @patch("openai_tunnel_kit.systemd.shutil.which", return_value="/usr/bin/systemd-creds")
    @patch("openai_tunnel_kit.systemd.subprocess.run")
    def test_undecryptable_credential_is_reported(self, run, _which):
        run.return_value.returncode = 1
        run.return_value.stderr = "Failed to determine local credential key: Permission denied\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = initialize_profile("demo", root=root)
            paths.credential.parent.mkdir(parents=True)
            paths.credential.write_text("encrypted")
            problem = credential_problem("demo", root=root)
        self.assertIn("cannot be decrypted", problem)
        self.assertIn("Permission denied", problem)

    def test_unit_is_user_template_with_restart_and_environment_file(self):
        unit = render_systemd_unit(Path("/home/example/.config/openai-tunnel-kit"))
        self.assertIn("EnvironmentFile=/home/example/.config/openai-tunnel-kit/profiles/%i.env", unit)
        self.assertIn(
            "ExecStart=/usr/bin/env ${TUNNEL_CLIENT_BIN} run $TUNNEL_CLIENT_ARGS $TUNNEL_CLIENT_MCP_ARGS",
            unit,
        )
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_encrypted_unit_loads_credential_and_uses_launcher(self):
        unit = render_systemd_unit(
            Path("/home/example/.config/openai-tunnel-kit"),
            Path("/opt/bin/openai-tunnel-kit"),
            encrypted_credentials=True,
        )
        self.assertIn(
            "LoadCredentialEncrypted=CONTROL_PLANE_API_KEY:/home/example/.config/"
            "openai-tunnel-kit/credentials/%i.api-key.cred",
            unit,
        )
        self.assertIn("ExecStart=/opt/bin/openai-tunnel-kit _run-service %i", unit)

    @patch("openai_tunnel_kit.systemd.write_text_atomic")
    @patch("openai_tunnel_kit.systemd.run_systemctl")
    def test_install_reloads_enables_and_starts(self, systemctl, write):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_profile("demo", root=root, api_key="sk-test")
            install_service("demo", root=root)
        self.assertEqual(systemctl.call_args_list, [call(["daemon-reload"]), call(["enable", "--now", "tunnel-client@demo.service"])])
        self.assertEqual(write.call_args.kwargs["mode"], 0o644)

    def test_desktop_drop_in_passes_session_environment(self):
        drop_in = render_desktop_environment_drop_in()
        self.assertIn("PassEnvironment=DISPLAY WAYLAND_DISPLAY XAUTHORITY", drop_in)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", drop_in)
        self.assertIn("XDG_RUNTIME_DIR", drop_in)

    @patch("openai_tunnel_kit.systemd.write_text_atomic")
    @patch("openai_tunnel_kit.systemd.run_systemctl")
    def test_install_writes_instance_drop_in_when_enabled(self, systemctl, write):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_profile("desktop", root=root, api_key="sk-test", pass_desktop_environment=True)
            install_service("desktop", root=root)
        written_paths = [entry.args[0] for entry in write.call_args_list]
        self.assertTrue(
            any(str(path).endswith("tunnel-client@desktop.service.d/desktop-environment.conf") for path in written_paths)
        )
        self.assertEqual(
            systemctl.call_args_list,
            [call(["daemon-reload"]), call(["enable", "--now", "tunnel-client@desktop.service"])],
        )


if __name__ == "__main__":
    unittest.main()
