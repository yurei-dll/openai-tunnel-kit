"""Safe wrappers around tunnel-client control-plane operations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import ConfigError, read_environment, require_profile, set_tunnel_id
from .credentials import decrypt_api_key


def _binary(profile: str, runtime_api_key: Optional[str] = None) -> tuple[str, dict[str, str]]:
    paths = require_profile(profile)
    environment = read_environment(paths.env)
    binary = environment.get("TUNNEL_CLIENT_BIN", "tunnel-client")
    if not (os.access(binary, os.X_OK) if "/" in binary else shutil.which(binary)):
        raise ConfigError(f"tunnel-client executable was not found: {binary}")
    runtime_key = runtime_api_key or environment.get("CONTROL_PLANE_API_KEY", "")
    if paths.credential.is_file():
        if runtime_key:
            raise ConfigError("both plaintext and encrypted runtime API keys are configured")
        runtime_key = decrypt_api_key(paths.credential)
    if not runtime_key:
        raise ConfigError("profile has no runtime API key")
    environment["CONTROL_PLANE_API_KEY"] = runtime_key
    return binary, environment


def _run_json(
    profile: str,
    arguments: list[str],
    admin_key_file: Optional[Path] = None,
    runtime_api_key: Optional[str] = None,
) -> dict[str, Any]:
    binary, profile_environment = _binary(profile, runtime_api_key)
    command = [binary, "admin", "--json"]
    if admin_key_file is not None:
        try:
            admin_key = admin_key_file.expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"could not read admin key file {admin_key_file}: {exc}") from exc
        if not admin_key or any(character in admin_key for character in "\n\r\0"):
            raise ConfigError("admin key file must contain one non-empty key")
        profile_environment["OPENAI_ADMIN_KEY"] = admin_key
    result = subprocess.run(
        [*command, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **profile_environment},
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "tunnel-client failed"
        raise ConfigError(f"control-plane command failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigError("tunnel-client returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigError("tunnel-client returned an unexpected response")
    return payload


def inspect_tunnel(profile: str, runtime_api_key: Optional[str] = None) -> dict[str, Any]:
    environment = read_environment(require_profile(profile).env)
    tunnel_id = environment.get("CONTROL_PLANE_TUNNEL_ID", "")
    if not tunnel_id:
        raise ConfigError("profile has no tunnel ID")
    return _run_json(profile, ["tunnels", "get", tunnel_id], runtime_api_key=runtime_api_key)


def _scope_flags(name: str, values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = value.strip()
        if not value or any(character in value for character in "\n\r\0"):
            raise ConfigError(f"{name} values must be non-empty single-line identifiers")
        result.extend([f"--{name}", value])
    return result


def _verify_scopes(
    payload: dict[str, Any],
    organization_ids: tuple[str, ...],
    workspace_ids: tuple[str, ...],
) -> None:
    actual_organizations = set(payload.get("organization_ids") or [])
    actual_workspaces = set(payload.get("workspace_ids") or [])
    missing_organizations = set(organization_ids) - actual_organizations
    missing_workspaces = set(workspace_ids) - actual_workspaces
    if missing_organizations or missing_workspaces:
        details = []
        if missing_organizations:
            details.append("organizations=" + ",".join(sorted(missing_organizations)))
        if missing_workspaces:
            details.append("workspaces=" + ",".join(sorted(missing_workspaces)))
        raise ConfigError("control plane did not retain requested tunnel scopes: " + "; ".join(details))


def attach_tunnel(
    profile: str,
    admin_key_file: Path,
    organization_ids: Iterable[str] = (),
    workspace_ids: Iterable[str] = (),
    runtime_api_key: Optional[str] = None,
) -> dict[str, Any]:
    organizations = tuple(organization_ids)
    workspaces = tuple(workspace_ids)
    if not organizations and not workspaces:
        raise ConfigError("at least one --organization-id or --workspace-id is required")
    current = inspect_tunnel(profile, runtime_api_key)
    tunnel_id = str(current.get("id") or "")
    arguments = ["tunnels", "update", tunnel_id]
    arguments += _scope_flags("organization-id", organizations)
    arguments += _scope_flags("workspace-id", workspaces)
    _run_json(profile, arguments, admin_key_file, runtime_api_key)
    verified = inspect_tunnel(profile, runtime_api_key)
    _verify_scopes(verified, organizations, workspaces)
    return verified


def provision_tunnel(
    profile: str,
    admin_key_file: Path,
    name: str,
    description: str,
    organization_ids: Iterable[str] = (),
    workspace_ids: Iterable[str] = (),
    runtime_api_key: Optional[str] = None,
) -> dict[str, Any]:
    organizations = tuple(organization_ids)
    workspaces = tuple(workspace_ids)
    if not organizations and not workspaces:
        raise ConfigError("tunnel provisioning requires an organization or workspace ID")
    arguments = ["tunnels", "create", "--name", name, "--description", description]
    arguments += _scope_flags("organization-id", organizations)
    arguments += _scope_flags("workspace-id", workspaces)
    created = _run_json(profile, arguments, admin_key_file, runtime_api_key)
    tunnel_id = created.get("id")
    if not isinstance(tunnel_id, str) or not tunnel_id:
        raise ConfigError("control plane created a tunnel without returning its ID")
    set_tunnel_id(profile, tunnel_id)
    verified = inspect_tunnel(profile, runtime_api_key)
    _verify_scopes(verified, organizations, workspaces)
    return verified
