from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from github import GithubException

from utils import github_api


def _github_exception(status: int) -> GithubException:
    return GithubException(status, data={}, headers={})


def test_push_files_updates_existing_files_with_sha():
    repo = Mock()
    repo.get_contents.return_value = SimpleNamespace(sha="abc123")

    github_api.push_files(repo, {"index.html": "<h1>Hello</h1>"}, commit_message="Update preview")

    repo.update_file.assert_called_once_with(
        path="index.html",
        message="Update preview: index.html",
        content="<h1>Hello</h1>",
        sha="abc123",
    )
    repo.create_file.assert_not_called()


def test_push_files_creates_missing_files():
    repo = Mock()
    repo.get_contents.side_effect = _github_exception(404)

    github_api.push_files(repo, {"style.css": "body{}"}, commit_message="Update preview")

    repo.create_file.assert_called_once_with(
        path="style.css",
        message="Update preview: style.css",
        content="body{}",
    )
    repo.update_file.assert_not_called()
