"""Command-line interface for openai-tunnel-kit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .config import (
    ConfigError,
    initialize_profile,
    list_profiles,
    load_mcp,
    normalize_mcp,
    register_mcp,
    remove_profile,
    require_profile,
)
from .systemd import doctor, install_service, status, uninstall_service


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="openai-tunnel-kit", description="Manage reproducible tunnel-client profiles")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = result.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a profile and MCP config")
    init.add_argument("profile")
    init.add_argument("--binary", default="tunnel-client", help="external tunnel-client executable")
    init.add_argument("--arg", action="append", default=[], help="argument passed to tunnel-client (repeatable)")
    init.add_argument("--tunnel-id", default="", metavar="ID", help="OpenAI tunnel ID (CONTROL_PLANE_TUNNEL_ID)")
    key = init.add_mutually_exclusive_group()
    key.add_argument(
        "--api-key",
        default="",
        metavar="KEY",
        help="runtime API key (visible in shell history; prefer --api-key-file)",
    )
    key.add_argument(
        "--api-key-file",
        type=Path,
        metavar="FILE",
        help="read the runtime API key from a file",
    )
    init.add_argument("--mcp-file", type=Path, help="existing MCP JSON to copy into the profile")
    init.add_argument("--force", action="store_true", help="replace an existing profile")

    install = commands.add_parser("install-service", help="install, enable, and start a profile service")
    install.add_argument("profile")
    install.add_argument("--no-start", action="store_true", help="enable without starting now")

    state = commands.add_parser("status", help="show systemd status for a profile")
    state.add_argument("profile")

    output = commands.add_parser("print-mcp", help="print normalized MCP JSON")
    output.add_argument("profile")

    set_mcp = commands.add_parser("set-mcp", help="validate and register an MCP JSON blob")
    set_mcp.add_argument("profile")
    set_mcp.add_argument("file", type=Path)

    diagnose = commands.add_parser("doctor", help="diagnose local setup problems")
    diagnose.add_argument("profile", nargs="?", help="check one profile (default: all)")

    commands.add_parser("list-profiles", help="list configured profile names")

    remove_profile_command = commands.add_parser(
        "remove-profile",
        help="disable its service and delete a profile",
    )
    remove_profile_command.add_argument("profile")

    remove = commands.add_parser("uninstall", help="disable a profile service")
    remove.add_argument("profile")
    remove.add_argument("--purge", action="store_true", help="also delete the profile and MCP files")
    return result


def run(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    if args.command == "init":
        api_key = args.api_key
        if args.api_key_file:
            api_key = args.api_key_file.read_text(encoding="utf-8").strip()
            if not api_key:
                raise ConfigError(f"API key file is empty: {args.api_key_file}")
        paths = initialize_profile(
            args.profile,
            args.binary,
            args.arg,
            args.mcp_file,
            args.force,
            tunnel_id=args.tunnel_id,
            api_key=api_key,
        )
        print(f"Created profile: {paths.env}")
        print(f"MCP config:      {paths.mcp}")
        if not args.tunnel_id:
            print("Action required: set CONTROL_PLANE_TUNNEL_ID in the profile or rerun with --tunnel-id")
        if not api_key:
            print("Action required: set CONTROL_PLANE_API_KEY in the profile or rerun with --api-key-file")
        if not args.mcp_file:
            print("Action required: register the MCP server with 'openai-tunnel-kit set-mcp " f"{args.profile} FILE'")
        print(f"Next: openai-tunnel-kit install-service {args.profile}")
        return 0
    if args.command == "install-service":
        path = install_service(args.profile, not args.no_start)
        action = "enabled" if args.no_start else "enabled and started"
        print(f"Installed {path}; tunnel-client@{args.profile}.service is {action}")
        return 0
    if args.command == "status":
        require_profile(args.profile)
        return status(args.profile)
    if args.command == "print-mcp":
        paths = require_profile(args.profile)
        print(normalize_mcp(load_mcp(paths.mcp)), end="")
        return 0
    if args.command == "set-mcp":
        destination = register_mcp(args.profile, args.file)
        print(f"Registered MCP config: {destination}")
        return 0
    if args.command == "doctor":
        checks = doctor(args.profile)
        for check in checks:
            print(f"{'OK' if check.ok else 'FAIL':4} {check.label}: {check.detail}")
        failed = sum(not check.ok for check in checks)
        print(f"\n{len(checks) - failed} passed, {failed} failed")
        return 1 if failed else 0
    if args.command == "list-profiles":
        profiles = list_profiles()
        if profiles:
            print("\n".join(profiles))
        else:
            print("No profiles configured.")
        return 0
    if args.command == "remove-profile":
        require_profile(args.profile)
        uninstall_service(args.profile)
        remove_profile(args.profile)
        print(f"Disabled service and deleted profile {args.profile}")
        return 0
    if args.command == "uninstall":
        paths = require_profile(args.profile)
        uninstall_service(args.profile)
        if args.purge:
            remove_profile(args.profile)
            print(f"Disabled service and deleted profile {args.profile}")
        else:
            print(f"Disabled service; profile retained at {paths.env}")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def main(arguments: Optional[Sequence[str]] = None) -> int:
    try:
        return run(arguments)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
