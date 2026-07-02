"""Narrow OpenAI Administration API client used by the optional wizard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import ConfigError


BASE_URL = "https://api.openai.com/v1/organization"


def _client() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise ConfigError("wizard HTTP dependency is missing; reinstall with '.[wizard]'") from exc
    return httpx


def _request(
    admin_key: str,
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not admin_key or any(character in admin_key for character in "\n\r\0"):
        raise ConfigError("admin API key must be one non-empty line")
    httpx = _client()
    try:
        response = httpx.request(
            method,
            BASE_URL + path,
            headers={"Authorization": f"Bearer {admin_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ConfigError(f"OpenAI Administration API request failed: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            detail = None
        raise ConfigError(
            f"OpenAI Administration API returned {response.status_code}: "
            f"{detail or response.text[:300] or 'request failed'}"
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise ConfigError("OpenAI Administration API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ConfigError("OpenAI Administration API returned an unexpected response")
    return result


def list_projects(admin_key: str) -> list[dict[str, Any]]:
    result = _request(admin_key, "GET", "/projects?limit=100&include_archived=false")
    projects = result.get("data")
    if not isinstance(projects, list):
        raise ConfigError("OpenAI Administration API project list is malformed")
    return [project for project in projects if isinstance(project, dict) and project.get("status") == "active"]


@dataclass(frozen=True)
class ServiceAccountCredential:
    service_account_id: str
    api_key_id: str
    api_key: str


def create_service_account(admin_key: str, project_id: str, name: str) -> ServiceAccountCredential:
    if not project_id.startswith("proj_"):
        raise ConfigError("project ID must start with proj_")
    result = _request(
        admin_key,
        "POST",
        f"/projects/{project_id}/service_accounts",
        {"name": name},
    )
    api_key = result.get("api_key") or {}
    service_account_id = result.get("id")
    key_id = api_key.get("id") if isinstance(api_key, dict) else None
    key_value = api_key.get("value") if isinstance(api_key, dict) else None
    if not all(isinstance(value, str) and value for value in (service_account_id, key_id, key_value)):
        raise ConfigError("service-account creation did not return its one-time API key")
    # Some deployments return the service account's user principal in the
    # create response. Resolve the canonical deletable service-account ID from
    # the API key owner instead of assuming the top-level ID is suitable.
    key_metadata = _request(admin_key, "GET", f"/projects/{project_id}/api_keys/{key_id}")
    owner = key_metadata.get("owner") or {}
    owner_service_account = owner.get("service_account") if isinstance(owner, dict) else None
    canonical_id = (
        owner_service_account.get("id")
        if isinstance(owner_service_account, dict)
        else None
    )
    if isinstance(canonical_id, str) and canonical_id:
        service_account_id = canonical_id
    return ServiceAccountCredential(service_account_id, key_id, key_value)


def delete_service_account(admin_key: str, project_id: str, service_account_id: str) -> None:
    _request(admin_key, "DELETE", f"/projects/{project_id}/service_accounts/{service_account_id}")
