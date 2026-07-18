"""Non-secret, user-global preferences for the browser wizard."""

from __future__ import annotations

import json

from .config import ConfigError, config_dir, write_text_atomic


def _path():
    return config_dir() / "wizard-preferences.json"


def load_preferences() -> dict[str, str]:
    path = _path()
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read wizard preferences {path}: {exc}") from exc
    if not isinstance(content, dict):
        raise ConfigError(f"wizard preferences must be a JSON object: {path}")
    organization_id = content.get("last_organization_id")
    return {
        "last_organization_id": organization_id
    } if isinstance(organization_id, str) and organization_id.startswith("org_") else {}


def save_last_organization_id(organization_id: str) -> None:
    if not organization_id.startswith("org_") or any(
        character in organization_id for character in "\n\r\0"
    ):
        raise ConfigError("organization ID must start with org_ and be one line")
    write_text_atomic(
        _path(),
        json.dumps({"last_organization_id": organization_id}, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
