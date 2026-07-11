"""Vercel deployment via the REST API."""
from __future__ import annotations

import time
from typing import Optional

import requests

import config

_API_BASE = "https://api.vercel.com"
_DEPLOY_TIMEOUT_SECONDS = 90
_POLL_INTERVAL_SECONDS = 3


def _headers() -> dict[str, str]:
    if not config.VERCEL_TOKEN:
        raise RuntimeError("VERCEL_TOKEN is not configured.")
    return {"Authorization": f"Bearer {config.VERCEL_TOKEN}", "Content-Type": "application/json"}


def _team_params() -> dict[str, str]:
    return {"teamId": config.VERCEL_TEAM_ID} if config.VERCEL_TEAM_ID else {}


def _sanitize_project_name(name: str) -> str:
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
    slug = "-".join(filter(None, slug.split("-")))
    return slug[:100] or "preview-site"


def deploy_files(project_name: str, files: dict[str, str]) -> dict:
    project_name = _sanitize_project_name(project_name)
    payload = {
        "name": project_name,
        "files": [{"file": path, "data": content} for path, content in files.items()],
        "projectSettings": {"framework": None},
        "target": "production",
    }
    resp = requests.post(
        f"{_API_BASE}/v13/deployments",
        headers=_headers(),
        params=_team_params(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    deployment_id = data["id"]
    url = data.get("url", "")

    _disable_deployment_protection(project_name)

    final_state = _poll_until_ready(deployment_id)

    return {
        "deployment_id": deployment_id,
        "url": f"https://{project_name}.vercel.app",
        "project_name": project_name,
        "ready_state": final_state,
    }


def _disable_deployment_protection(project_name: str) -> None:
    try:
        resp = requests.patch(
            f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
            headers=_headers(),
            params=_team_params(),
            json={"ssoProtection": None, "passwordProtection": None},
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"[vercel_api] Could not disable deployment protection for '{project_name}' "
                f"(HTTP {resp.status_code}). Manually disable: Vercel -> project -> Settings -> "
                "Deployment Protection -> Vercel Authentication -> Disabled."
            )
    except requests.RequestException as exc:
        print(
            f"[vercel_api] Deployment-protection request failed ({exc}) -- "
            "if preview shows a Vercel login page, disable protection manually."
        )


def _poll_until_ready(deployment_id: str) -> str:
    deadline = time.monotonic() + _DEPLOY_TIMEOUT_SECONDS
    state = "QUEUED"
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{_API_BASE}/v13/deployments/{deployment_id}",
            headers=_headers(),
            params=_team_params(),
            timeout=30,
        )
        resp.raise_for_status()
        state = resp.json().get("readyState", "QUEUED")
        if state in ("READY", "ERROR", "CANCELED"):
            return state
        time.sleep(_POLL_INTERVAL_SECONDS)
    return state


def get_deployment_url(deployment_id: str) -> Optional[str]:
    resp = requests.get(
        f"{_API_BASE}/v13/deployments/{deployment_id}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.json().get("url")
    return f"https://{url}" if url else None