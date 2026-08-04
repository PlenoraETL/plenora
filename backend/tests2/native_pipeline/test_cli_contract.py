"""The backend CLI is the production entry point: JSON out, no secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.native_pipeline.cli import _emit, main
from tests2.native_pipeline._fakes import (
    DSN_ENV_VAR,
    DSN_VALUE,
    IO_PAYLOAD,
    FakeToolchain,
    build_fake_toolchain,
)


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli_env(
    fakes: FakeToolchain, monkeypatch: pytest.MonkeyPatch, dsn_env: str
) -> None:
    monkeypatch.setenv("NATIVE_PIPELINE_ENABLED", "true")
    monkeypatch.setenv("NATIVE_PIPELINE_DATABASE_IDENTITY_MODE", "version")
    monkeypatch.setenv("NATIVE_PIPELINE_DATA_IDENTITY_MODE", "version")
    monkeypatch.setenv("NATIVE_PIPELINE_IO_IDENTITY_MODE", "version")
    monkeypatch.setenv(
        "NATIVE_PIPELINE_DATABASE_ARGV", json.dumps(list(fakes.argv_prefix("database")))
    )
    monkeypatch.setenv(
        "NATIVE_PIPELINE_DATA_ARGV", json.dumps(list(fakes.argv_prefix("data")))
    )
    monkeypatch.setenv(
        "NATIVE_PIPELINE_IO_ARGV", json.dumps(list(fakes.argv_prefix("io")))
    )


def _run_args(workdir: Path, output_name: str = "cli.arrow") -> list[str]:
    plan_path = workdir / "plan.json"
    plan_path.write_text(json.dumps({"version": 1, "ops": []}), encoding="utf-8")
    return [
        "run",
        "--dsn-env",
        DSN_ENV_VAR,
        "--schema",
        "public",
        "--table",
        "misure",
        "--plan",
        str(plan_path),
        "--output",
        str(workdir / output_name),
        "--max-rows",
        "1000",
        "--order-by",
        "id",
    ]


def test_run_emits_a_machine_readable_success_document(
    cli_env: None, workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(_run_args(workdir))

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["result"]["capability"] == "postgres.read"
    assert payload["result"]["artifact_path"] == str(workdir / "cli.arrow")
    assert (workdir / "cli.arrow").read_bytes() == IO_PAYLOAD.encode("utf-8")


def test_run_output_never_contains_the_dsn(
    cli_env: None,
    fakes: FakeToolchain,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes.configure("database", echo_env=[DSN_ENV_VAR])
    fakes.configure("io", stdout=json.dumps({"note": f"src={DSN_VALUE}"}))

    main(_run_args(workdir))

    captured = capsys.readouterr()
    assert DSN_VALUE not in captured.out
    assert "sup3r-s3cret" not in captured.out
    assert DSN_VALUE not in captured.err


def test_unsupported_capability_exits_non_zero_with_a_typed_code(
    cli_env: None,
    fakes: FakeToolchain,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([*_run_args(workdir), "--source", "mysql"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "unsupported_capability"
    assert fakes.invocations() == []


def test_write_operation_exits_non_zero(
    cli_env: None,
    fakes: FakeToolchain,
    workdir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([*_run_args(workdir), "--operation", "write"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["error"]["code"] == "unsupported_capability"
    assert fakes.invocations() == []


def test_existing_output_exits_non_zero(
    cli_env: None, workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _run_args(workdir)
    (workdir / "cli.arrow").write_bytes(b"existing")

    exit_code = main(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["error"]["code"] == "output_already_exists"
    assert (workdir / "cli.arrow").read_bytes() == b"existing"


def test_disabled_by_default_without_opt_in(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NATIVE_PIPELINE_ENABLED", raising=False)
    monkeypatch.setenv(DSN_ENV_VAR, DSN_VALUE)

    exit_code = main(_run_args(workdir))

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["error"]["code"] == "native_pipeline_disabled"


def test_capabilities_subcommand_documents_the_matrix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["capabilities"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    entries = {entry["capability"]: entry for entry in payload["capabilities"]}
    assert entries["postgres.read"]["supported"] is True
    assert entries["mysql.read"]["supported"] is False
    assert entries["postgres.write"]["supported"] is False
    assert entries["mysql.read"]["reason"].strip()


def test_module_entry_point_runs_as_a_real_process(
    fakes: FakeToolchain, workdir: Path, tmp_path: Path
) -> None:
    env = dict(os.environ)
    env.update(
        {
            "NATIVE_PIPELINE_ENABLED": "true",
            "NATIVE_PIPELINE_DATABASE_IDENTITY_MODE": "version",
            "NATIVE_PIPELINE_DATA_IDENTITY_MODE": "version",
            "NATIVE_PIPELINE_IO_IDENTITY_MODE": "version",
            "NATIVE_PIPELINE_DATABASE_ARGV": json.dumps(
                list(fakes.argv_prefix("database"))
            ),
            "NATIVE_PIPELINE_DATA_ARGV": json.dumps(list(fakes.argv_prefix("data"))),
            "NATIVE_PIPELINE_IO_ARGV": json.dumps(list(fakes.argv_prefix("io"))),
            DSN_ENV_VAR: DSN_VALUE,
            "PYTHONPATH": str(_BACKEND_ROOT),
        }
    )
    process = subprocess.run(
        [sys.executable, "-m", "app.core.native_pipeline", *_run_args(workdir)],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert process.returncode == 0, process.stderr[-2_000:]
    payload = json.loads(process.stdout)
    assert payload["status"] == "ok"
    assert DSN_VALUE not in process.stdout
    assert (workdir / "cli.arrow").is_file()


def test_second_run_over_the_same_output_fails(
    fakes: FakeToolchain, workdir: Path, cli_env: None
) -> None:
    assert main(_run_args(workdir)) == 0
    assert main(_run_args(workdir)) != 0


def test_filesystem_failure_still_produces_a_json_envelope(
    cli_env: None,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CLI run never exits with a bare traceback on stdout."""

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        "app.core.native_pipeline.cli.run_native_pipeline", _explode
    )

    exit_code = main(_run_args(workdir))

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "io_error"


def test_unknown_subcommand_is_rejected(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["frobnicate"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["code"] == "invalid_arguments"


def test_argparse_error_is_json_from_real_process() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_ROOT)
    process = subprocess.run(
        [sys.executable, "-m", "app.core.native_pipeline", "frobnicate"],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode != 0
    assert process.stderr == ""
    assert json.loads(process.stdout)["error"]["code"] == "invalid_arguments"


def test_emit_is_ascii_safe_and_strict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _emit({"status": "ok", "message": "città"})
    raw = capsys.readouterr().out
    assert raw.isascii()
    assert json.loads(raw)["message"] == "città"


def test_emit_nonfinite_payload_returns_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _emit({"status": "ok", "value": float("inf")})
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "serialization_error"


def test_fake_toolchain_helper_is_reusable(tmp_path: Path) -> None:
    fakes = build_fake_toolchain(tmp_path / "tools")

    assert fakes.script.is_file()
    assert fakes.invocations() == []
