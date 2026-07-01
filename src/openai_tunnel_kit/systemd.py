"""Generate and manage a systemd user service."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .config import ConfigError, config_dir, read_environment, require_profile, write_text_atomic
from .templates import render_desktop_environment_drop_in, render_systemd_unit


UNIT_NAME = "tunnel-client@.service"


def user_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def unit_path() -> Path:
    return user_unit_dir() / UNIT_NAME


def instance_name(profile: str) -> str:
    return f"tunnel-client@{profile}.service"


def instance_drop_in_dir(profile: str) -> Path:
    return user_unit_dir() / f"{instance_name(profile)}.d"


def desktop_drop_in_path(profile: str) -> Path:
    return instance_drop_in_dir(profile) / "desktop-environment.conf"


def remove_desktop_drop_in(profile: str) -> None:
    path = desktop_drop_in_path(profile)
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def run_systemctl(arguments: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    if shutil.which("systemctl") is None:
        raise ConfigError("systemctl was not found; systemd user services are required")
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            check=check,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        command = " ".join(exc.cmd)
        raise ConfigError(f"command failed ({exc.returncode}): {command}") from exc


def install_service(profile: str, start: bool = True, root: Optional[Path] = None) -> Path:
    paths = require_profile(profile, root)
    if shutil.which("systemctl") is None:
        raise ConfigError("systemctl was not found; systemd user services are required")
    destination = unit_path()
    write_text_atomic(
        destination,
        render_systemd_unit(
            root or config_dir(),
            Path(sys.argv[0]).resolve(),
            paths.credential.is_file(),
        ),
        mode=0o644,
    )
    environment = read_environment(paths.env)
    pass_desktop_environment = environment.get(
        "OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT", ""
    ).lower() in {"1", "true", "yes"}
    if pass_desktop_environment:
        write_text_atomic(
            desktop_drop_in_path(profile),
            render_desktop_environment_drop_in(),
            mode=0o644,
        )
    else:
        remove_desktop_drop_in(profile)
    run_systemctl(["daemon-reload"])
    args = ["enable"]
    if start:
        args.append("--now")
    args.append(instance_name(profile))
    run_systemctl(args)
    return destination


def status(profile: str) -> int:
    result = run_systemctl(["status", instance_name(profile)], check=False)
    return result.returncode


def start_service(profile: str) -> None:
    require_profile(profile)
    run_systemctl(["start", instance_name(profile)])


def stop_service(profile: str) -> None:
    require_profile(profile)
    run_systemctl(["stop", instance_name(profile)])


def service_state(profile: str) -> tuple[bool, bool]:
    """Return (enabled, active) without printing systemctl's answers."""
    if shutil.which("systemctl") is None:
        return False, False

    def probe(operation: str) -> bool:
        result = subprocess.run(
            ["systemctl", "--user", operation, instance_name(profile)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0

    return probe("is-enabled"), probe("is-active")


def uninstall_service(profile: str) -> None:
    if not unit_path().is_file():
        remove_desktop_drop_in(profile)
        return
    run_systemctl(["disable", "--now", instance_name(profile)])
    remove_desktop_drop_in(profile)
    run_systemctl(["daemon-reload"])


@dataclass(frozen=True)
class Check:
    ok: bool
    label: str
    detail: str


def doctor(profile: Optional[str] = None, root: Optional[Path] = None) -> list[Check]:
    root = root or config_dir()
    systemctl = shutil.which("systemctl")
    checks = [
        Check(systemctl is not None, "systemctl", systemctl or "not found"),
        Check(Path("/usr/bin/env").is_file(), "env launcher", "/usr/bin/env"),
        Check(unit_path().is_file(), "service template", str(unit_path())),
    ]
    manager = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) if systemctl else None
    manager_detail = manager.stdout.strip() if manager else "not checked"
    manager_ok = manager is not None and manager.returncode in (0, 1) and "failed to connect" not in manager_detail.lower()
    checks.append(Check(manager_ok, "user manager", manager_detail))

    profiles = [profile] if profile else sorted(path.stem for path in (root / "profiles").glob("*.env"))
    if not profiles:
        checks.append(Check(False, "profiles", "none found; run 'openai-tunnel-kit init <profile>'"))
        return checks

    from .config import load_mcp, mcp_launch, profile_paths, read_environment

    for name in profiles:
        paths = profile_paths(name, root)
        if not paths.env.is_file():
            checks.append(Check(False, f"profile {name}", f"missing {paths.env}"))
            continue
        try:
            environment = read_environment(paths.env)
            tunnel_id = environment.get("CONTROL_PLANE_TUNNEL_ID", "")
            api_key = environment.get("CONTROL_PLANE_API_KEY", "")
            encrypted_api_key = paths.credential.is_file()
            checks.append(
                Check(
                    bool(tunnel_id),
                    f"profile {name} tunnel ID",
                    "configured" if tunnel_id else "missing; use init --tunnel-id or edit the profile",
                )
            )
            checks.append(
                Check(
                    bool(api_key) or encrypted_api_key,
                    f"profile {name} API key",
                    "encrypted credential"
                    if encrypted_api_key
                    else ("configured" if api_key else "missing; use setup-env or init --api-key-file"),
                )
            )
            binary = environment.get("TUNNEL_CLIENT_BIN", "")
            binary_found = bool(
                binary
                and (os.access(binary, os.X_OK) if "/" in binary else shutil.which(binary))
            )
            checks.append(Check(binary_found, f"profile {name} binary", binary or "TUNNEL_CLIENT_BIN is unset"))
            launch = mcp_launch(load_mcp(paths.mcp))
            checks.append(Check(True, f"profile {name} MCP", str(paths.mcp)))
            checks.append(
                Check(
                    bool(environment.get("TUNNEL_CLIENT_MCP_ARGS")),
                    f"profile {name} MCP launch",
                    " ".join(launch.arguments),
                )
            )
        except (ConfigError, OSError) as exc:
            checks.append(Check(False, f"profile {name} config", str(exc)))
    return checks
