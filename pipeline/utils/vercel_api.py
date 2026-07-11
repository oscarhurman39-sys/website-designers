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
from utils import retry

_API_BASE = "https://api.vercel.com"
_DEPLOY_TIMEOUT_SECONDS = 90
_POLL_INTERVAL_SECONDS = 3

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
    """Vercel project names must be lowercase alphanumeric + hyphens, <= 100 chars."""
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
    slug = "-".join(filter(None, slug.split("-")))
    return slug[:100] or "preview-site"


@_vercel_retry
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
    url = data.get("url", "")

    # Make the preview actually viewable by strangers. Vercel enables
    # "Vercel Authentication" deployment protection by default on new
    # projects -- a real emailed screenshot from this pipeline showed the
    # Vercel login wall instead of the site, so leads clicking the link hit
    # the same wall. ssoProtection=null disables it (per Vercel's Projects
    # API docs; not live-verifiable from the build sandbox). Failures are
    # non-fatal but loudly explain the manual dashboard fix.
    _disable_deployment_protection(project_name)

    final_state = _poll_until_ready(deployment_id)

    return {
        "deployment_id": deployment_id,
        "url": _public_url(deployment_id, project_name, f"https://{url}" if url else ""),
        "project_name": project_name,
        "ready_state": final_state,
    }


def _public_url(deployment_id: str, project_name: str, fallback_url: str) -> str:
    """The URL to store, email, and screenshot: prefer the clean production
    alias (`{project}.vercel.app`) over the hash-suffixed deployment URL the
    create call returns. Vercel's Standard Deployment Protection (on by
    default) serves a "Log in to Vercel" screen on deployment URLs but NOT
    on the production alias -- using the deployment URL is exactly how a
    login page ends up screenshotted into a cold email. Alias resolution is
    per Vercel's deployment API docs (the `alias` array on GET
    /v13/deployments/{id}). Alias assignment can lag the READY state by a
    few seconds (observed in a real run: the alias list came back empty and
    the hash-suffixed deployment URL leaked into the email), so the lookup
    retries briefly; as a last resort the constructed {project}.vercel.app
    is used only after fetching it and confirming it serves one of OUR
    generated pages (the templates' 'web design team' footer marker), so a
    name collision can never put someone else's site in a cold email."""
    preferred = f"{project_name}.vercel.app"

    aliases: list[str] = []
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{_API_BASE}/v13/deployments/{deployment_id}",
                headers=_headers(),
                params=_team_params(),
                timeout=30,
            )
            resp.raise_for_status()
            aliases = resp.json().get("alias") or []
        except requests.RequestException:
            aliases = []
        if aliases:
            break
        time.sleep(2)  # alias assignment often lands moments after READY

    if preferred in aliases:
        return f"https://{preferred}"
    if aliases:
        # No exact match: the shortest alias is the cleanest public one
        # (deployment-hash aliases are strictly longer).
        return f"https://{min(aliases, key=len)}"

    # Alias list never appeared. Production deployments normally do get the
    # project alias -- trust it only if it demonstrably serves our page.
    try:
        check = requests.get(f"https://{preferred}", timeout=15)
        if check.status_code < 400 and "web design team" in check.text:
            return f"https://{preferred}"
    except requests.RequestException:
        pass
    return fallback_url


def _disable_deployment_protection(project_name: str) -> None:
    """Turn off Vercel Authentication for a preview project so anonymous
    visitors (the leads we email) can open it. Per Vercel's Projects API
    (PATCH /v9/projects/{name}, body {"ssoProtection": null}); could not be
    live-verified from the build sandbox (no route to api.vercel.com), so
    any failure prints the exact manual dashboard path instead of raising."""
    try:
        resp = requests.patch(
            f"{_API_BASE}/v9/projects/{_sanitize_project_name(project_name)}",
            headers=_headers(),
            params=_team_params(),
            json={"ssoProtection": None},
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"[vercel_api] Could not disable deployment protection for '{project_name}' "
                f"(HTTP {resp.status_code}). Recipients will hit a Vercel login wall until you "
                "disable it manually: Vercel dashboard -> project -> Settings -> "
                "Deployment Protection -> Vercel Authentication -> Disabled."
            )
    except requests.RequestException as exc:
        print(
            f"[vercel_api] Deployment-protection request failed ({exc}) -- if the preview shows "
            "a Vercel login page, disable protection manually in the project's settings."
        )


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


@_vercel_retry
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
