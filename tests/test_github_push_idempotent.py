"""Regression test: push_files() must survive a retried deploy.

Scenario it guards: DesignAgent pushes template files to GitHub, then the
Vercel deploy fails once. On the next cycle create_repo() idempotently
returns the existing repo, so create_file() answers 422 ("file already
exists"). push_files() must update the file in place instead of raising --
otherwise the lead retries and fails identically forever.

Runs entirely against mocks: no credentials, no network.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from github import GithubException

from utils import github_api


def _existing_file_422(*, existing_paths: set[str]) -> MagicMock:
    repo = MagicMock()

    def create_file(path, message, content):
        if path in existing_paths:
            raise GithubException(422, {"message": f"{path} already exists"}, None)
        return None

    repo.create_file.side_effect = create_file
    repo.get_contents.return_value = MagicMock(sha="abc123")
    return repo


def test_push_files_updates_existing_file_on_422():
    repo = _existing_file_422(existing_paths={"index.html"})

    github_api.push_files(repo, {"index.html": "<html></html>", "style.css": "body{}"})

    # index.html hit 422 and was updated in place with the existing blob's sha...
    repo.update_file.assert_called_once()
    assert repo.update_file.call_args.kwargs["sha"] == "abc123"
    assert repo.update_file.call_args.kwargs["path"] == "index.html"
    # ...and style.css (not yet existing) was still created normally.
    created_paths = [c.kwargs["path"] for c in repo.create_file.call_args_list]
    assert created_paths == ["index.html", "style.css"]


def test_push_files_reraises_non_422_errors():
    repo = MagicMock()
    repo.create_file.side_effect = GithubException(403, {"message": "forbidden"}, None)

    with pytest.raises(GithubException):
        github_api.push_files(repo, {"index.html": "<html></html>"})

    repo.update_file.assert_not_called()
