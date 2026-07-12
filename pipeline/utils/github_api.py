"""GitHub repo creation, file push, and ownership transfer via PyGithub.

Each lead gets its own private repo holding its rendered website template.
When a deal closes, we invite the client as a collaborator and then remove
our own access so they end up with full, sole control of the repo.
"""
from __future__ import annotations

import re
from typing import Optional

from github import Github, GithubException
from github.Repository import Repository
from urllib3.util.retry import Retry

import config

_client: Optional[Github] = None

# Transport-level retry (3 attempts, 2s/4s exponential backoff) for every
# GitHub API call made through this client. This is deliberately done here
# rather than with utils/retry.py's function decorator: urllib3's Retry
# understands idempotency -- non-idempotent POSTs (create repo, create file)
# are only retried when the connection failed before the request reached
# the server, never after an ambiguous response, so a retry can't create
# duplicate repos/commits. GETs/DELETEs also retry on 429/5xx responses.
# 403 is included alongside 429 for GitHub's *secondary* rate limit, which
# (unlike the primary limit) is signaled with a plain 403 + Retry-After
# rather than 429 -- see respect_retry_after_header below, and GitHub's own
# guidance: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#about-secondary-rate-limits
_RETRY = Retry(
    total=3,
    backoff_factor=2,
    status_forcelist=(403, 429, 500, 502, 503, 504),
    respect_retry_after_header=True,
)


def _get_client() -> Github:
    global _client
    if _client is None:
        if not config.GITHUB_TOKEN:
            raise RuntimeError("GITHUB_TOKEN is not configured.")
        _client = Github(config.GITHUB_TOKEN, retry=_RETRY)
    return _client


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", text.strip().lower()).strip("-")
    return slug or "site"


def make_repo_name(business_name: str, lead_id: int) -> str:
    """Deterministic, collision-free repo name, e.g. `joes-cafe-preview-42`."""
    return f"{_slugify(business_name)}-preview-{lead_id}"


def create_repo(repo_name: str, private: bool = True, description: str = "") -> Repository:
    """Create a new repo owned by the authenticated GitHub account. Idempotent:
    if a repo with that name already exists for this account, returns it."""
    client = _get_client()
    user = client.get_user()
    try:
        return user.create_repo(
            name=repo_name,
            private=private,
            description=description or "Auto-generated website preview",
            auto_init=False,
        )
    except GithubException as exc:
        if exc.status == 422:  # name already exists
            return user.get_repo(repo_name)
        raise


def push_files(repo: Repository, files: dict[str, str], commit_message: str = "Initial preview site") -> None:
    """Create each file in `files` (path -> text content) in a single-ish batch.

    PyGithub's Contents API creates one commit per file (there is no native
    multi-file commit helper), which is fine for a handful of small template
    files like index.html/style.css.

    If a file already exists (422 -- e.g. a retried deploy after Vercel
    failed once, where create_repo idempotently returned the existing repo),
    update it in place instead of raising: a single transient failure
    downstream must never permanently brick the lead's retry loop here.
    """
    for path, content in files.items():
        try:
            repo.create_file(path=path, message=f"{commit_message}: {path}", content=content)
        except GithubException as exc:
            if exc.status == 422:
                existing = repo.get_contents(path)
                repo.update_file(
                    path=path,
                    message=f"{commit_message}: {path}",
                    content=content,
                    sha=existing.sha,
                )
            else:
                raise


def create_repo_with_files(
    business_name: str,
    lead_id: int,
    files: dict[str, str],
) -> tuple[Optional[Repository], str, str]:
    """Convenience wrapper: create the repo and push all template files.

    Returns (repo_object, html_url, full_name). In SAFE_MODE, makes no real
    GitHub API calls and returns stub values instead (repo_object is None
    in that case -- design_agent.py's only caller discards it already).
    """
    repo_name = make_repo_name(business_name, lead_id)
    if config.SAFE_MODE:
        print(f"[github_api] SAFE_MODE: skipping real repo creation for {repo_name}.")
        return None, f"https://github.com/safe-mode/{repo_name}", f"safe-mode/{repo_name}"
    repo = create_repo(repo_name, private=True, description=f"Website preview for {business_name}")
    push_files(repo, files)
    return repo, repo.html_url, repo.full_name


def invite_collaborator(repo_full_name: str, github_username: str, permission: str = "admin") -> None:
    """Invite a GitHub user (by username, not email -- GitHub has no email-based
    collaborator API) to the repo with the given permission level."""
    client = _get_client()
    repo = client.get_repo(repo_full_name)
    repo.add_to_collaborators(github_username, permission=permission)


def remove_collaborator(repo_full_name: str, github_username: str) -> None:
    """Remove a collaborator -- used to remove our own access after transfer."""
    client = _get_client()
    repo = client.get_repo(repo_full_name)
    repo.remove_from_collaborators(github_username)


def get_authenticated_username() -> str:
    return _get_client().get_user().login


def delete_repo(repo_full_name: str) -> None:
    """Permanently delete a repo. Destructive and irreversible -- used only
    by the opt-in integration test (tests/test_pipeline_real.py) to clean
    up the throwaway repo it creates, never by the normal pipeline flow."""
    client = _get_client()
    repo = client.get_repo(repo_full_name)
    repo.delete()
