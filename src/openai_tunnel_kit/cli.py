"""Command-line interface for openai-tunnel-kit."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .config import (
    ConfigError, environment_flag, initialize_profile, list_profiles, load_mcp,
    mcp_launch, normalize_mcp, read_environment, register_mcp, remove_profile,
    require_profile,
)
from .credentials import CREDENTIAL_NAME, encrypt_api_key
from .systemd import (
    doctor, install_service, instance_name, service_state, start_service, status,
    stop_service, unit_path, uninstall_service,
)


FLOW = "Typical flow:  profile init  ->  service install  ->  doctor"


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def _add_init_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("profile")
    command.add_argument("--binary", default="tunnel-client", help="external tunnel-client executable")
    command.add_argument("--arg", action="append", default=[], help="argument passed to tunnel-client (repeatable)")
    command.add_argument("--tunnel-id", default="", metavar="ID", help="OpenAI tunnel ID")
    key = command.add_mutually_exclusive_group()
    key.add_argument("--api-key", default="", metavar="KEY", help="runtime API key (prefer --api-key-file)")
    key.add_argument("--api-key-file", type=Path, metavar="FILE", help="read the runtime API key from a file")
    command.add_argument("--mcp-file", type=Path, help="existing MCP JSON to copy into the profile")
    command.add_argument("--pass-desktop-environment", action="store_true", help="pass desktop session variables through systemd")
    command.add_argument("--force", action="store_true", help="replace an existing profile")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="openai-tunnel-kit",
        description="Manage reproducible tunnel-client profiles",
        epilog=FLOW + "\n\nInspect any setup with:  openai-tunnel-kit explain <profile>",
        formatter_class=HelpFormatter,
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = result.add_subparsers(dest="command", required=True, metavar="LAYER")

    profiles = commands.add_parser("profile", help="create, inspect, list, and remove profiles")
    profile_commands = profiles.add_subparsers(dest="profile_command", required=True)
    init = profile_commands.add_parser("init", help="create a profile and MCP config")
    _add_init_arguments(init)
    profile_commands.add_parser("list", help="list configured profiles")
    show = profile_commands.add_parser("show", help="show a profile's paths and settings")
    show.add_argument("profile")
    remove = profile_commands.add_parser("remove", help="disable its service and delete a profile")
    remove.add_argument("profile")

    services = commands.add_parser("service", help="install and control systemd services")
    service_commands = services.add_subparsers(dest="service_command", required=True)
    install = service_commands.add_parser("install", help="install, enable, and start a service")
    install.add_argument("profile")
    install.add_argument("--no-start", action="store_true", help="enable without starting now")
    install.add_argument("--force", action="store_true", help="install despite credential preflight failures")
    for name, help_text in (("start", "start a service"), ("stop", "stop a service"), ("status", "show service status")):
        item = service_commands.add_parser(name, help=help_text)
        item.add_argument("profile")
    uninstall = service_commands.add_parser("uninstall", help="disable and stop a service")
    uninstall.add_argument("profile")
    uninstall.add_argument("--purge", action="store_true", help="also delete profile and MCP files")

    mcps = commands.add_parser("mcp", help="register and inspect MCP servers")
    mcp_commands = mcps.add_subparsers(dest="mcp_command", required=True)
    add = mcp_commands.add_parser("add", help="validate and register MCP JSON")
    add.add_argument("profile")
    add.add_argument("file", type=Path)
    for name, help_text in (("list", "list attached MCP servers"), ("print", "print normalized MCP JSON")):
        item = mcp_commands.add_parser(name, help=help_text)
        item.add_argument("profile")

    diagnose = commands.add_parser("doctor", help="diagnose local setup problems")
    diagnose.add_argument("profile", nargs="?", help="check one profile (default: all)")
    explain = commands.add_parser("explain", help="explain what a profile actually runs")
    explain.add_argument("profile")
    setup = commands.add_parser("setup-env", help="create and start a profile from exported variables")
    setup.add_argument("--force", action="store_true")
    internal = commands.add_parser("_run-service", help=argparse.SUPPRESS)
    commands._choices_actions.pop()
    internal.add_argument("profile")

    # Quiet compatibility aliases for scripts written against 0.1.0.
    legacy_init = commands.add_parser("init", help=argparse.SUPPRESS)
    commands._choices_actions.pop()
    _add_init_arguments(legacy_init)
    for old, profile_arg in (("install-service", True), ("status", True), ("print-mcp", True), ("list-profiles", False), ("remove-profile", True), ("uninstall", True)):
        item = commands.add_parser(old, help=argparse.SUPPRESS)
        commands._choices_actions.pop()
        if profile_arg:
            item.add_argument("profile")
        if old == "install-service":
            item.add_argument("--no-start", action="store_true")
            item.add_argument("--force", action="store_true")
        if old == "uninstall": item.add_argument("--purge", action="store_true")
    old_set = commands.add_parser("set-mcp", help=argparse.SUPPRESS)
    commands._choices_actions.pop()
    old_set.add_argument("profile"); old_set.add_argument("file", type=Path)
    return result


def _initialize(args: argparse.Namespace) -> int:
    api_key = args.api_key
    if args.api_key_file:
        api_key = args.api_key_file.read_text(encoding="utf-8").strip()
        if not api_key:
            raise ConfigError(f"API key file is empty: {args.api_key_file}")
    paths = initialize_profile(args.profile, args.binary, args.arg, args.mcp_file, args.force,
        tunnel_id=args.tunnel_id, api_key=api_key,
        pass_desktop_environment=args.pass_desktop_environment)
    print(f"Created profile: {paths.env}\nMCP config:      {paths.mcp}")
    if not args.tunnel_id: print("Action required: set CONTROL_PLANE_TUNNEL_ID in the profile or rerun with --tunnel-id")
    if not api_key: print("Action required: set CONTROL_PLANE_API_KEY in the profile or rerun with --api-key-file")
    if not args.mcp_file: print(f"Action required: register MCP with 'openai-tunnel-kit mcp add {args.profile} FILE'")
    print(f"Next: openai-tunnel-kit service install {args.profile}")
    return 0


def _profile_summary(profile: str) -> int:
    paths = require_profile(profile)
    environment = read_environment(paths.env)
    print(f"Profile:    {profile}\nEnvironment: {paths.env}\nMCP config:  {paths.mcp}")
    print(f"Credential:  {paths.credential if paths.credential.is_file() else 'plaintext profile value or not configured'}")
    print(f"Binary:      {environment.get('TUNNEL_CLIENT_BIN', 'tunnel-client')}")
    return 0


def _explain(profile: str) -> int:
    paths = require_profile(profile)
    environment = read_environment(paths.env)
    content = load_mcp(paths.mcp)
    launch = mcp_launch(content)
    servers = content["mcpServers"]
    enabled, active = service_state(profile)
    tunnel_command = [environment.get("TUNNEL_CLIENT_BIN", "tunnel-client"), "run"]
    try:
        tunnel_command += shlex.split(environment.get("TUNNEL_CLIENT_ARGS", ""))
        tunnel_command += shlex.split(environment.get("TUNNEL_CLIENT_MCP_ARGS", ""))
    except ValueError as exc:
        raise ConfigError(f"invalid profile arguments: {exc}") from exc
    installed = unit_path().is_file()
    systemd_exec = (
        f"{Path(sys.argv[0]).resolve()} _run-service {profile}"
        if paths.credential.is_file()
        else "/usr/bin/env ${TUNNEL_CLIENT_BIN} run "
        "$TUNNEL_CLIENT_ARGS $TUNNEL_CLIENT_MCP_ARGS"
    )

    print(f"Profile: {profile}")
    print("\nService")
    print(f"  Name:      {instance_name(profile)}")
    print(f"  Installed: {'yes' if installed else 'no'} ({unit_path()})")
    print(f"  Enabled:   {'yes' if enabled else 'no'}")
    print(f"  Running:   {'yes' if active else 'no'}")
    print(f"  ExecStart: {systemd_exec}")
    print(f"  Launches:  {shlex.join(tunnel_command)}")
    print("\nMCP servers")
    for name, server in servers.items():
        target = server.get("url") or shlex.join([server.get("command", ""), *server.get("args", [])])
        print(f"  {name}: {target}")
    print("\nEnvironment")
    print(f"  File: {paths.env}")
    for key in sorted(environment):
        value = environment[key]
        if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            value = "<set, redacted>" if value else "<not set>"
        print(f"  {key}={value}")
    if paths.credential.is_file():
        print(f"  CONTROL_PLANE_API_KEY=<encrypted credential: {paths.credential}>")
    return 0


def run(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    command = args.command
    if command == "init" or (command == "profile" and args.profile_command == "init"): return _initialize(args)
    if command == "profile":
        if args.profile_command == "list": command = "list-profiles"
        elif args.profile_command == "show": return _profile_summary(args.profile)
        elif args.profile_command == "remove": command = "remove-profile"
    if command == "service": command = args.service_command
    if command == "mcp": command = {"add": "set-mcp", "print": "print-mcp", "list": "mcp-list"}[args.mcp_command]
    if command in {"install", "install-service"}:
        path = install_service(args.profile, not args.no_start, force=args.force)
        action = "enabled" if args.no_start else "enabled and started"
        print(f"Installed {path}; {instance_name(args.profile)} is {action}"); return 0
    if command == "setup-env":
        profile = os.environ.get("OPENAI_TUNNEL_PROFILE", "default")
        tunnel_id, api_key = os.environ.get("CONTROL_PLANE_TUNNEL_ID", ""), os.environ.get("CONTROL_PLANE_API_KEY", "")
        api_key_file, mcp_file = os.environ.get("CONTROL_PLANE_API_KEY_FILE", ""), os.environ.get("OPENAI_TUNNEL_MCP_FILE", "")
        if api_key and api_key_file: raise ConfigError("set only one of CONTROL_PLANE_API_KEY or CONTROL_PLANE_API_KEY_FILE")
        if api_key_file: api_key = Path(api_key_file).expanduser().read_text(encoding="utf-8").strip()
        if not tunnel_id: raise ConfigError("CONTROL_PLANE_TUNNEL_ID is required")
        if not api_key: raise ConfigError("CONTROL_PLANE_API_KEY or CONTROL_PLANE_API_KEY_FILE is required")
        if not mcp_file: raise ConfigError("OPENAI_TUNNEL_MCP_FILE is required")
        try: tunnel_args = shlex.split(os.environ.get("TUNNEL_CLIENT_ARGS", ""))
        except ValueError as exc: raise ConfigError(f"invalid TUNNEL_CLIENT_ARGS: {exc}") from exc
        paths = initialize_profile(profile, os.environ.get("TUNNEL_CLIENT_BIN", "tunnel-client"), tunnel_args,
            Path(mcp_file).expanduser(), args.force, tunnel_id=tunnel_id, api_key="",
            pass_desktop_environment=environment_flag("OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT"))
        encrypt_api_key(api_key, paths.credential); install_service(profile, start=True)
        print(f"Configured {profile} with an encrypted API key\nEnabled and started {instance_name(profile)}"); return 0
    if command == "_run-service":
        paths = require_profile(args.profile); environment = read_environment(paths.env)
        if paths.credential.is_file():
            directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
            if not directory: raise ConfigError("systemd did not provide CREDENTIALS_DIRECTORY")
            environment[CREDENTIAL_NAME] = (Path(directory) / CREDENTIAL_NAME).read_text(encoding="utf-8").strip()
        try: executable = [environment.get("TUNNEL_CLIENT_BIN", "tunnel-client"), "run", *shlex.split(environment.get("TUNNEL_CLIENT_ARGS", "")), *shlex.split(environment.get("TUNNEL_CLIENT_MCP_ARGS", ""))]
        except ValueError as exc: raise ConfigError(f"invalid profile arguments: {exc}") from exc
        os.execvpe(executable[0], executable, {**os.environ, **environment}); raise AssertionError("os.execvpe returned")
    if command == "start": start_service(args.profile); print(f"Started {instance_name(args.profile)}"); return 0
    if command == "stop": stop_service(args.profile); print(f"Stopped {instance_name(args.profile)}"); return 0
    if command == "status": require_profile(args.profile); return status(args.profile)
    if command == "print-mcp": print(normalize_mcp(load_mcp(require_profile(args.profile).mcp)), end=""); return 0
    if command == "mcp-list":
        servers = load_mcp(require_profile(args.profile).mcp).get("mcpServers", {})
        print("\n".join(sorted(servers)) if servers else "No MCP servers attached."); return 0
    if command == "set-mcp": print(f"Registered MCP config: {register_mcp(args.profile, args.file)}"); return 0
    if command == "doctor":
        checks = doctor(args.profile); [print(f"{'OK' if c.ok else 'FAIL':4} {c.label}: {c.detail}") for c in checks]
        failed = sum(not c.ok for c in checks); print(f"\n{len(checks)-failed} passed, {failed} failed"); return 1 if failed else 0
    if command == "explain": return _explain(args.profile)
    if command == "list-profiles":
        profiles = list_profiles(); print("\n".join(profiles) if profiles else "No profiles configured."); return 0
    if command == "remove-profile":
        require_profile(args.profile); uninstall_service(args.profile); remove_profile(args.profile)
        print(f"Disabled service and deleted profile {args.profile}"); return 0
    if command in {"uninstall", "service-uninstall"}:
        paths = require_profile(args.profile); uninstall_service(args.profile)
        if args.purge: remove_profile(args.profile); print(f"Disabled service and deleted profile {args.profile}")
        else: print(f"Disabled service; profile retained at {paths.env}")
        return 0
    raise AssertionError(f"unhandled command: {command}")


def main(arguments: Optional[Sequence[str]] = None) -> int:
    try: return run(arguments)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
