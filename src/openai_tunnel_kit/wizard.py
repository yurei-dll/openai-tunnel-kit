"""Optional loopback-only web wizard for end-to-end tunnel setup."""

from __future__ import annotations

import json
import secrets
import shlex
import socket
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

from .config import ConfigError, initialize_profile, read_environment
from .control_plane import attach_tunnel, inspect_tunnel, provision_tunnel
from .credentials import encrypt_api_key
from .systemd import install_service


def _dependencies() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.responses import HTMLResponse
        import uvicorn
    except ImportError as exc:
        raise ConfigError(
            "wizard dependencies are missing; install with "
            "'python -m pip install -e .[wizard]'"
        ) from exc
    return FastAPI, Header, HTTPException, HTMLResponse, uvicorn


def _identifiers(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _target_config(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("target_mode")
    if mode == "command":
        command = str(payload.get("command") or "").strip()
        if not command:
            raise ConfigError("MCP command is required")
        try:
            command_parts = shlex.split(command)
        except ValueError as exc:
            raise ConfigError(f"invalid MCP command: {exc}") from exc
        if not command_parts:
            raise ConfigError("MCP command is required")
        return {"command": command_parts[0], "args": command_parts[1:], "env": {}}
    if mode == "url":
        url = str(payload.get("mcp_url") or "").strip()
        if not url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ConfigError("MCP URL must use HTTPS (loopback HTTP is allowed for development)")
        return {"url": url}
    raise ConfigError("target_mode must be 'command' or 'url'")


def run_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one confirmed wizard setup without retaining submitted secrets."""
    if payload.get("confirm") is not True:
        raise ConfigError("explicit confirmation is required")
    profile = str(payload.get("profile") or "").strip()
    runtime_key = str(payload.get("runtime_api_key") or "").strip()
    if not profile:
        raise ConfigError("profile name is required")
    if not runtime_key:
        raise ConfigError("runtime API key is required")
    target = _target_config(payload)
    tunnel_mode = str(payload.get("tunnel_mode") or "existing")
    tunnel_id = str(payload.get("tunnel_id") or "").strip()
    organizations = _identifiers(str(payload.get("organization_ids") or ""))
    workspaces = _identifiers(str(payload.get("workspace_ids") or ""))
    admin_key = str(payload.get("admin_api_key") or "").strip()
    if tunnel_mode == "existing" and not tunnel_id:
        raise ConfigError("existing tunnel mode requires a tunnel ID")
    if tunnel_mode in {"attach", "provision"} and not admin_key:
        raise ConfigError(f"{tunnel_mode} mode requires an admin API key")

    with tempfile.TemporaryDirectory(prefix="openai-tunnel-kit-wizard-") as temporary:
        temporary_path = Path(temporary)
        mcp_file = temporary_path / "mcp.json"
        mcp_file.write_text(
            json.dumps({"mcpServers": {profile: target}}, indent=2) + "\n",
            encoding="utf-8",
        )
        admin_file = temporary_path / "admin.key"
        if admin_key:
            admin_file.write_text(admin_key + "\n", encoding="utf-8")
            admin_file.chmod(0o600)

        paths = initialize_profile(
            profile,
            binary=str(payload.get("tunnel_client_bin") or "tunnel-client"),
            mcp_source=mcp_file,
            force=bool(payload.get("replace_profile")),
            tunnel_id=tunnel_id,
            api_key="",
            pass_desktop_environment=bool(payload.get("pass_desktop_environment")),
        )
        if tunnel_mode == "provision":
            metadata = provision_tunnel(
                profile,
                admin_file,
                str(payload.get("tunnel_name") or profile),
                str(payload.get("tunnel_description") or f"Managed by openai-tunnel-kit profile {profile}"),
                organizations,
                workspaces,
                runtime_api_key=runtime_key,
            )
        elif tunnel_mode == "attach":
            metadata = attach_tunnel(
                profile, admin_file, organizations, workspaces, runtime_api_key=runtime_key
            )
        else:
            metadata = inspect_tunnel(profile, runtime_key)

        tunnel_id = read_environment(paths.env).get("CONTROL_PLANE_TUNNEL_ID", "")
        initialize_profile(
            profile,
            binary=str(payload.get("tunnel_client_bin") or "tunnel-client"),
            mcp_source=mcp_file,
            force=True,
            tunnel_id=tunnel_id,
            api_key="",
            pass_desktop_environment=bool(payload.get("pass_desktop_environment")),
        )
        encrypt_api_key(runtime_key, paths.credential)
        if payload.get("install_service", True):
            install_service(profile, start=True)

    return {
        "ok": True,
        "profile": profile,
        "tunnel": metadata,
        "workspace_attached": bool(metadata.get("workspace_ids")),
        "target_mode": payload.get("target_mode"),
        "chatgpt_settings_url": "https://chatgpt.com/#settings/Connectors",
        "next": (
            "Open ChatGPT Apps settings, create the app, select this tunnel, and scan tools."
            if metadata.get("workspace_ids")
            else "The backend returned no workspace attachment. Use Server URL or resolve account linkage before app creation."
        ),
    }


def create_app(token: Optional[str] = None) -> Any:
    FastAPI, Header, HTTPException, HTMLResponse, _uvicorn = _dependencies()
    app = FastAPI(title="openai-tunnel-kit wizard", docs_url=None, redoc_url=None)
    wizard_token = token or secrets.token_urlsafe(32)
    app.state.wizard_token = wizard_token

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _html(wizard_token)

    @app.post("/api/setup")
    async def setup(payload: dict[str, Any], x_wizard_token: str = Header(default="")) -> dict[str, Any]:
        if not secrets.compare_digest(x_wizard_token, wizard_token):
            raise HTTPException(status_code=403, detail="invalid wizard token")
        try:
            return await threading_to_async(run_setup, payload)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


async def threading_to_async(function: Any, *args: Any) -> Any:
    import asyncio
    return await asyncio.to_thread(function, *args)


def launch_wizard(port: int = 0, open_browser: bool = True) -> None:
    _FastAPI, _Header, _HTTPException, _HTMLResponse, uvicorn = _dependencies()
    token = secrets.token_urlsafe(32)
    app = create_app(token)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    actual_port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Wizard: {url}")
    print("Listening on loopback only. Press Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[listener])
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()


def _html(token: str) -> str:
    escaped_token = json.dumps(token)
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>openai-tunnel-kit wizard</title><style>
:root{color-scheme:dark}body{font:15px system-ui;max-width:900px;margin:32px auto;padding:0 18px;background:#151515;color:#eee}
h1{margin-bottom:6px}.sub{color:#aaa;margin-top:0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
fieldset{border:1px solid #444;border-radius:10px;margin:16px 0;padding:16px}legend{padding:0 8px;font-weight:700}
label{display:block;margin:9px 0 4px;color:#ccc}input,select,textarea{box-sizing:border-box;width:100%;padding:10px;background:#222;color:#fff;border:1px solid #555;border-radius:6px}
input[type=checkbox]{width:auto}button{padding:11px 18px;border:0;border-radius:7px;background:#eee;color:#111;font-weight:700;cursor:pointer}
pre{white-space:pre-wrap;background:#0d0d0d;border:1px solid #333;border-radius:8px;padding:14px;min-height:50px}.warn{color:#ffbd66}.secret{font-family:monospace}
@media(max-width:650px){.grid{grid-template-columns:1fr}}
</style></head><body><h1>openai-tunnel-kit wizard</h1><p class="sub">Local setup UI. Secrets remain in memory and transient mode-0600 files.</p>
<form id="setup"><fieldset><legend>Profile and MCP target</legend><div class="grid">
<div><label>Profile name</label><input name="profile" required value="personal-access-tool"></div>
<div><label>tunnel-client binary</label><input name="tunnel_client_bin" value="tunnel-client"></div>
<div><label>Target type</label><select name="target_mode"><option value="command">Local command / stdio</option><option value="url">Server URL / OAuth-capable HTTP</option></select></div>
<div><label>MCP command</label><input name="command" placeholder="node /absolute/path/dist/index.js"></div>
<div style="grid-column:1/-1"><label>MCP server URL</label><input name="mcp_url" placeholder="https://mcp.example.com/mcp"></div></div></fieldset>
<fieldset><legend>Tunnel control plane</legend><div class="grid">
<div><label>Mode</label><select name="tunnel_mode"><option value="existing">Use existing tunnel</option><option value="attach">Attach existing tunnel scopes</option><option value="provision">Create a tunnel</option></select></div>
<div><label>Existing tunnel ID</label><input name="tunnel_id" placeholder="tunnel_..."></div>
<div><label>Organization IDs (comma-separated)</label><input name="organization_ids" placeholder="org_..."></div>
<div><label>Workspace IDs (comma-separated; never guess)</label><input name="workspace_ids" placeholder="ws_..."></div>
<div><label>Tunnel name</label><input name="tunnel_name" value="Personal MCP tunnel"></div>
<div><label>Description</label><input name="tunnel_description" value="Managed by openai-tunnel-kit"></div></div></fieldset>
<fieldset><legend>Credentials and installation</legend><div class="grid">
<div><label>Runtime API key</label><input class="secret" name="runtime_api_key" type="password" required autocomplete="off"></div>
<div><label>Admin API key (attach/provision only)</label><input class="secret" name="admin_api_key" type="password" autocomplete="off"></div></div>
<p><label><input type="checkbox" name="pass_desktop_environment"> Pass desktop environment</label></p>
<p><label><input type="checkbox" name="replace_profile"> Replace existing profile</label></p>
<p><label><input type="checkbox" name="install_service" checked> Install and start systemd user service</label></p>
<p class="warn"><label><input type="checkbox" name="confirm" required> I confirm this may create or update tunnel metadata and local service configuration.</label></p></fieldset>
<button type="submit">Configure and verify</button></form><h2>Result</h2><pre id="result">Ready.</pre>
<script>const token=""" + escaped_token + """;const form=document.querySelector('#setup'),out=document.querySelector('#result');
form.addEventListener('submit',async e=>{e.preventDefault();out.textContent='Working…';const data={};for(const [k,v] of new FormData(form))data[k]=v;
for(const k of ['confirm','replace_profile','install_service','pass_desktop_environment'])data[k]=form.elements[k].checked;
try{const r=await fetch('/api/setup',{method:'POST',headers:{'content-type':'application/json','x-wizard-token':token},body:JSON.stringify(data)});const body=await r.json();out.textContent=JSON.stringify(body,null,2)}catch(err){out.textContent=String(err)}});</script></body></html>"""
