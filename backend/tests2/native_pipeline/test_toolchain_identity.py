"""Identity and version of every pinned binary must be proven before use."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json

import pytest

from app.core.native_pipeline import (
    PinnedBinary,
    ToolchainIdentityError,
    run_native_pipeline,
)
from tests2.native_pipeline._fakes import (
    DATABASE_IDENTITY,
    FakeToolchain,
    build_config,
    build_request,
)


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


def test_version_mismatch_fails_closed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", version_line="plenora-data-tools 1.0.1")
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError) as excinfo:
        run_native_pipeline(request, build_config(fakes))

    assert excinfo.value.code == "toolchain_identity"
    assert not request.output_path.exists()
    assert fakes.stage_invocations() == []


def test_identity_mismatch_fails_closed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", version_line="some-other-tool 1.0.1")
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()
    assert fakes.stage_invocations() == []


def test_unparseable_version_output_fails_closed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("database", version_line="")
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()


def test_non_zero_version_exit_fails_closed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("database", version_exit=3)
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError):
        run_native_pipeline(request, build_config(fakes))

    assert not request.output_path.exists()


def test_missing_binary_fails_closed(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    config = build_config(
        fakes,
        database=PinnedBinary(
            argv_prefix=(str(workdir / "does-not-exist"),),
            identity=DATABASE_IDENTITY,
            version="1.1.0",
        ),
    )
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError):
        run_native_pipeline(request, config)

    assert not request.output_path.exists()


def test_unconfigured_binary_fails_closed_without_spawn(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    config = build_config(
        fakes,
        io=PinnedBinary(argv_prefix=(), identity="plenora-io", version="1.0.1"),
    )
    request = build_request(workdir)

    with pytest.raises(ToolchainIdentityError) as excinfo:
        run_native_pipeline(request, config)

    assert excinfo.value.details["reason"] == "argv_prefix_empty"
    assert fakes.invocations() == []


def test_sha256_identity_succeeds_without_version_spawn(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    digest = hashlib.sha256(fakes.script.read_bytes()).hexdigest()
    config = build_config(
        fakes,
        database=PinnedBinary(
            argv_prefix=fakes.argv_prefix("database"),
            identity=DATABASE_IDENTITY,
            version="1.1.0",
            identity_mode="sha256",
            identity_path=fakes.script,
            expected_sha256=digest,
        ),
    )

    run_native_pipeline(build_request(workdir), config)

    database_probes = [
        entry
        for entry in fakes.invocations()
        if entry["tool"] == "database" and entry["args"][:1] == ["--version"]
    ]
    assert database_probes == []


def test_sha256_mismatch_fails_before_any_spawn(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    config = build_config(
        fakes,
        database=PinnedBinary(
            argv_prefix=fakes.argv_prefix("database"),
            identity=DATABASE_IDENTITY,
            version="1.1.0",
            identity_mode="sha256",
            identity_path=fakes.script,
            expected_sha256="0" * 64,
        ),
    )

    with pytest.raises(ToolchainIdentityError) as excinfo:
        run_native_pipeline(build_request(workdir), config)

    assert excinfo.value.details["reason"] == "sha256_mismatch"
    assert fakes.invocations() == []


def test_io_json_version_document_is_accepted(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "io",
        version_line=json.dumps(
            {"status": "ok", "identity": "plenora-io", "version": "1.0.1"}
        ),
    )

    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    assert result.toolchain["io"] == "1.0.1"


def test_identity_is_probed_for_all_three_binaries_before_any_stage(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(build_request(workdir), build_config(fakes))

    probes = [
        entry for entry in fakes.invocations() if entry["args"][:1] == ["--version"]
    ]
    assert [entry["tool"] for entry in probes] == ["database", "data", "io"]
    first_stage_index = next(
        index
        for index, entry in enumerate(fakes.invocations())
        if entry["args"][:1] != ["--version"]
    )
    assert first_stage_index == 3
