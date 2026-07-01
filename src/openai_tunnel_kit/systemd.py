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
from .credentials import CREDENTIAL_NAME


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


def credential_problem(profile: str, root: Optional[Path] = None) -> Optional[str]:
    paths = require_profile(profile, root)
    environment = read_environment(paths.env)
    plaintext = bool(environment.get(CREDENTIAL_NAME, ""))
    encrypted = paths.credential.is_file()
    if plaintext and encrypted:
        return "both plaintext and encrypted API-key credentials are configured"
    if not plaintext and not encrypted:
        return "CONTROL_PLANE_API_KEY is missing"
    if not encrypted:
        return None
    executable = shutil.which("systemd-creds")
    if not executable:
        return "encrypted credential exists but systemd-creds is unavailable"
    result = subprocess.run(
        [
            executable, "decrypt", "--user", f"--name={CREDENTIAL_NAME}",
            str(paths.credential), "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        return f"encrypted credential cannot be decrypted: {detail[-1] if detail else 'systemd-creds failed'}"
    return None


def install_service(
    profile: str,
    start: bool = True,
    root: Optional[Path] = None,
    force: bool = False,
) -> Path:
    paths = require_profile(profile, root)
    problem = credential_problem(profile, root)
    if problem and not force:
        raise ConfigError(
            f"credential preflight failed: {problem}; repair the profile or rerun "
            f"'openai-tunnel-kit service install {profile} --force'"
        )
    if problem:
        print(f"WARNING: credential preflight failed: {problem}; continuing due to --force", file=sys.stderr)
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


@dataclass(frozen=True)
class ServiceState:
    enabled: bool
    running: bool
    active_state: str = "unknown"
    sub_state: str = "unknown"
    result: str = "unknown"
    exit_status: str = "unknown"
    restarts: str = "0"

    @property
    def detail(self) -> str:
        detail = f"{self.active_state}/{self.sub_state}"
        if self.result not in {"", "success", "unknown"}:
            detail += f", result={self.result}, exit={self.exit_status}"
        if self.restarts not in {"", "0"}:
            detail += f", restarts={self.restarts}"
        return detail


def inspect_service(profile: str) -> ServiceState:
    """Read exact unit state; 'activating/auto-restart' is not running."""
    if shutil.which("systemctl") is None:
        return ServiceState(False, False)
    result = subprocess.run(
        [
            "systemctl", "--user", "show", instance_name(profile),
            "--property=UnitFileState", "--property=ActiveState",
            "--property=SubState", "--property=Result",
            "--property=ExecMainStatus", "--property=NRestarts", "--no-pager",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    active_state = values.get("ActiveState", "unknown")
    sub_state = values.get("SubState", "unknown")
    return ServiceState(
        enabled=values.get("UnitFileState") in {"enabled", "enabled-runtime", "linked", "linked-runtime"},
        running=active_state == "active" and sub_state == "running",
        active_state=active_state,
        sub_state=sub_state,
        result=values.get("Result", "unknown"),
        exit_status=values.get("ExecMainStatus", "unknown"),
        restarts=values.get("NRestarts", "0"),
    )


def service_state(profile: str) -> tuple[bool, bool]:
    state = inspect_service(profile)
    return state.enabled, state.running


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
        checks.append(
            Check(
                False,
                "profiles",
                "none found; run 'openai-tunnel-kit profile init <profile>'",
            )
        )
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
            service = inspect_service(name)
            checks.append(
                Check(
                    service.enabled,
                    f"profile {name} service enabled",
                    service.detail if service.enabled else f"disabled ({service.detail}); run openai-tunnel-kit service install {name}",
                )
            )
            checks.append(
                Check(
                    service.running,
                    f"profile {name} service running",
                    service.detail if service.running else f"not running ({service.detail}); check openai-tunnel-kit service status {name}",
                )
            )
            checks.append(
                Check(
                    bool(api_key) or encrypted_api_key,
                    f"profile {name} API key",
                    "encrypted credential"
                    if encrypted_api_key
                    else ("configured" if api_key else "missing; use setup-env or profile init --api-key-file"),
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
