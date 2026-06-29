"""Small, deterministic text templates used by the toolkit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


def _environment_value(value: str) -> str:
    """Quote a value using the conservative subset accepted by EnvironmentFile."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_profile(binary: str, arguments: Sequence[str]) -> str:
    # systemd expands $TUNNEL_CLIENT_ARGS into words. JSON strings preserve spaces
    # and quotes without requiring a shell.
    encoded_args = " ".join(json.dumps(argument, ensure_ascii=False) for argument in arguments)
    return "".join(
        (
            "# Managed by openai-tunnel-kit. Safe to copy between machines.\n",
            f"TUNNEL_CLIENT_BIN={_environment_value(binary)}\n",
            f"TUNNEL_CLIENT_ARGS={_environment_value(encoded_args)}\n",
        )
    )


def render_systemd_unit(config_dir: Path) -> str:
    environment_file = config_dir / "profiles" / "%i.env"
    mcp_config = config_dir / "profiles" / "%i.mcp.json"
    return f"""[Unit]
Description=OpenAI tunnel-client profile %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={environment_file}
Environment="MCP_CONFIG={mcp_config}"
ExecStart=/usr/bin/env ${{TUNNEL_CLIENT_BIN}} $TUNNEL_CLIENT_ARGS
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
"""


def render_mcp_example() -> str:
    return json.dumps(
        {
            "mcpServers": {
                "example": {
                    "command": "/absolute/path/to/mcp-server",
                    "args": [],
                    "env": {},
                }
            }
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
