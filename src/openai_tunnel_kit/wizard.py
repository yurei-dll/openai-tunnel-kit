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

from .config import (
    ConfigError, initialize_profile, mcp_launch, profile_paths, read_environment,
    remove_profile,
)
from .control_plane import attach_tunnel, inspect_tunnel, provision_tunnel
from .credentials import decrypt_api_key, encrypt_api_key
from .admin_credentials import forget_admin_key, load_admin_key, save_admin_key
from .wizard_preferences import load_preferences, save_last_organization_id
from .systemd import install_service, wait_for_service
from .platform_admin import create_service_account, delete_service_account, list_projects


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


def _mcp_content(payload: dict[str, Any]) -> dict[str, Any]:
    uploaded = str(payload.get("mcp_json") or "").strip()
    if uploaded:
        try:
            content = json.loads(uploaded)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"uploaded mcp.json is invalid: {exc}") from exc
        mcp_launch(content)
        return content
    return {"mcpServers": {str(payload.get("profile") or "mcp-server"): _target_config(payload)}}


def _wizard_tunnel_arguments(
    existing_environment: dict[str, str], health_url_file: Path,
) -> tuple[str, ...]:
    """Preserve explicit run flags and add collision-safe health defaults."""
    try:
        arguments = shlex.split(existing_environment.get("TUNNEL_CLIENT_ARGS", ""))
    except ValueError as exc:
        raise ConfigError(f"invalid existing TUNNEL_CLIENT_ARGS: {exc}") from exc
    if not any(argument == "--health.listen-addr" or argument.startswith("--health.listen-addr=") for argument in arguments):
        arguments.extend(("--health.listen-addr", "127.0.0.1:0"))
    if not any(argument == "--health.url-file" or argument.startswith("--health.url-file=") for argument in arguments):
        arguments.extend(("--health.url-file", str(health_url_file)))
    return tuple(arguments)


def run_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one confirmed wizard setup without retaining submitted secrets."""
    if payload.get("confirm") is not True:
        raise ConfigError("explicit confirmation is required")
    profile = str(payload.get("profile") or "").strip()
    runtime_key = str(payload.get("runtime_api_key") or "").strip()
    credential_mode = str(payload.get("credential_mode") or "automatic")
    admin_key = str(payload.get("admin_api_key") or "").strip()
    if not profile:
        raise ConfigError("profile name is required")
    if credential_mode not in {"automatic", "existing"}:
        raise ConfigError("credential mode must be automatic or existing")
    if credential_mode == "existing" and payload.get("rotate_runtime_key"):
        raise ConfigError("runtime-key rotation applies only to automatic credentials")

    existing_paths = profile_paths(profile)
    profile_preexisting = existing_paths.env.exists()
    existing_environment = read_environment(existing_paths.env) if profile_preexisting else {}
    tunnel_arguments = _wizard_tunnel_arguments(existing_environment, existing_paths.health_url)
    if profile_preexisting and not payload.get("replace_profile"):
        incomplete = (
            not existing_environment.get("CONTROL_PLANE_TUNNEL_ID")
            and not existing_environment.get("CONTROL_PLANE_API_KEY")
            and not existing_paths.credential.exists()
        )
        if incomplete:
            raise ConfigError(
                f"incomplete profile from an earlier failed setup: {profile}; "
                "enable 'Replace existing profile' to retry (no tunnel or runtime credential is attached)"
            )

    reuse_automatic_credential = (
        credential_mode == "automatic"
        and profile_preexisting
        and not payload.get("rotate_runtime_key")
        and (
            existing_paths.credential.exists()
            or bool(existing_environment.get("CONTROL_PLANE_API_KEY"))
        )
    )
    if reuse_automatic_credential:
        runtime_key = (
            decrypt_api_key(existing_paths.credential)
            if existing_paths.credential.exists()
            else existing_environment["CONTROL_PLANE_API_KEY"]
        )
        runtime_credential_action = "reused"
    elif credential_mode == "automatic":
        if not admin_key:
            raise ConfigError("creating an automatic runtime credential requires an admin API key")
        runtime_credential_action = "created"
    else:
        if not runtime_key:
            raise ConfigError("existing credential mode requires a runtime API key")
        runtime_credential_action = "supplied"
    content = _mcp_content(payload)
    tunnel_mode = str(payload.get("tunnel_mode") or "existing")
    tunnel_id = str(payload.get("tunnel_id") or "").strip()
    organizations = _identifiers(str(payload.get("organization_ids") or ""))
    workspaces = _identifiers(str(payload.get("workspace_ids") or ""))
    if tunnel_mode == "existing" and not tunnel_id:
        raise ConfigError("existing tunnel mode requires a tunnel ID")
    if tunnel_mode not in {"existing", "attach", "provision"}:
        raise ConfigError("tunnel mode must be existing, attach, or provision")
    if tunnel_mode in {"attach", "provision"} and not admin_key:
        raise ConfigError(f"{tunnel_mode} mode requires an admin API key")
    if tunnel_mode in {"attach", "provision"} and not organizations and not workspaces:
        raise ConfigError(
            f"{tunnel_mode} mode requires an explicit organization or workspace ID; "
            "no account, tunnel, or profile was created"
        )

    project_id = str(payload.get("project_id") or "").strip()
    if credential_mode == "automatic" and not reuse_automatic_credential and not project_id.startswith("proj_"):
        raise ConfigError("automatic credential mode requires a selected project")
    service_credential = None
    profile_created = False

    with tempfile.TemporaryDirectory(prefix="openai-tunnel-kit-wizard-") as temporary:
        temporary_path = Path(temporary)
        mcp_file = temporary_path / "mcp.json"
        mcp_file.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        admin_file = temporary_path / "admin.key"
        if admin_key:
            admin_file.write_text(admin_key + "\n", encoding="utf-8")
            admin_file.chmod(0o600)

        existing_paths.health_url.parent.mkdir(parents=True, exist_ok=True)
        existing_paths.health_url.parent.chmod(0o700)
        paths = initialize_profile(
            profile,
            binary=str(payload.get("tunnel_client_bin") or "tunnel-client"),
            arguments=tunnel_arguments,
            mcp_source=mcp_file,
            force=bool(payload.get("replace_profile")),
            tunnel_id=tunnel_id,
            api_key="",
            pass_desktop_environment=bool(payload.get("pass_desktop_environment")),
        )
        profile_created = not profile_preexisting
        try:
            if credential_mode == "automatic" and not reuse_automatic_credential:
                service_credential = create_service_account(
                    admin_key,
                    project_id,
                    str(payload.get("service_account_name") or f"openai-tunnel-kit-{profile}"),
                )
                runtime_key = service_credential.api_key
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
        except Exception as exc:
            created_tunnel = bool(read_environment(paths.env).get("CONTROL_PLANE_TUNNEL_ID")) and not tunnel_id
            cleanup_warnings = []
            if service_credential is not None and not created_tunnel:
                try:
                    delete_service_account(admin_key, project_id, service_credential.service_account_id)
                except ConfigError as rollback_exc:
                    cleanup_warnings.append(f"service-account cleanup failed: {rollback_exc}")
            if profile_created and not created_tunnel:
                try:
                    remove_profile(profile)
                except (ConfigError, OSError) as rollback_exc:
                    cleanup_warnings.append(f"local profile cleanup failed: {rollback_exc}")
            suffix = f"; cleanup warnings: {'; '.join(cleanup_warnings)}" if cleanup_warnings else ""
            raise ConfigError(f"{exc}{suffix}") from exc

        tunnel_id = read_environment(paths.env).get("CONTROL_PLANE_TUNNEL_ID", "")
        initialize_profile(
            profile,
            binary=str(payload.get("tunnel_client_bin") or "tunnel-client"),
            arguments=tunnel_arguments,
            mcp_source=mcp_file,
            force=True,
            tunnel_id=tunnel_id,
            api_key="",
            pass_desktop_environment=bool(payload.get("pass_desktop_environment")),
        )
        encrypt_api_key(runtime_key, paths.credential)
        if payload.get("install_service", True):
            install_service(profile, start=True)
            wait_for_service(profile)

    return {
        "ok": True,
        "profile": profile,
        "tunnel": metadata,
        "workspace_attached": bool(metadata.get("workspace_ids")),
        "target_mode": payload.get("target_mode"),
        "service_account_id": service_credential.service_account_id if service_credential else None,
        "runtime_api_key_id": service_credential.api_key_id if service_credential else None,
        "runtime_credential_action": runtime_credential_action,
        "chatgpt_settings_url": "https://chatgpt.com/#settings/Connectors",
        "doctor_command": f"openai-tunnel-kit doctor {profile}",
        "next": (
            "Open ChatGPT Apps settings, create the app, select this tunnel, and scan tools."
            if metadata.get("workspace_ids")
            else "The backend returned no workspace attachment. Use Server URL or resolve account linkage before app creation."
        ),
    }


def _admin_key_for_payload(payload: dict[str, Any]) -> tuple[str, bool]:
    """Resolve a transient browser value or the global user-wallet credential."""
    submitted = str(payload.get("admin_api_key") or "").strip()
    should_save = bool(payload.get("save_admin_key"))
    if submitted:
        return submitted, should_save
    if payload.get("use_saved_admin_key"):
        if should_save:
            raise ConfigError("enter a new admin key before choosing to save it")
        return load_admin_key(), False
    return "", False


def run_setup_with_admin_credential(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve the wallet credential locally without returning it to the browser."""
    admin_key, should_save = _admin_key_for_payload(payload)
    if should_save:
        # Validate before storing, and store before setup mutates local or remote
        # state so a wallet failure cannot make a successful setup look failed.
        list_projects(admin_key)
        save_admin_key(admin_key)
    resolved = dict(payload)
    resolved["admin_api_key"] = admin_key
    result = run_setup(resolved)
    organizations = _identifiers(str(payload.get("organization_ids") or ""))
    if organizations:
        try:
            save_last_organization_id(organizations[0])
        except (ConfigError, OSError) as exc:
            result["preference_warning"] = f"setup succeeded but the organization preference was not saved: {exc}"
    return result


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
            return await threading_to_async(run_setup_with_admin_credential, payload)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects")
    async def projects(payload: dict[str, Any], x_wizard_token: str = Header(default="")) -> dict[str, Any]:
        if not secrets.compare_digest(x_wizard_token, wizard_token):
            raise HTTPException(status_code=403, detail="invalid wizard token")
        try:
            admin_key, should_save = await threading_to_async(_admin_key_for_payload, payload)
            projects = await threading_to_async(list_projects, admin_key)
            if should_save:
                await threading_to_async(save_admin_key, admin_key)
            return {"projects": projects, "admin_key_saved": should_save}
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin-credential/forget")
    async def forget_credential(x_wizard_token: str = Header(default="")) -> dict[str, Any]:
        if not secrets.compare_digest(x_wizard_token, wizard_token):
            raise HTTPException(status_code=403, detail="invalid wizard token")
        try:
            removed = await threading_to_async(forget_admin_key)
            return {"removed": removed}
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/preferences")
    async def preferences(x_wizard_token: str = Header(default="")) -> dict[str, str]:
        if not secrets.compare_digest(x_wizard_token, wizard_token):
            raise HTTPException(status_code=403, detail="invalid wizard token")
        try:
            return await threading_to_async(load_preferences)
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
pre{white-space:pre-wrap;background:#0d0d0d;border:1px solid #333;border-radius:8px;padding:14px;min-height:50px}.warn{color:#ffbd66}.secret{font-family:monospace}.req{color:#ff5c5c;font-weight:800}.hint{color:#999;font-size:13px}
@media(max-width:650px){.grid{grid-template-columns:1fr}}
</style></head><body><h1>openai-tunnel-kit wizard</h1><p class="sub">Local setup UI. Runtime secrets use encrypted credentials; the optional admin credential is stored globally in your system wallet.</p>
<form id="setup"><fieldset><legend>1. MCP configuration</legend><div class="grid">
<div><label>Profile name <span class="req" title="Required">*</span></label><input name="profile" required value="personal-access-tool"></div>
<div><label>tunnel-client binary</label><input name="tunnel_client_bin" value="tunnel-client"></div>
<div style="grid-column:1/-1"><label>mcp.json (recommended) <span class="req" title="Required unless a manual target is entered">*</span></label><input name="mcp_file" type="file" accept="application/json,.json"><span class="hint">Required unless you enter the MCP command or Server URL below.</span></div>
<div><label>Target type</label><select name="target_mode"><option value="command">Local command / stdio</option><option value="url">Server URL / OAuth-capable HTTP</option></select></div>
<div><label>MCP command <span class="req" title="Required for local command mode without mcp.json">*</span></label><input name="command" placeholder="node /absolute/path/dist/index.js"></div>
<div style="grid-column:1/-1"><label>MCP server URL <span class="req" title="Required for Server URL mode without mcp.json">*</span></label><input name="mcp_url" placeholder="https://mcp.example.com/mcp"></div></div></fieldset>
<fieldset><legend>2. Administrative provisioning</legend><div class="grid">
<div><label>Credential mode</label><select name="credential_mode"><option value="automatic">Create dedicated service account</option><option value="existing">Use existing runtime key</option></select></div>
<div><label>Admin API key <span class="req" title="Required for automatic credentials and tunnel mutations">*</span></label><input class="secret" name="admin_api_key" type="password" autocomplete="off"><span class="hint">A value entered here overrides the saved global credential.</span></div>
<div><label>System wallet</label><p><label><input type="checkbox" name="use_saved_admin_key" checked> Use saved admin key</label></p><p><label><input type="checkbox" name="save_admin_key"> Save entered key globally in system wallet</label></p><button type="button" id="forget-admin-key">Forget saved key</button></div>
<div><label>Project <span class="req" title="Required for automatic credentials">*</span></label><select name="project_id"><option value="">Load projects with admin key</option></select></div>
<div><label>&nbsp;</label><button type="button" id="load-projects">Load projects</button></div>
<div><label>Service-account name</label><input name="service_account_name" placeholder="Defaults to openai-tunnel-kit-&lt;profile&gt;"><span class="hint">Leave blank to include the profile identity automatically.</span></div>
<div><label>Existing runtime key (manual mode only) <span class="req" title="Required in existing credential mode">*</span></label><input class="secret" name="runtime_api_key" type="password" autocomplete="off"></div></div></fieldset>
<fieldset><legend>3. Tunnel control plane</legend><div class="grid">
<div><label>Mode</label><select name="tunnel_mode"><option value="provision">Create a tunnel</option><option value="existing">Use existing tunnel</option><option value="attach">Attach existing tunnel scopes</option></select></div>
<div><label>Existing tunnel ID <span class="req" title="Required when reusing or attaching a tunnel">*</span></label><input name="tunnel_id" placeholder="tunnel_..."></div>
<div><label>Organization IDs (comma-separated) <span class="req" title="An organization or workspace ID is required">*</span></label><input name="organization_ids" placeholder="org_..."></div>
<div><label>Workspace IDs (comma-separated; never guess) <span class="req" title="An organization or workspace ID is required">*</span></label><input name="workspace_ids" placeholder="ws_..."></div>
<div style="grid-column:1/-1" class="hint"><span class="req">*</span> At least one organization or workspace ID is required when creating or attaching a tunnel.</div>
<div><label>Tunnel name <span class="req" title="Required when creating a tunnel">*</span></label><input name="tunnel_name" placeholder="Defaults to the profile name"><span class="hint">Leave blank to match the profile name automatically.</span></div>
<div><label>Description <span class="req" title="Required when creating a tunnel">*</span></label><input name="tunnel_description" value="Managed by openai-tunnel-kit"></div></div></fieldset>
<fieldset><legend>4. Installation</legend>
<p><label><input type="checkbox" name="pass_desktop_environment"> Pass desktop environment</label></p>
<p><label><input type="checkbox" name="replace_profile"> Replace existing profile</label></p>
<p><label><input type="checkbox" name="rotate_runtime_key"> Rotate automatic runtime credential</label><span class="hint">Normally, replacing an existing profile reuses its encrypted runtime key. Enable this only to create a new service account key; revoke the previous service account after verification.</span></p>
<p><label><input type="checkbox" name="install_service" checked> Install and start systemd user service</label></p>
<p class="warn"><label><input type="checkbox" name="confirm" required> I confirm this may create or update tunnel metadata and local service configuration. <span class="req" title="Required">*</span></label></p></fieldset>
<button type="submit">Configure and verify</button></form><h2>Result</h2><pre id="result">Ready.</pre>
<script>const token=""" + escaped_token + """;const form=document.querySelector('#setup'),out=document.querySelector('#result');
const adminCredentialData=()=>({admin_api_key:form.elements.admin_api_key.value,use_saved_admin_key:form.elements.use_saved_admin_key.checked,save_admin_key:form.elements.save_admin_key.checked});
fetch('/api/preferences',{headers:{'x-wizard-token':token}}).then(async r=>{const body=await r.json();if(r.ok&&body.last_organization_id&&!form.elements.organization_ids.value)form.elements.organization_ids.value=body.last_organization_id});
document.querySelector('#load-projects').addEventListener('click',async()=>{out.textContent='Loading projects…';const r=await fetch('/api/projects',{method:'POST',headers:{'content-type':'application/json','x-wizard-token':token},body:JSON.stringify(adminCredentialData())});const body=await r.json();if(!r.ok){out.textContent=JSON.stringify(body,null,2);return}const select=form.elements.project_id;select.innerHTML='';for(const p of body.projects){const o=document.createElement('option');o.value=p.id;o.textContent=`${p.name} (${p.id})`;select.appendChild(o)}if(body.admin_key_saved){form.elements.admin_api_key.value='';form.elements.save_admin_key.checked=false;form.elements.use_saved_admin_key.checked=true}out.textContent=`Loaded ${body.projects.length} active project(s).${body.admin_key_saved?' Admin key saved in the system wallet.':''}`});
document.querySelector('#forget-admin-key').addEventListener('click',async()=>{out.textContent='Removing saved admin key…';const r=await fetch('/api/admin-credential/forget',{method:'POST',headers:{'x-wizard-token':token}});const body=await r.json();if(!r.ok){out.textContent=JSON.stringify(body,null,2);return}form.elements.use_saved_admin_key.checked=false;out.textContent=body.removed?'Removed the saved global admin key.':'No saved admin key was present.'});
form.addEventListener('submit',async e=>{e.preventDefault();out.textContent='Working…';const data={};for(const [k,v] of new FormData(form))if(k!=='mcp_file')data[k]=v;const file=form.elements.mcp_file.files[0];if(file)data.mcp_json=await file.text();
for(const k of ['confirm','replace_profile','rotate_runtime_key','install_service','pass_desktop_environment','use_saved_admin_key','save_admin_key'])data[k]=form.elements[k].checked;
try{const r=await fetch('/api/setup',{method:'POST',headers:{'content-type':'application/json','x-wizard-token':token},body:JSON.stringify(data)});const body=await r.json();out.textContent=JSON.stringify(body,null,2)}catch(err){out.textContent=String(err)}});</script></body></html>"""
