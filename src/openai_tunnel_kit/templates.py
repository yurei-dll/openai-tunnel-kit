"""Small, deterministic text templates used by the toolkit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Optional, Sequence


def _environment_value(value: str) -> str:
    """Quote a value using the conservative subset accepted by EnvironmentFile."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_profile(
    binary: str,
    arguments: Sequence[str],
    tunnel_id: str = "",
    api_key: str = "",
    mcp_arguments: Sequence[str] = (),
    extra_environment: Optional[Mapping[str, str]] = None,
    pass_desktop_environment: bool = False,
) -> str:
    # systemd expands $TUNNEL_CLIENT_ARGS into words. JSON strings preserve spaces
    # and quotes without requiring a shell.
    encoded_args = " ".join(json.dumps(argument, ensure_ascii=False) for argument in arguments)
    encoded_mcp_args = " ".join(
        json.dumps(argument, ensure_ascii=False) for argument in mcp_arguments
    )
    lines = [
        "# Managed by openai-tunnel-kit. Contains credentials; keep mode 0600.\n",
        "# Runtime credentials: create these in the OpenAI Platform settings.\n",
        f"CONTROL_PLANE_TUNNEL_ID={_environment_value(tunnel_id)}\n",
        f"CONTROL_PLANE_API_KEY={_environment_value(api_key)}\n",
        f"TUNNEL_CLIENT_BIN={_environment_value(binary)}\n",
        f"TUNNEL_CLIENT_ARGS={_environment_value(encoded_args)}\n",
        f"TUNNEL_CLIENT_MCP_ARGS={_environment_value(encoded_mcp_args)}\n",
        "OPENAI_TUNNEL_KIT_PASS_DESKTOP_ENVIRONMENT="
        f"{_environment_value('true' if pass_desktop_environment else 'false')}\n",
    ]
    if extra_environment:
        lines.append("# Environment requested by the registered MCP server.\n")
        lines.extend(
            f"{key}={_environment_value(value)}\n"
            for key, value in sorted(extra_environment.items())
        )
    return "".join(lines)


def render_systemd_unit(config_dir: Path) -> str:
    environment_file = config_dir / "profiles" / "%i.env"
    return f"""[Unit]
Description=OpenAI tunnel-client profile %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={environment_file}
ExecStart=/usr/bin/env ${{TUNNEL_CLIENT_BIN}} run $TUNNEL_CLIENT_ARGS $TUNNEL_CLIENT_MCP_ARGS
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
"""


def render_desktop_environment_drop_in() -> str:
    return """[Service]
PassEnvironment=DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS
PassEnvironment=XDG_RUNTIME_DIR XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DESKTOP_SESSION
PassEnvironment=KDE_FULL_SESSION GNOME_DESKTOP_SESSION_ID
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
