"""Contracts for static Arricchisci API templates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.core.arricchisci.router as router
from app.core.arricchisci.async_poller import validate_async_config


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


TEMPLATES_FILE = Path(__file__).parents[3] / "app" / "data" / "api_templates.json"


def _templates() -> list[dict]:
    payload = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    return payload["templates"]


def test_api_templates_have_unique_ids_and_valid_async_configs() -> None:
    templates = _templates()
    ids = [template["_meta"]["id"] for template in templates]

    assert len(ids) == len(set(ids))
    for template in templates:
        async_config = template.get("config", {}).get("async")
        if async_config:
            validate_async_config(async_config)


def test_sister_async_template_is_config_only_generator() -> None:
    sister = next(
        template for template in _templates()
        if template["_meta"]["id"] == "sister-async-generator"
    )

    config = sister["config"]
    assert config["api_type"] == "generator"
    assert config["endpoint"]["method"] == "POST"
    assert config["request"] == {"body_type": "json"}
    assert config["async"]["enabled"] is True
    assert config["async"]["status_url"] == "{base}/jobs/{job_id}"
    assert config["async"]["result_from"] == "status_response"
    assert sister["output_mapping"]
    validate_async_config(config["async"])


def test_draft_api_connection_test_forwards_async_request_and_options(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _test_api_connection(*, config, test_params):
        captured["config"] = config
        captured["test_params"] = test_params
        return {"success": True}

    monkeypatch.setattr(router, "_validate_external_url", lambda _url: None)
    monkeypatch.setattr(
        router,
        "_api_connection_service",
        SimpleNamespace(test_api_connection=_test_api_connection),
    )

    result = router.test_api_config(
        {
            "url": "https://api.example.test/submit",
            "method": "POST",
            "auth": {"type": "none"},
            "custom_headers": {"X-Client": "plenora"},
            "request": {"body_type": "json"},
            "async": {
                "enabled": True,
                "job_id_path": "id",
                "status_url": "{base}/jobs/{job_id}",
            },
            "options": {"timeout": 120},
            "success_when": {"path": "status", "equals": "ok"},
            "error_path": "error",
            "test_params": {"payload": "{}"},
        },
        current_user=object(),
    )

    assert result == {"success": True}
    assert captured["test_params"] == {"payload": "{}"}
    assert captured["config"] == {
        "endpoint": {"url": "https://api.example.test/submit", "method": "POST"},
        "auth": {"type": "none"},
        "custom_headers": {"X-Client": "plenora"},
        "static_parameters": {},
        "options": {"timeout": 120},
        "request": {"body_type": "json"},
        "async": {
            "enabled": True,
            "job_id_path": "id",
            "status_url": "{base}/jobs/{job_id}",
        },
        "success_when": {"path": "status", "equals": "ok"},
        "error_path": "error",
    }
