"""Process invariants: argv list, no shell, allowlisted env, isolated temp."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.native_pipeline import StageFailed, run_native_pipeline
from tests2.native_pipeline._fakes import (
    DSN_ENV_VAR,
    DSN_VALUE,
    FakeToolchain,
    build_config,
    build_request,
)


pytestmark = [pytest.mark.functional, pytest.mark.security, pytest.mark.contract]


_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "app" / "core" / "native_pipeline"


def test_no_module_uses_a_shell() -> None:
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                if not (
                    isinstance(node.value, ast.Constant) and node.value.value is False
                ):
                    offenders.append(f"{path.name}: shell={ast.unparse(node.value)}")
            if isinstance(node, ast.Attribute) and node.attr in {
                "system",
                "popen",
                "getoutput",
            }:
                offenders.append(f"{path.name}: {node.attr}")
    assert offenders == []


def test_no_module_reads_raw_environment() -> None:
    """Env access stays behind Settings, per the app-wide boundary rule."""
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for needle in ("os.getenv", "os.environ.get", "environ["):
            if needle in source:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


def test_child_environment_is_allowlisted(
    fakes: FakeToolchain,
    workdir: Path,
    dsn_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLENORA_NATIVE_TEST_CANARY", "must-not-be-inherited")

    run_native_pipeline(build_request(workdir), build_config(fakes))

    for entry in fakes.invocations():
        assert "PLENORA_NATIVE_TEST_CANARY" not in entry["env"]


def test_dynamic_linker_path_is_explicitly_allowlisted(
    fakes: FakeToolchain,
    workdir: Path,
    dsn_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linker_path = "/opt/conda/envs/plenora/lib"
    monkeypatch.setenv("LD_LIBRARY_PATH", linker_path)

    run_native_pipeline(build_request(workdir), build_config(fakes))

    for entry in fakes.invocations():
        assert entry["env"].get("LD_LIBRARY_PATH") == linker_path


def test_only_the_database_stage_receives_the_dsn(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(build_request(workdir), build_config(fakes))

    for entry in fakes.invocations():
        has_dsn = DSN_ENV_VAR in entry["env"]
        is_database_stage = entry["tool"] == "database" and entry["args"][:1] != [
            "--version"
        ]
        assert has_dsn is is_database_stage
        if has_dsn:
            assert entry["env"][DSN_ENV_VAR] == DSN_VALUE


def test_identity_probe_never_receives_the_dsn(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(build_request(workdir), build_config(fakes))

    probes = [
        entry for entry in fakes.invocations() if entry["args"][:1] == ["--version"]
    ]
    assert probes
    for probe in probes:
        assert DSN_ENV_VAR not in probe["env"]


def test_child_temp_directories_point_at_the_isolated_workspace(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    run_native_pipeline(build_request(workdir), build_config(fakes))

    stage = fakes.stage_invocations()[0]
    scratch_values = {
        stage["env"].get(name) for name in ("TMPDIR", "TEMP", "TMP")
    } - {None}
    assert scratch_values, "no scratch directory was handed to the child"
    for value in scratch_values:
        assert "plenora-native-" in Path(value).as_posix()
        assert str(workdir) not in value


def test_stage_argv_entries_are_all_strings(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    result = run_native_pipeline(build_request(workdir), build_config(fakes))

    for stage in result.stages:
        assert isinstance(stage.argv, tuple)
        assert all(isinstance(arg, str) for arg in stage.argv)


def test_stage_failure_records_the_exit_code_and_stage(
    fakes: FakeToolchain, workdir: Path, dsn_env: str
) -> None:
    fakes.configure("data", exit_code=5, output_content=None)

    with pytest.raises(StageFailed) as excinfo:
        run_native_pipeline(build_request(workdir), build_config(fakes))

    assert excinfo.value.exit_code == 5
    assert excinfo.value.stage.value == "data.plan"
