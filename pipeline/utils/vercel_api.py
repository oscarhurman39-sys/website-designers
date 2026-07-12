"""Vercel deployment via the REST API."""
from __future__ import annotations

import time
from typing import Optional

import requests

import config
from utils import retry

_API_BASE = "https://api.vercel.com"
_DEPLOY_TIMEOUT_SECONDS = 90
_POLL_INTERVAL_SECONDS = 3
_PROTECTION_CHECK_TIMEOUT_SECONDS = 10

# All public functions below retry transient failures (connection errors,
# 429/5xx) 3 times with 2s/4s backoff; definitive 4xx errors raise
# immediately. Retrying deploy_files can at worst create a second deployment
# on the same project (the latest one wins), which is harmless.
_vercel_retry = retry.with_retries(retriable=(requests.RequestException,), label="vercel")


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


@_vercel_retry
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

    public_url = f"https://{project_name}.vercel.app"
    if not _wait_until_publicly_accessible(public_url):
        print(
            f"[vercel_api] '{project_name}' still looks protected after "
            f"{_PROTECTION_CHECK_TIMEOUT_SECONDS}s -- the preview may show a Vercel login "
            "wall to leads. Check Vercel dashboard -> project -> Settings -> Deployment "
            "Protection, or set VERCEL_BYPASS_TOKEN so screenshot capture can bypass it."
        )

    final_state = _poll_until_ready(deployment_id)

    return {
        "deployment_id": deployment_id,
        "url": public_url,
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


_LOGIN_WALL_MARKERS = ("vercel authentication", "log in to vercel")


def _wait_until_publicly_accessible(url: str, timeout_seconds: float = _PROTECTION_CHECK_TIMEOUT_SECONDS) -> bool:
    """Poll `url` right after disabling deployment protection to confirm an
    anonymous visitor (a lead clicking the emailed link) actually gets the
    site rather than a Vercel login wall. Settings changes can lag a few
    seconds behind the PATCH response, so this retries for up to
    `timeout_seconds` before giving up. Non-fatal either way -- the caller
    only uses the result to decide whether to print a warning."""
    if not url:
        return True
    headers = {"x-vercel-protection-bypass": config.VERCEL_BYPASS_TOKEN} if config.VERCEL_BYPASS_TOKEN else {}
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code < 400 and not any(m in resp.text.lower() for m in _LOGIN_WALL_MARKERS):
                return True
        except requests.RequestException:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


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


@_vercel_retry
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


@_vercel_retry
def _get_project_id(project_name: str) -> str:
    resp = requests.get(
        f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


@_vercel_retry
def invite_collaborator(project_name: str, email: str, project_role: str = "ADMIN") -> None:
    """Invite a client to a specific Vercel project by email.

    Requires VERCEL_TEAM_ID. Vercel has no API to grant access to a single
    project under a personal (non-team) account -- access is only ever
    granted by inviting someone to a TEAM, with an optional per-project
    role assignment (the `projects` field below) scoping what they can do
    within it. If no team is configured, this raises rather than silently
    no-op'ing or inviting the client to unrelated projects that might also
    live in a team.
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


@_vercel_retry
def delete_project(project_name: str) -> None:
    """Permanently delete a Vercel project (and its deployments). Destructive
    and irreversible -- used only by the opt-in integration test
    (tests/test_pipeline_real.py) to clean up the throwaway project it
    creates, never by the normal pipeline flow. A 404 (already gone) is
    treated as success, not an error, since cleanup should be idempotent.
    """
    resp = requests.delete(
        f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
        headers=_headers(),
        params=_team_params(),
        timeout=30,
    )
    if resp.status_code != 404:
        resp.raise_for_status()
