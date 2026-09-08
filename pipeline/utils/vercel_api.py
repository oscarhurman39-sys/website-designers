"""Vercel deployment via the REST API."""
from __future__ import annotations

import time
from typing import Optional

import base64

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
def _file_entry(path: str, content) -> dict:
    """Vercel's inline-files API takes text as-is and binaries base64
    encoded with an explicit `encoding` flag (client photos/logos)."""
    if isinstance(content, (bytes, bytearray)):
        return {"file": path, "data": base64.b64encode(bytes(content)).decode("ascii"), "encoding": "base64"}
    return {"file": path, "data": content}


def deploy_files(project_name: str, files: dict) -> dict:
    project_name = _sanitize_project_name(project_name)
    payload = {
        "name": project_name,
        "files": [_file_entry(path, content) for path, content in files.items()],
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
    # Vercel returns this deployment's own hostname without a scheme
    # (e.g. "foo-abc123.vercel.app"). It is always valid for a READY
    # deployment, which the clean project alias is not -- see _public_url.
    deployment_url = f"https://{data['url']}" if data.get("url") else ""

    _disable_deployment_protection(project_name)

    # Order matters: the reachability probes below must run against a
    # finished build, otherwise they spend their whole budget polling a
    # deployment that has not been served yet and report a false negative.
    final_state = _poll_until_ready(deployment_id)

    return {
        "deployment_id": deployment_id,
        "url": _public_url(project_name, deployment_url),
        "project_name": project_name,
        "ready_state": final_state,
    }


def _public_url(project_name: str, deployment_url: str) -> str:
    """Return a URL a lead can actually open.

    Vercel only assigns the clean `<project>.vercel.app` alias when the
    project name is short enough; longer names never get one and the
    guessed alias 404s. Observed on this account: every project name up to
    35 characters resolved, every one from 36 characters up returned 404,
    which silently failed 7 of 10 previews before this was fixed.

    Prefer the clean alias anyway -- it is the link that goes in a cold
    email and a tidy hostname converts better than a hash -- but only after
    confirming an anonymous visitor really gets the site. Otherwise fall
    back to the per-deployment hostname the API handed us.
    """
    alias = f"https://{_sanitize_project_name(project_name)}.vercel.app"
    if _wait_until_publicly_accessible(alias):
        return alias

    if deployment_url and _wait_until_publicly_accessible(deployment_url):
        print(
            f"[vercel_api] '{project_name}' has no public '{alias}' alias "
            "(Vercel skips it for longer project names) -- using the "
            f"deployment hostname {deployment_url} instead."
        )
        return deployment_url

    # Neither is reachable. Hand back the best candidate and let
    # design_agent._validate_deployment_url refuse to email it.
    print(
        f"[vercel_api] '{project_name}' is not publicly reachable after "
        f"{_PROTECTION_CHECK_TIMEOUT_SECONDS}s on either {alias} or "
        f"{deployment_url or '(no deployment URL returned)'} -- the preview may show a "
        "Vercel login wall to leads. Check Vercel dashboard -> project -> Settings -> "
        "Deployment Protection, or set VERCEL_BYPASS_TOKEN so screenshot capture can bypass it."
    )
    return deployment_url or alias


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
    and irreversible -- used by utils/teardown.py (expired previews),
    cleanup_tests.py (throwaway test leads) and the opt-in integration test
    (tests/test_pipeline_real.py), never by the normal lead flow. A 404
    (already gone) is treated as success, not an error, since cleanup
    should be idempotent.

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
