"""Global user-wallet storage for the wizard's OpenAI admin credential."""

from __future__ import annotations

from typing import Any

from .config import ConfigError


SERVICE_NAME = "openai-tunnel-kit"
ACCOUNT_NAME = "openai-organization-admin-key"


def _keyring() -> Any:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise ConfigError(
            "system-wallet support is missing; reinstall with 'python -m pip install -e .[wizard]'"
        ) from exc

    backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) <= 0:
        raise ConfigError(
            "no usable system credential wallet is available; unlock or configure "
            "GNOME Keyring, Secret Service, or KWallet, then retry"
        )
    return keyring, KeyringError


def load_admin_key() -> str:
    """Load the single global admin key for the current OS user."""
    keyring, keyring_error = _keyring()
    try:
        value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except keyring_error as exc:
        raise ConfigError(f"could not read the admin key from the system wallet: {exc}") from exc
    if not value:
        raise ConfigError("no admin key is saved in the system wallet")
    if any(character in value for character in "\n\r\0"):
        raise ConfigError("the saved admin key is not a valid single-line credential")
    return value


def save_admin_key(value: str) -> None:
    """Save the global admin key after the caller has validated it with OpenAI."""
    if not value or any(character in value for character in "\n\r\0"):
        raise ConfigError("admin API key must be one non-empty line")
    keyring, keyring_error = _keyring()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, value)
    except keyring_error as exc:
        raise ConfigError(f"could not save the admin key in the system wallet: {exc}") from exc


def forget_admin_key() -> bool:
    """Remove the global admin key, returning whether one existed."""
    keyring, keyring_error = _keyring()
    try:
        if keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is None:
            return False
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except keyring_error as exc:
        raise ConfigError(f"could not remove the admin key from the system wallet: {exc}") from exc
    return True
