from __future__ import annotations

from unittest.mock import Mock

from utils import vercel_api


def _response(payload: dict) -> Mock:
    response = Mock()
    response.json.return_value = payload
    return response


def test_get_public_production_url_prefers_primary_project_alias(monkeypatch):
    monkeypatch.setattr(
        vercel_api.requests,
        "get",
        Mock(
            return_value=_response(
                {
                    "targets": {
                        "production": {
                            "alias": [
                                "example-owner-projects.vercel.app",
                                "example.vercel.app",
                            ]
                        }
                    }
                }
            )
        ),
    )

    assert vercel_api._get_public_production_url("Example") == "https://example.vercel.app"


def test_get_public_production_url_falls_back_to_available_alias(monkeypatch):
    monkeypatch.setattr(
        vercel_api.requests,
        "get",
        Mock(
            return_value=_response(
                {"targets": {"production": {"alias": ["assigned.vercel.app"]}}}
            )
        ),
    )

    assert vercel_api._get_public_production_url("example") == "https://assigned.vercel.app"


def test_get_public_production_url_returns_none_without_alias(monkeypatch):
    monkeypatch.setattr(
        vercel_api.requests,
        "get",
        Mock(return_value=_response({"targets": {"production": {}}})),
    )

    assert vercel_api._get_public_production_url("example") is None


def test_deploy_files_returns_public_alias_and_keeps_generated_url(monkeypatch):
    monkeypatch.setattr(
        vercel_api.requests,
        "post",
        Mock(return_value=_response({"id": "dpl_123", "url": "example-abc.vercel.app"})),
    )
    monkeypatch.setattr(vercel_api, "_poll_until_ready", Mock(return_value="READY"))
    monkeypatch.setattr(
        vercel_api,
        "_get_public_production_url",
        Mock(return_value="https://example.vercel.app"),
    )

    result = vercel_api.deploy_files("Example", {"index.html": "hello"})

    assert result["url"] == "https://example.vercel.app"
    assert result["deployment_url"] == "https://example-abc.vercel.app"
    assert result["ready_state"] == "READY"
