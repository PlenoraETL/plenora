"""The DSN value must never surface in argv, reports, errors or logs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.native_pipeline import StageFailed, run_native_pipeline
from app.core.native_pipeline.redaction import REDACTED, SecretRedactor
from tests2.native_pipeline._fakes import (
    DSN_ENV_VAR,
    DSN_VALUE,
    FakeToolchain,
    build_config,
    build_request,
)


pytestmark = [pytest.mark.functional, pytest.mark.security]


_SECRET_FRAGMENT = "sup3r-s3cret"


def test_redactor_masks_the_configured_secret() -> None:
    redactor = SecretRedactor((DSN_VALUE,))

    assert _SECRET_FRAGMENT not in redactor.redact(f"connected to {DSN_VALUE}")
    assert REDACTED in redactor.redact(f"connected to {DSN_VALUE}")


def test_redactor_masks_dsn_shaped_text_without_prior_knowledge() -> None:
    redactor = SecretRedactor(("pa",))
    text = "dsn=postgresql://other:hunter2@host:5432/db password=abc123 done"

    redacted = redactor.redact(text)

    assert "hunter2" not in redacted
    assert "host:5432/db" not in redacted
    assert "abc123" not in redacted
    assert redacted.count(REDACTED) >= 2
    assert "done" in redacted


def test_redactor_masks_json_escaped_url_before_parsing() -> None:
    raw = r'{"note":"postgresql:\/\/user:hunter2@host:5432\/db"}'
    redacted = SecretRedactor(("hunter",)).redact(raw)
    parsed = json.loads(redacted)

    assert "hunter2" not in redacted
    assert "host:5432" not in redacted
    assert parsed["note"] == f"postgresql://{REDACTED}"


def test_redactor_ignores_empty_secrets() -> None:
    redactor = SecretRedactor(("", "   "))

    assert redactor.redact("plain text") == "plain text"


def test_stage_failure_message_is_redacted(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "database", exit_code=9, output_content=None, echo_env=[DSN_ENV_VAR]
    )
    request = build_request(workdir)

    with pytest.raises(StageFailed) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    rendered = f"{excinfo.value} {json.dumps(dict(excinfo.value.details))}"
    assert _SECRET_FRAGMENT not in rendered
    assert DSN_VALUE not in rendered
    assert REDACTED in rendered


def test_stage_reports_are_redacted(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("database", echo_env=[DSN_ENV_VAR])
    fakes.configure(
        "io", stdout=json.dumps({"note": f"read from {DSN_VALUE}", "loss": []})
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    rendered = json.dumps(result.to_json_mapping())
    assert _SECRET_FRAGMENT not in rendered
    assert DSN_VALUE not in rendered


def test_secret_is_never_placed_on_the_command_line(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(build_request(workdir), build_config(fakes))

    for entry in fakes.invocations():
        assert all(DSN_VALUE not in str(arg) for arg in entry["args"])
        assert all(_SECRET_FRAGMENT not in str(arg) for arg in entry["args"])

    database_stage = fakes.stage_invocations()[0]
    assert DSN_ENV_VAR in database_stage["args"]


def test_result_argv_carries_no_secret(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    for stage in result.stages:
        assert all(DSN_VALUE not in arg for arg in stage.argv)
