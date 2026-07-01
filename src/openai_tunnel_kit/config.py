"""Profile and MCP configuration management."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .templates import render_mcp_example, render_profile


PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MANAGED_ENVIRONMENT_KEYS = {
    "CONTROL_PLANE_TUNNEL_ID",
    "CONTROL_PLANE_API_KEY",
    "TUNNEL_CLIENT_BIN",
    "TUNNEL_CLIENT_ARGS",
    "TUNNEL_CLIENT_MCP_ARGS",
    "OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT",
}


def environment_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpLaunch:
    arguments: tuple[str, ...]
    environment: dict[str, str]


def config_dir() -> Path:
    override = os.environ.get("OPENAI_TUNNEL_KIT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "openai-tunnel-kit"


def validate_profile_name(profile: str) -> str:
    if not PROFILE_PATTERN.fullmatch(profile):
        raise ConfigError(
            "profile must start with a letter or digit and contain only "
            "letters, digits, '.', '_' or '-'"
        )
    return profile


@dataclass(frozen=True)
class ProfilePaths:
    name: str
    root: Path

    @property
    def env(self) -> Path:
        return self.root / "profiles" / f"{self.name}.env"

    @property
    def mcp(self) -> Path:
        return self.root / "profiles" / f"{self.name}.mcp.json"

    @property
    def credential(self) -> Path:
        return self.root / "credentials" / f"{self.name}.api-key.cred"


def profile_paths(profile: str, root: Optional[Path] = None) -> ProfilePaths:
    return ProfilePaths(validate_profile_name(profile), root or config_dir())


def write_text_atomic(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_mcp(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"MCP config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def normalize_mcp(content: Any) -> str:
    if not isinstance(content, dict):
        raise ConfigError("MCP config must be a JSON object")
    return json.dumps(content, indent=2, sort_keys=True) + "\n"


def mcp_launch(content: Any) -> McpLaunch:
    if not isinstance(content, dict) or not isinstance(content.get("mcpServers"), dict):
        raise ConfigError("MCP config must contain an 'mcpServers' object")
    servers = content["mcpServers"]
    if len(servers) != 1:
        raise ConfigError("tunnel profiles require exactly one MCP server bound to channel 'main'")
    name, server = next(iter(servers.items()))
    if not isinstance(server, dict):
        raise ConfigError(f"MCP server {name!r} must be a JSON object")

    environment = server.get("env", {})
    if not isinstance(environment, dict) or any(
        not isinstance(key, str)
        or not ENVIRONMENT_KEY_PATTERN.fullmatch(key)
        or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ConfigError(f"MCP server {name!r} has invalid environment variables")
    if MANAGED_ENVIRONMENT_KEYS.intersection(environment):
        raise ConfigError(f"MCP server {name!r} cannot override toolkit-managed environment variables")

    command = server.get("command")
    url = server.get("url")
    if isinstance(command, str) and command:
        raw_args = server.get("args", [])
        if not isinstance(raw_args, list) or any(not isinstance(value, str) for value in raw_args):
            raise ConfigError(f"MCP server {name!r} args must be an array of strings")
        command_line = shlex.join((command, *raw_args))
        if "," in command_line:
            raise ConfigError("MCP command paths and arguments cannot contain commas")
        arguments = ("--mcp.command", f"command={command_line},channel=main")
    elif isinstance(url, str) and url:
        if "," in url:
            raise ConfigError("MCP server URLs cannot contain commas")
        arguments = ("--mcp.server-url", f"url={url},channel=main")
    else:
        raise ConfigError(f"MCP server {name!r} needs either 'command' or 'url'")
    return McpLaunch(arguments, dict(environment))


def initialize_profile(
    profile: str,
    binary: str = "tunnel-client",
    arguments: Iterable[str] = (),
    mcp_source: Optional[Path] = None,
    force: bool = False,
    root: Optional[Path] = None,
    tunnel_id: str = "",
    api_key: str = "",
    pass_desktop_environment: bool = False,
) -> ProfilePaths:
    paths = profile_paths(profile, root)
    if paths.env.exists() and not force:
        raise ConfigError(f"profile already exists: {profile} (use --force to replace it)")
    if not binary or any(character.isspace() for character in binary):
        raise ConfigError("--binary must be a single executable name or path")
    arguments = tuple(arguments)
    if any("\n" in argument or "\r" in argument or "\0" in argument for argument in arguments):
        raise ConfigError("--arg values cannot contain newlines or NUL bytes")
    if any(character in tunnel_id for character in "\n\r\0"):
        raise ConfigError("--tunnel-id cannot contain newlines or NUL bytes")
    if any(character in api_key for character in "\n\r\0"):
        raise ConfigError("--api-key cannot contain newlines or NUL bytes")

    launch = McpLaunch((), {})
    if mcp_source:
        mcp_content = load_mcp(mcp_source)
        launch = mcp_launch(mcp_content)
        mcp_text = normalize_mcp(mcp_content)
    elif paths.mcp.exists() and force:
        mcp_content = load_mcp(paths.mcp)
        launch = mcp_launch(mcp_content)
        mcp_text = normalize_mcp(mcp_content)
    else:
        mcp_text = render_mcp_example()

    write_text_atomic(paths.mcp, mcp_text)
    write_text_atomic(
        paths.env,
        render_profile(
            binary,
            arguments,
            tunnel_id,
            api_key,
            launch.arguments,
            launch.environment,
            pass_desktop_environment,
        ),
    )
    if api_key:
        # An explicitly supplied plaintext key switches the profile back to the
        # legacy EnvironmentFile path instead of silently retaining an older
        # encrypted credential.
        paths.credential.unlink(missing_ok=True)
    return paths


def register_mcp(profile: str, source: Path, root: Optional[Path] = None) -> Path:
    paths = require_profile(profile, root)
    content = load_mcp(source)
    launch = mcp_launch(content)
    environment = read_environment(paths.env)
    old_mcp_environment: set[str] = set()
    try:
        old_mcp_environment = set(mcp_launch(load_mcp(paths.mcp)).environment)
    except ConfigError:
        pass
    extra_environment = {
        key: value
        for key, value in environment.items()
        if key not in MANAGED_ENVIRONMENT_KEYS and key not in old_mcp_environment
    }
    extra_environment.update(launch.environment)
    try:
        arguments = shlex.split(environment.get("TUNNEL_CLIENT_ARGS", ""))
    except ValueError as exc:
        raise ConfigError(f"invalid TUNNEL_CLIENT_ARGS in {paths.env}: {exc}") from exc
    write_text_atomic(paths.mcp, normalize_mcp(content))
    write_text_atomic(
        paths.env,
        render_profile(
            environment.get("TUNNEL_CLIENT_BIN", "tunnel-client"),
            arguments,
            environment.get("CONTROL_PLANE_TUNNEL_ID", ""),
            environment.get("CONTROL_PLANE_API_KEY", ""),
            launch.arguments,
            extra_environment,
            environment.get("OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT", "").lower()
            in {"1", "true", "yes"},
        ),
    )
    return paths.mcp


def require_profile(profile: str, root: Optional[Path] = None) -> ProfilePaths:
    paths = profile_paths(profile, root)
    if not paths.env.is_file():
        raise ConfigError(f"profile not found: {profile}; run 'openai-tunnel-kit init {profile}'")
    return paths


def list_profiles(root: Optional[Path] = None) -> list[str]:
    profiles_dir = (root or config_dir()) / "profiles"
    return sorted(path.stem for path in profiles_dir.glob("*.env") if path.is_file())


def remove_profile(profile: str, root: Optional[Path] = None) -> ProfilePaths:
    paths = require_profile(profile, root)
    paths.env.unlink()
    paths.mcp.unlink(missing_ok=True)
    paths.credential.unlink(missing_ok=True)
    return paths


def read_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or not key:
            raise ConfigError(f"invalid EnvironmentFile line {line_number} in {path}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key] = value
    return values
