from __future__ import annotations

import pytest

from app.core.carica.rest_config_parser import parse_carica_rest_config
from app.core.connections.rest_sink import RestSinkBatchMode, RestSinkMethod


def test_carica_rest_config_parser_builds_typed_sink_config() -> None:
    parsed = parse_carica_rest_config(
        {
            "dest_type": "rest",
            "rest_config": {
                "url": "https://api.example.com/features",
                "method": "patch",
                "headers": [
                    {"name": "Authorization", "value": "Bearer token-1", "secret": True},
                    {"name": "X-Trace", "value": "trace-1"},
                ],
                "body_mapping": {
                    "fields": [
                        {"source": "id", "target": "external_id", "required": True},
                        {"source_column": "name", "dest_column": "label", "default": ""},
                    ],
                    "include_unmapped": False,
                    "batch_records_key": "items",
                },
                "batch_mode": "per_record",
                "batch_size": "25",
                "timeout_seconds": "12.5",
                "success_statuses": [200, "204"],
            },
        }
    )

    config = parsed.sink_config
    assert config.url == "https://api.example.com/features"
    assert config.method is RestSinkMethod.PATCH
    assert config.batch_mode is RestSinkBatchMode.PER_RECORD
    assert config.batch_size == 25
    assert config.timeout_seconds == 12.5
    assert config.success_statuses == (200, 204)
    assert config.headers[0].name == "Authorization"
    assert config.headers[0].value == "Bearer token-1"
    assert config.headers[0].secret is True
    assert config.body_mapping.include_unmapped is False
    assert config.body_mapping.batch_records_key == "items"
    assert config.body_mapping.fields[0].source == "id"
    assert config.body_mapping.fields[0].target == "external_id"
    assert config.body_mapping.fields[0].required is True
    assert config.body_mapping.fields[1].source == "name"
    assert config.body_mapping.fields[1].target == "label"


def test_carica_rest_config_parser_rejects_invalid_url_fail_closed() -> None:
    with pytest.raises(ValueError, match="url must be absolute"):
        parse_carica_rest_config({"rest_config": {"url": "/relative/path"}})

    with pytest.raises(ValueError, match="url must not include credentials"):
        parse_carica_rest_config(
            {"rest_config": {"url": "https://user:pass@api.example.com/features"}}
        )


def test_carica_rest_config_parser_blocks_loopback_url(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="IP privato/interno"):
        parse_carica_rest_config(
            {"rest_config": {"url": "http://127.0.0.1:9000/internal"}}
        )


def test_carica_rest_config_parser_rejects_invalid_method_and_header_shape() -> None:
    with pytest.raises(ValueError, match="method must be one of"):
        parse_carica_rest_config(
            {"rest_config": {"url": "https://api.example.com/features", "method": "GET"}}
        )

    with pytest.raises(ValueError, match="headers must be an object or list"):
        parse_carica_rest_config(
            {"rest_config": {"url": "https://api.example.com/features", "headers": "bad"}}
        )

    with pytest.raises(ValueError, match="header name is invalid"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "headers": {"Bad Header": "value"},
                }
            }
        )


def test_carica_rest_config_parser_redacts_secret_header_metadata() -> None:
    parsed = parse_carica_rest_config(
        {
            "rest_url": "https://api.example.com/features",
            "headers": {
                "Authorization": "Bearer secret-token",
                "X-Api-Key": "api-secret",
                "X-Client": "plenora",
            },
            "secret_headers": ["authorization", "X-Api-Key"],
        }
    )

    assert parsed.redacted_headers == {
        "Authorization": "***REDACTED***",
        "X-Api-Key": "***REDACTED***",
        "X-Client": "plenora",
    }
    assert parsed.sink_config.headers[0].value == "Bearer secret-token"
    assert "secret-token" not in repr(parsed.redacted_headers)
    assert "api-secret" not in repr(parsed.redacted_headers)


def test_carica_rest_config_parser_rejects_header_injection() -> None:
    with pytest.raises(ValueError, match="header value cannot contain newlines"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "headers": {"Authorization": "Bearer token\r\nX-Evil: yes"},
                }
            }
        )


def test_carica_rest_config_parser_rejects_invalid_body_mapping_shape() -> None:
    with pytest.raises(ValueError, match="body_mapping.fields must be a list"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "body_mapping": {"fields": {"source": "id", "target": "id"}},
                }
            }
        )

    with pytest.raises(ValueError, match="default must be a scalar"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "body_mapping": {
                        "fields": [
                            {
                                "source": "id",
                                "target": "id",
                                "default": {"nested": "not-supported"},
                            }
                        ]
                    },
                }
            }
        )


def test_carica_rest_config_parser_rejects_invalid_success_statuses() -> None:
    with pytest.raises(ValueError, match="success_statuses cannot be empty"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "success_statuses": [],
                }
            }
        )

    with pytest.raises(ValueError, match="success status must be a valid HTTP status"):
        parse_carica_rest_config(
            {
                "rest_config": {
                    "url": "https://api.example.com/features",
                    "success_statuses": [99],
                }
            }
        )
