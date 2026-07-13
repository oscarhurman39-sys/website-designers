"""Vercel deployment via the REST API.

We deploy by inlining the rendered template files directly in the
deployment request ("Deploy without Git integration"). This avoids requiring
the operator to have installed Vercel's GitHub App / connected the specific
repo through the Vercel dashboard first, which makes the whole pipeline
runnable end-to-end from API tokens alone. The GitHub repo created by
github_api.py remains the source of truth / hand-off artifact for the
client; Vercel just serves the preview.
"""
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
    """Vercel project names must be lowercase alphanumeric + hyphens, <= 100 chars."""
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
    slug = "-".join(filter(None, slug.split("-")))
    return slug[:100] or "preview-site"


def deploy_files(project_name: str, files: dict[str, str]) -> dict:
    """Deploy `files` (path -> text content) as a new production deployment.

    Returns a dict with keys: deployment_id, url (full https:// preview URL),
    project_name.
    """
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
    deployment_url = data.get("url", "")

    final_state = _poll_until_ready(deployment_id)
    public_url = _get_public_production_url(project_name)

    return {
        "deployment_id": deployment_id,
        # Vercel's unique deployment URL is protected by default, even for
        # production deploys. The stable production alias is the public URL
        # intended for prospects.
        "url": public_url or (f"https://{deployment_url}" if deployment_url else ""),
        "deployment_url": f"https://{deployment_url}" if deployment_url else "",
        "project_name": project_name,
        "ready_state": final_state,
    }


def _poll_until_ready(deployment_id: str) -> str:
    """Poll deployment status until READY/ERROR/CANCELED or timeout. Returns final state."""
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


def _get_public_production_url(project_name: str) -> Optional[str]:
    """Return the stable production alias assigned to a Vercel project.

    Vercel's standard deployment protection redirects generated deployment
    hostnames to login while leaving the project's primary production alias
    public. Prefer the exact ``<project>.vercel.app`` alias and only use
    another production alias when it is the sole option.
    """
    project_name = _sanitize_project_name(project_name)
    resp = requests.get(
        f"{_API_BASE}/v9/projects/{project_name}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    resp.raise_for_status()
    aliases = resp.json().get("targets", {}).get("production", {}).get("alias", []) or []
    aliases = [alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()]
    if not aliases:
        return None

    preferred = f"{project_name}.vercel.app"
    hostname = preferred if preferred in aliases else aliases[0]
    return f"https://{hostname}"


def _get_project_id(project_name: str) -> str:
    resp = requests.get(
        f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def invite_collaborator(project_name: str, email: str, project_role: str = "ADMIN") -> None:
    """Invite a client to a specific Vercel project by email.

    Requires VERCEL_TEAM_ID. Vercel has no API to grant access to a single
    project under a personal (non-team) account -- access is only ever
    granted by inviting someone to a TEAM, with an optional per-project
    role assignment (the `projects` field below) scoping what they can do
    within it. If no team is configured, this raises rather than silently
    no-op'ing or inviting the client to unrelated projects that might also
    live in a team.

    Endpoint: POST /v2/teams/{teamId}/members, verified against Vercel's
    published REST API / SDK reference docs. Unlike the rest of this
    pipeline (which was exercised against real APIs during development),
    this specific call could not be verified live -- api.vercel.com wasn't
    reachable from the sandbox this was built in. Test it once against a
    real VERCEL_TOKEN/VERCEL_TEAM_ID before relying on it in production.
    """
    if not config.VERCEL_TEAM_ID:
        raise RuntimeError(
            "Cannot invite a Vercel collaborator without VERCEL_TEAM_ID set -- "
            "Vercel has no per-project invite API for personal (non-team) accounts."
        )
    project_id = _get_project_id(project_name)
    payload = {
        "email": email,
        "role": "MEMBER",
        "projects": [{"projectId": project_id, "role": project_role}],
    }
    resp = requests.post(
        f"{_API_BASE}/v2/teams/{config.VERCEL_TEAM_ID}/members",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()


def delete_project(project_name: str) -> None:
    """Permanently delete a Vercel project (and its deployments). Destructive
    and irreversible -- used only by the opt-in integration test
    (tests/test_pipeline_real.py) to clean up the throwaway project it
    creates, never by the normal pipeline flow. A 404 (already gone) is
    treated as success, not an error, since cleanup should be idempotent.

    Applies the same `_sanitize_project_name` as `deploy_files`, so callers
    can pass the same raw name they'd pass to `deploy_files` (e.g.
    `github_api.make_repo_name(...)`) rather than needing to separately
    track the sanitized form Vercel actually assigned.
    """
    resp = requests.delete(
        f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    if resp.status_code != 404:
        resp.raise_for_status()
