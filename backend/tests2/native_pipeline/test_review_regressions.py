"""Regression tests for the native pipeline security review."""

from __future__ import annotations

from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import sys
import time

import pytest

from app.core.native_pipeline import (
    DataPlanRejected,
    InvalidNativeRequest,
    MalformedStageOutput,
    NativePlatformUnsupported,
    OutputAlreadyExists,
    ProcessSpawnFailed,
    StageArtifactOverflow,
    StageFailed,
    ToolchainIdentityError,
    WorkspaceCleanupFailed,
    run_native_pipeline,
)
import app.core.native_pipeline.process as process_module
import app.core.native_pipeline.publication as publication_module
from app.core.native_pipeline.process import run_bounded_process
from app.core.native_pipeline.publication import prepare_artifact, publish_artifact
from app.core.native_pipeline.redaction import SecretRedactor
from app.core.native_pipeline.stages import PipelineStage
from tests2.native_pipeline._fakes import FakeToolchain, build_config, build_request


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


def test_atomic_link_race_preserves_competing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.arrow"
    destination = tmp_path / "final.arrow"
    source.write_bytes(b"ours")
    real_link = os.link

    def competing_link(staged: Path, final: Path) -> None:
        Path(final).write_bytes(b"competitor")
        real_link(staged, final)

    monkeypatch.setattr("app.core.native_pipeline.publication.os.link", competing_link)
    with pytest.raises(OutputAlreadyExists) as excinfo:
        publish_artifact(source, destination)

    assert getattr(excinfo.value, "code", None) == "output_already_exists"
    assert destination.read_bytes() == b"competitor"


@pytest.mark.skipif(os.name == "nt", reason="native runner is POSIX-only")
def test_exited_leader_with_pipe_inheriting_child_is_killed_bounded(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    parent = tmp_path / "parent"
    parent.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, subprocess, time\n"
        "p=subprocess.Popen(['sleep','30'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(p.pid))\n",
        encoding="utf-8",
    )
    parent.chmod(0o700)

    started = time.monotonic()
    outcome = run_bounded_process(
        (str(parent),), stage=PipelineStage.DATABASE_READ, env={"PATH": os.environ["PATH"]},
        cwd=tmp_path, timeout_ms=5_000, max_stdout_bytes=1024,
        max_stderr_bytes=1024, redactor=SecretRedactor(()),
    )
    elapsed = time.monotonic() - started

    assert outcome.exit_code == 0
    assert elapsed < 2
    pid = int(child_pid.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_reader_stuck_is_typed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_module._BoundedStreamReader, "is_alive", lambda self: True)
    with pytest.raises(ProcessSpawnFailed):
        run_bounded_process(
            (sys.executable, "-c", "print('ok')"),
            stage=PipelineStage.DATABASE_READ,
            env={"PATH": os.environ["PATH"]},
            cwd=tmp_path,
            timeout_ms=5_000,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
            redactor=SecretRedactor(()),
        )


def test_reader_exception_is_typed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_reader(reader: process_module._BoundedStreamReader) -> None:
        reader.failure = OSError("read failed")

    monkeypatch.setattr(process_module._BoundedStreamReader, "run", fail_reader)
    started = time.monotonic()
    with pytest.raises(ProcessSpawnFailed):
        run_bounded_process(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stage=PipelineStage.DATABASE_READ,
            env={"PATH": os.environ["PATH"]},
            cwd=tmp_path,
            timeout_ms=5_000,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
            redactor=SecretRedactor(()),
        )
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("kind", ["relative", "multi", "symlink", "sha_mismatch_path"])
def test_invalid_binary_binding_spawns_nothing(
    kind: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    executable = fakes.executable("database")
    pin = build_config(fakes).database
    if kind == "relative":
        pin = replace(pin, argv_prefix=(executable.name,))
    elif kind == "multi":
        pin = replace(pin, argv_prefix=(str(executable), "wrapper-arg"))
    elif kind == "symlink":
        link = executable.parent / "database-link"
        link.symlink_to(executable)
        pin = replace(pin, argv_prefix=(str(link),))
    else:
        pin = replace(
            pin, identity_mode="sha256", identity_path=fakes.executable("data"),
            expected_sha256="0" * 64,
        )
    with pytest.raises(ToolchainIdentityError):
        run_native_pipeline(build_request(workdir), build_config(fakes, database=pin))
    assert fakes.invocations() == []


def test_stage_argv_uses_private_binary_snapshots(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))
    originals = {str(fakes.executable(name)) for name in ("database", "data", "io")}
    assert all(stage.argv[0] not in originals for stage in result.stages)
    assert all("plenora-native-" in stage.argv[0] for stage in result.stages)


def test_publication_forces_private_mode(tmp_path: Path) -> None:
    source = tmp_path / "source.arrow"
    destination = tmp_path / "final.arrow"
    source.write_bytes(b"payload")
    source.chmod(0o666)

    publish_artifact(source, destination)

    assert destination.stat().st_mode & 0o777 == 0o600


def test_windows_platform_guard_precedes_spawn(
    fakes: FakeToolchain,
    workdir: Path,
    dsn_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.native_pipeline.pipeline.os.name", "nt")
    with pytest.raises(NativePlatformUnsupported):
        run_native_pipeline(build_request(workdir), build_config(fakes))
    assert fakes.invocations() == []


@pytest.mark.parametrize("bad_kind", ["symlink", "oversized"])
def test_plan_snapshot_rejects_unbounded_or_symlink_input_without_spawn(
    bad_kind: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    if bad_kind == "symlink":
        target = workdir / "real-plan.json"
        request.plan_path.replace(target)
        request.plan_path.symlink_to(target)
        config = build_config(fakes)
    else:
        config = build_config(fakes, max_plan_bytes=4)
    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(request, config)
    assert fakes.invocations() == []


@pytest.mark.parametrize("tool", ["data", "io"])
@pytest.mark.parametrize("bad_kind", ["symlink", "oversized"])
def test_invalid_stage_artifact_is_typed_and_never_published(
    tool: str, bad_kind: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    if bad_kind == "symlink":
        target = workdir / "target"
        target.write_bytes(b"not-stage-owned")
        fakes.configure(tool, output_content=None, output_symlink_target=str(target))
    else:
        fakes.configure(tool, output_content=None, output_size_bytes=33)
    request = build_request(workdir)
    request = replace(
        request, source=replace(request.source, max_output_bytes=32)
    )
    expected_error = StageArtifactOverflow if bad_kind == "oversized" else StageFailed
    with pytest.raises(expected_error):
        run_native_pipeline(
            request, build_config(fakes, max_stage_artifact_bytes=32)
        )
    assert not request.output_path.exists()


def test_request_output_bound_cannot_exceed_orchestrator_cap(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    request = replace(
        request,
        source=replace(request.source, max_output_bytes=65),
    )

    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(
            request, build_config(fakes, max_stage_artifact_bytes=64)
        )

    assert fakes.invocations() == []


@pytest.mark.parametrize("tool", ["data", "io"])
def test_stage_output_bound_is_enforced_while_child_is_running(
    tool: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        tool,
        output_content=None,
        output_size_bytes=65,
        post_output_sleep_ms=30_000,
    )
    request = build_request(workdir)
    request = replace(
        request, source=replace(request.source, max_output_bytes=64)
    )
    started = time.monotonic()

    with pytest.raises(StageArtifactOverflow) as excinfo:
        run_native_pipeline(
            request, build_config(fakes, max_stage_artifact_bytes=64)
        )

    assert excinfo.value.details["limit_bytes"] == 64
    assert time.monotonic() - started < 2
    assert not request.output_path.exists()


@pytest.mark.parametrize("remove", [False, True])
def test_plan_is_snapshotted_before_database_stage(
    remove: bool, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    original = request.plan_path.read_bytes()
    fakes.configure(
        "database", mutate_path=str(request.plan_path),
        remove_mutated_path=remove, mutate_content="attacker replacement",
    )
    run_native_pipeline(request, build_config(fakes))
    data_call = next(x for x in fakes.stage_invocations() if x["tool"] == "data")
    assert bytes.fromhex(data_call["plan_bytes"]) == original
    assert data_call["args"][data_call["args"].index("--plan") + 1] != str(request.plan_path)


@pytest.mark.parametrize(
    "row_diag",
    [
        {"contract": "wrong", "completeness": "complete", "index_basis": "source_row_zero_based", "total": 0, "observed_total": 0, "counts": {}, "examples": []},
        {"contract": "plenora-row-diagnostics-v1", "completeness": "partial", "index_basis": "source_row_zero_based", "total": 0, "observed_total": 0, "counts": {}, "examples": []},
        {"contract": "plenora-row-diagnostics-v1", "completeness": "complete", "index_basis": "source_row_zero_based", "total": 2, "observed_total": 1, "counts": {"x": 2}, "examples": []},
        {"contract": "plenora-row-diagnostics-v1", "completeness": "complete", "index_basis": "source_row_zero_based", "total": 1, "observed_total": 1, "counts": {"x": 1}, "examples": [{"source_index": -1, "cause": ""}]},
    ],
)
def test_invalid_row_diagnostics_fail_closed(
    row_diag: dict[str, object], fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", exit_code=2, output_content=None, stdout=json.dumps({"status": "error", "error": {"code": "data_mapping", "row_diagnostics": row_diag}}))
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


def test_data_mapping_exit_two_requires_row_diagnostics(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", exit_code=2, output_content=None, stdout=json.dumps({"status": "error", "error": {"code": "data_mapping"}}))
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


def test_data_mapping_diagnostics_marker_cannot_replace_row_document(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure(
        "data",
        exit_code=2,
        output_content=None,
        stdout=json.dumps(
            {
                "status": "error",
                "error": {"code": "data_mapping"},
                "diagnostics": [{"contract": "plenora-row-diagnostics-v1"}],
            }
        ),
    )
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


def test_data_exit_two_progress_preamble_preserves_complete_row_diagnostics(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    row_diag = {
        "contract": "plenora-row-diagnostics-v1",
        "completeness": "complete",
        "index_basis": "source_row_zero_based",
        "total": 1,
        "observed_total": 1,
        "counts": {"x": 1},
        "examples": [{"source_index": 4, "cause": "x"}],
    }
    report = json.dumps(
        {"status": "error", "error": {"code": "data_mapping", "row_diagnostics": row_diag}}
    )
    fakes.configure(
        "data", exit_code=2, output_content=None, stdout=f"progress\n{report}"
    )
    with pytest.raises(DataPlanRejected) as captured:
        run_native_pipeline(build_request(workdir), build_config(fakes))
    assert captured.value.diagnostics == (row_diag,)


def test_data_exit_two_progress_preamble_still_rejects_incomplete_document(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    report = json.dumps(
        {
            "status": "error",
            "error": {
                "code": "data_mapping",
                "row_diagnostics": {
                    "contract": "plenora-row-diagnostics-v1",
                    "completeness": "partial",
                    "index_basis": "source_row_zero_based",
                    "total": 1,
                    "observed_total": 1,
                    "counts": {"x": 1},
                    "examples": [],
                },
            },
        }
    )
    fakes.configure(
        "data", exit_code=2, output_content=None, stdout=f"progress\n{report}"
    )
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


@pytest.mark.parametrize(
    "payload",
    [
        '{"status":"error","error":{"code":"data_mapping","code":"invalid_plan"}}',
        '{"status":"error","error":{"code":"data_mapping","row_diagnostics":{"total":1e400}}}',
    ],
)
def test_data_exit_two_malformed_json_is_not_downgraded(
    payload: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", exit_code=2, output_content=None, stdout=payload)
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


@pytest.mark.parametrize("payload", ['{"value": NaN}', '{"value": 1e400}'])
def test_non_finite_stage_json_is_rejected(
    payload: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout=payload)
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


@pytest.mark.parametrize("field,value", [("schema", "public;drop"), ("table", "a.b"), ("order_by", "id desc")])
def test_invalid_identifiers_spawn_nothing(
    field: str, value: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    source = replace(request.source, **{field: value})
    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(replace(request, source=source), build_config(fakes))
    assert fakes.invocations() == []


def test_prepare_primary_error_is_not_masked_by_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.arrow"
    destination = tmp_path / "final.arrow"
    source.write_bytes(b"payload")

    def primary_fsync(fd: int) -> None:
        raise OSError("primary fsync failure")

    def cleanup_close(fd: int) -> None:
        raise OSError("secondary close failure")

    monkeypatch.setattr(publication_module.os, "fsync", primary_fsync)
    monkeypatch.setattr(publication_module.os, "close", cleanup_close)
    with pytest.raises(OSError, match="primary fsync failure"):
        prepare_artifact(source, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.part-*"))


def test_post_commit_cleanup_failure_does_not_turn_publication_into_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.arrow"
    destination = tmp_path / "final.arrow"
    source.write_bytes(b"ours")
    prepared = prepare_artifact(source, destination)

    monkeypatch.setattr(
        "app.core.native_pipeline.publication.PreparedPublication.discard",
        lambda self: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    monkeypatch.setattr(
        "app.core.native_pipeline.publication._fsync_directory",
        lambda directory: (_ for _ in ()).throw(OSError("fsync failed")),
    )

    assert prepared.commit() == 4
    assert destination.read_bytes() == b"ours"


def test_link_failure_discards_owned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.arrow"
    destination = tmp_path / "final.arrow"
    source.write_bytes(b"ours")
    prepared = prepare_artifact(source, destination)
    monkeypatch.setattr(
        "app.core.native_pipeline.publication.os.link",
        lambda source, target: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(PermissionError):
        prepared.commit()

    assert not prepared.staged.exists()
    assert not destination.exists()


def test_duplicate_json_keys_are_rejected(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("io", stdout='{"error":{"code":"data_mapping","code":"invalid_plan"}}')
    with pytest.raises(MalformedStageOutput):
        run_native_pipeline(build_request(workdir), build_config(fakes))


@pytest.mark.parametrize("reserved_name", ["TMPDIR", "LD_PRELOAD"])
def test_reserved_dsn_environment_name_spawns_nothing(
    reserved_name: str, fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    request = build_request(workdir)
    source = replace(request.source, dsn_env_var=reserved_name)
    with pytest.raises(InvalidNativeRequest):
        run_native_pipeline(replace(request, source=source), build_config(fakes))
    assert fakes.invocations() == []


def test_cleanup_failure_prevents_publication(
    fakes: FakeToolchain, workdir: Path, dsn_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = build_request(workdir)
    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EBUSY, "busy")
    monkeypatch.setattr("app.core.native_pipeline.workspace.shutil.rmtree", fail_cleanup)
    with pytest.raises(WorkspaceCleanupFailed) as excinfo:
        run_native_pipeline(request, build_config(fakes))
    assert getattr(excinfo.value, "code", None) == "workspace_cleanup_failed"
    assert not request.output_path.exists()
