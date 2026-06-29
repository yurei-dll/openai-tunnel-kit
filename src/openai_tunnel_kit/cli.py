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
    load_mcp,
    normalize_mcp,
    register_mcp,
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

    remove = commands.add_parser("uninstall", help="disable a profile service")
    remove.add_argument("profile")
    remove.add_argument("--purge", action="store_true", help="also delete the profile and MCP files")
    return result


def run(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    if args.command == "init":
        paths = initialize_profile(args.profile, args.binary, args.arg, args.mcp_file, args.force)
        print(f"Created profile: {paths.env}")
        print(f"MCP config:      {paths.mcp}")
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
    if args.command == "uninstall":
        paths = require_profile(args.profile)
        uninstall_service(args.profile)
        if args.purge:
            paths.env.unlink(missing_ok=True)
            paths.mcp.unlink(missing_ok=True)
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
