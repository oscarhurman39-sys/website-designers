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

import config

_client: Optional[Github] = None


def _get_client() -> Github:
    global _client
    if _client is None:
        if not config.GITHUB_TOKEN:
            raise RuntimeError("GITHUB_TOKEN is not configured.")
        _client = Github(config.GITHUB_TOKEN)
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
    """Create or update each file in `files` (path -> text content).

    PyGithub's Contents API creates one commit per file (there is no native
    multi-file commit helper), which is fine for a handful of small template
    files like index.html/style.css.

    Upserts rather than create-only: a lead that bounces back to
    'researched' (blocked email, dashboard bounce-retry) gets re-designed
    into the same repo, and create_file against an existing path fails with
    422 because the Contents API requires the current blob sha to update.
    """
    for path, content in files.items():
        try:
            repo.create_file(path=path, message=f"{commit_message}: {path}", content=content)
        except GithubException as exc:
            if exc.status not in (409, 422):  # anything but "file already exists"
                raise
            existing = repo.get_contents(path)
            repo.update_file(
                path=path,
                message=f"Update preview site: {path}",
                content=content,
                sha=existing.sha,
            )


def create_repo_with_files(
    business_name: str,
    lead_id: int,
    files: dict[str, str],
) -> tuple[Repository, str, str]:
    """Convenience wrapper: create the repo and push all template files.

    Returns (repo_object, html_url, full_name).
    """
    repo_name = make_repo_name(business_name, lead_id)
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
