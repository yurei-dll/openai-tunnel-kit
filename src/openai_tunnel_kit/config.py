"""Profile and MCP configuration management."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .templates import render_mcp_example, render_profile


PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConfigError(RuntimeError):
    pass


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


def initialize_profile(
    profile: str,
    binary: str = "tunnel-client",
    arguments: Iterable[str] = (),
    mcp_source: Optional[Path] = None,
    force: bool = False,
    root: Optional[Path] = None,
) -> ProfilePaths:
    paths = profile_paths(profile, root)
    if paths.env.exists() and not force:
        raise ConfigError(f"profile already exists: {profile} (use --force to replace it)")
    if not binary or any(character.isspace() for character in binary):
        raise ConfigError("--binary must be a single executable name or path")
    arguments = tuple(arguments)
    if any("\n" in argument or "\r" in argument or "\0" in argument for argument in arguments):
        raise ConfigError("--arg values cannot contain newlines or NUL bytes")

    if mcp_source:
        mcp_text = normalize_mcp(load_mcp(mcp_source))
    elif paths.mcp.exists() and force:
        mcp_text = paths.mcp.read_text(encoding="utf-8")
    else:
        mcp_text = render_mcp_example()

    write_text_atomic(paths.mcp, mcp_text)
    write_text_atomic(paths.env, render_profile(binary, arguments))
    return paths


def register_mcp(profile: str, source: Path, root: Optional[Path] = None) -> Path:
    paths = require_profile(profile, root)
    write_text_atomic(paths.mcp, normalize_mcp(load_mcp(source)))
    return paths.mcp


def require_profile(profile: str, root: Optional[Path] = None) -> ProfilePaths:
    paths = profile_paths(profile, root)
    if not paths.env.is_file():
        raise ConfigError(f"profile not found: {profile}; run 'openai-tunnel-kit init {profile}'")
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
