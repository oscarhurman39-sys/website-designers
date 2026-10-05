"""Preview hosting on GitHub Pages (PREVIEW_HOST=github_pages).

Vercel Hobby stops at 100 deployments a day and 200 projects. Here every
preview is a folder in ONE public repository served by GitHub Pages.
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Optional

from github import GithubException, InputGitTreeElement
from github.Repository import Repository

import config
from utils import github_api

MARKER = "ghpages:"

_BUILD_STAMP_FILE = "build.txt"
_PUBLISH_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 5
_NOINDEX_META = '<meta name="robots" content="noindex, nofollow">'

_repo: Optional[Repository] = None
_base_url: Optional[str] = None


def enabled() -> bool:
    return config.PREVIEW_HOST == "github_pages"


def host_ref(slug: str) -> str:
    return f"{MARKER}{slug}"


def slug_of(website: Optional[dict]) -> Optional[str]:
    """The folder a website row is published under, or None for a Vercel one."""
    ref = (website or {}).get("vercel_project_id") or ""
    return ref[len(MARKER):] if ref.startswith(MARKER) else None


def _get_repo() -> Repository:
    global _repo
    if _repo is not None:
        return _repo
    user = github_api._get_client().get_user()
    try:
        repo = user.get_repo(config.GITHUB_PAGES_REPO)
    except GithubException as exc:
        if exc.status != 404:
            raise
        repo = user.create_repo(
            name=config.GITHUB_PAGES_REPO,
            private=False,
            description="Draft website previews",
            auto_init=True,
        )
        repo.create_file(".nojekyll", "Serve files as they are", "")
        repo.create_file("index.html", "Blank front page", f"<!doctype html>{_NOINDEX_META}<title>Previews</title>")
        try:
            repo._requester.requestJsonAndCheck(
                "POST", f"{repo.url}/pages",
                input={"source": {"branch": repo.default_branch, "path": "/"}},
            )
        except GithubException as e:
            if e.status != 409:
                raise
    _repo = repo
    return repo


def base_url() -> str:
    global _base_url
    if _base_url is None:
        repo = _get_repo()
        _base_url = f"https://{repo.owner.login.lower()}.github.io/{repo.name}".rstrip("/")
    return _base_url


def preview_url(slug: str) -> str:
    return f"{base_url()}/{slug}/"


def _with_noindex(path: str, content):
    if not path.endswith(".html") or not isinstance(content, str) or 'name="robots"' in content:
        return content
    return content.replace("<head", f"<head>{_NOINDEX_META}", 1)


def _commit(repo: Repository, elements: list, message: str) -> None:
    ref = repo.get_git_ref(f"heads/{repo.default_branch}")
    parent = repo.get_git_commit(ref.object.sha)
    tree = repo.create_git_tree(elements, base_tree=parent.tree)
    commit = repo.create_git_commit(message, tree, [parent])
    ref.edit(commit.sha)


def _wait_until_served(url: str, stamp: str) -> None:
    deadline = time.monotonic() + _PUBLISH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            import requests
            resp = requests.get(f"{url}{_BUILD_STAMP_FILE}", params={"v": stamp}, timeout=10)
            if resp.status_code == 200 and resp.text.strip() == stamp:
                return
        except Exception:
            pass
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"GitHub Pages did not publish {url} within {_PUBLISH_TIMEOUT_SECONDS} s")


def publish(slug: str, files: dict) -> str:
    repo = _get_repo()
    stamp = uuid.uuid4().hex
    elements = []
    for path, content in {**files, _BUILD_STAMP_FILE: stamp}.items():
        content = _with_noindex(path, content)
        if isinstance(content, bytes):
            blob = repo.create_git_blob(base64.b64encode(content).decode("ascii"), "base64")
        else:
            blob = repo.create_git_blob(content, "utf-8")
        elements.append(InputGitTreeElement(f"{slug}/{path}", "100644", "blob", sha=blob.sha))
    _commit(repo, elements, f"Publish preview {slug}")
    url = preview_url(slug)
    _wait_until_served(url, stamp)
    return url


def remove(slug: str) -> None:
    repo = _get_repo()
    tree = repo.get_git_tree(repo.default_branch, recursive=True)
    doomed = [item.path for item in tree.tree if item.type == "blob" and item.path.startswith(f"{slug}/")]
    if not doomed:
        return
    _commit(
        repo,
        [InputGitTreeElement(path, "100644", "blob", sha=None) for path in doomed],
        f"Remove preview {slug}",
    )
