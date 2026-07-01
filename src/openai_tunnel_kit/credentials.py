"""Encrypted credential storage backed by systemd-creds."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import ConfigError, write_text_atomic


CREDENTIAL_NAME = "CONTROL_PLANE_API_KEY"


def encrypt_api_key(api_key: str, destination: Path) -> Path:
    executable = shutil.which("systemd-creds")
    if not executable:
        raise ConfigError(
            "systemd-creds is required for encrypted API-key storage; "
            "install a recent systemd release"
        )
    if not api_key or any(character in api_key for character in "\n\r\0"):
        raise ConfigError("CONTROL_PLANE_API_KEY must be a non-empty single-line value")
    result = subprocess.run(
        [
            executable,
            "encrypt",
            "--user",
            f"--name={CREDENTIAL_NAME}",
            "-",
            "-",
        ],
        input=api_key,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "systemd-creds failed"
        raise ConfigError(f"could not encrypt API key: {detail}")
    write_text_atomic(destination, result.stdout, mode=0o600)
    return destination
