"""Contratto dell'executor data-plane subprocess cross-platform."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _CaptureSendConnection:
    def __init__(self) -> None:
        self.envelopes: list[dict[str, object]] = []
        self.closed = False

    def send(self, envelope: dict[str, object]) -> None:
        self.envelopes.append(envelope)

    def close(self) -> None:
        self.closed = True


class _ReceiveConnection:
    def __init__(self, envelope: object = None, *, available: bool = True) -> None:
        self.envelope = envelope
        self.available = available
        self.closed = False

    def poll(self, _timeout: float = 0) -> bool:
        return self.available

    def recv(self) -> object:
        self.available = False
        return self.envelope

    def close(self) -> None:
        self.closed = True


class _SendConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, *, alive: bool, exitcode: int | None = 0) -> None:
        self._alive = alive
        self.exitcode = exitcode
        self.started = False
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        _ = timeout

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.killed = True
        self._alive = False
        self.exitcode = -9


class _StartFailureProcess(_FakeProcess):
    def start(self) -> None:
        raise OSError("process creation denied")


class _FakeContext:
    def __init__(self, receive: _ReceiveConnection, process: _FakeProcess) -> None:
        self.receive = receive
        self.send = _SendConnection()
        self.process = process
        self.process_kwargs: dict[str, object] = {}

    def Pipe(self, *, duplex: bool):
        assert duplex is False
        return self.receive, self.send

    def Process(self, **kwargs):
        self.process_kwargs = kwargs
        return self.process


class _ProcessConstructionFailureContext(_FakeContext):
    def Process(self, **kwargs):
        self.process_kwargs = kwargs
        raise OSError("process object construction denied")


def test_child_entry_returns_result_envelope(monkeypatch):
    from workers import data_plane_subprocess as executor

    connection = _CaptureSendConnection()

    def _body(self, **kwargs):
        return {"task_id": self.request.id, "value": kwargs["value"]}

    monkeypatch.setattr(executor, "_resolve_task_body", lambda _name: _body)
    executor._child_entry(
        connection,
        "pipeline.execute",
        "task-1",
        {"value": 7},
        "request-1",
    )

    assert connection.envelopes == [
        {"kind": "result", "result": {"task_id": "task-1", "value": 7}}
    ]
    assert connection.closed is True


def test_child_entry_serializes_crash(monkeypatch):
    from workers import data_plane_subprocess as executor

    connection = _CaptureSendConnection()

    def _body(_self, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(executor, "_resolve_task_body", lambda _name: _body)
    executor._child_entry(connection, "pipeline.execute", "task-2", {}, "")

    envelope = connection.envelopes[0]
    assert envelope["kind"] == "error"
    assert envelope["error_type"] == "RuntimeError"
    assert envelope["message"] == "boom"
    assert "RuntimeError: boom" in str(envelope["traceback"])


def test_child_entry_preserves_cooperative_revocation(monkeypatch):
    from app.core.orchestrator.task_progress import TaskRevoked
    from workers import data_plane_subprocess as executor

    connection = _CaptureSendConnection()

    def _body(_self, **_kwargs):
        raise TaskRevoked("task-revoked")

    monkeypatch.setattr(executor, "_resolve_task_body", lambda _name: _body)
    executor._child_entry(
        connection,
        "pipeline.execute",
        "task-revoked",
        {},
        "",
    )

    assert connection.envelopes == [
        {"kind": "revoked", "task_id": "task-revoked"}
    ]


def test_child_entry_routes_all_temp_files_to_task_directory(
    monkeypatch,
    tmp_path,
):
    from workers import data_plane_subprocess as executor

    connection = _CaptureSendConnection()
    task_temp = tmp_path / "task-temp"
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, "original-temp")

    def _body(_self, **_kwargs):
        return {
            "tempfile": tempfile.gettempdir(),
            "env": {name: os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")},
        }

    monkeypatch.setattr(executor, "_resolve_task_body", lambda _name: _body)
    executor._child_entry(
        connection,
        "pipeline.execute",
        "task-temp",
        {},
        "",
        str(task_temp),
    )

    result = connection.envelopes[0]["result"]
    assert result["tempfile"] == str(task_temp)
    assert set(result["env"].values()) == {str(task_temp)}
    assert task_temp.is_dir()


def test_child_entry_boots_with_real_spawn_context():
    """Smoke reale del boundary usato su Windows, Linux e container."""
    import multiprocessing as mp

    from workers.data_plane_subprocess import _child_entry

    context = mp.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_child_entry,
        args=(send, "unsupported.test-task", "spawn-smoke", {}, ""),
    )
    process.start()
    send.close()
    try:
        assert receive.poll(30), "child spawn non ha prodotto un envelope"
        envelope = receive.recv()
    finally:
        receive.close()
        process.join(timeout=30)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    assert envelope["kind"] == "error"
    assert envelope["error_type"] == "ValueError"
    assert "non supportato" in envelope["message"]


def test_parent_returns_child_result():
    from workers.data_plane_subprocess import execute_task_in_subprocess

    receive = _ReceiveConnection({"kind": "result", "result": {"ok": True}})
    process = _FakeProcess(alive=False, exitcode=0)
    context = _FakeContext(receive, process)

    result = execute_task_in_subprocess(
        task_name="pipeline.execute",
        task_id="task-3",
        kwargs={"project_id": "p1"},
        timeout_seconds=5,
        mp_context=context,
    )

    assert result == {"ok": True}
    assert process.started is True
    assert context.send.closed is True
    assert receive.closed is True
    assert context.process_kwargs["target"].__name__ == "_child_entry"
    task_temp = Path(context.process_kwargs["args"][-1])
    assert not task_temp.exists()


def test_parent_closes_pipe_when_process_start_fails():
    from workers.data_plane_subprocess import (
        DataPlaneChildError,
        execute_task_in_subprocess,
    )

    receive = _ReceiveConnection(available=False)
    context = _FakeContext(
        receive,
        _StartFailureProcess(alive=False, exitcode=None),
    )

    with pytest.raises(DataPlaneChildError, match="Impossibile avviare"):
        execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id="task-start-failure",
            kwargs={},
            timeout_seconds=5,
            mp_context=context,
        )

    assert receive.closed is True
    assert context.send.closed is True
    assert not Path(context.process_kwargs["args"][-1]).exists()


def test_parent_closes_pipe_when_process_construction_fails():
    from workers.data_plane_subprocess import execute_task_in_subprocess

    receive = _ReceiveConnection(available=False)
    context = _ProcessConstructionFailureContext(
        receive,
        _FakeProcess(alive=False, exitcode=None),
    )

    with pytest.raises(OSError, match="construction denied"):
        execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id="task-construction-failure",
            kwargs={},
            timeout_seconds=5,
            mp_context=context,
        )

    assert receive.closed is True
    assert context.send.closed is True
    task_temp = Path(context.process_kwargs["args"][-1])
    assert not task_temp.exists()

def test_parent_re_raises_revocation():
    from app.core.orchestrator.task_progress import TaskRevoked
    from workers.data_plane_subprocess import execute_task_in_subprocess

    context = _FakeContext(
        _ReceiveConnection({"kind": "revoked", "task_id": "task-4"}),
        _FakeProcess(alive=False, exitcode=0),
    )

    with pytest.raises(TaskRevoked) as exc_info:
        execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id="task-4",
            kwargs={},
            timeout_seconds=5,
            mp_context=context,
        )
    assert exc_info.value.task_id == "task-4"


def test_parent_raises_remote_crash_without_traceback_in_message():
    from workers.data_plane_subprocess import (
        DataPlaneChildError,
        execute_task_in_subprocess,
    )

    context = _FakeContext(
        _ReceiveConnection(
            {
                "kind": "error",
                "error_type": "ValueError",
                "message": "bad input",
                "traceback": "secret remote traceback",
            }
        ),
        _FakeProcess(alive=False, exitcode=1),
    )

    with pytest.raises(DataPlaneChildError, match="ValueError: bad input") as exc_info:
        execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id="task-5",
            kwargs={},
            timeout_seconds=5,
            mp_context=context,
        )
    assert "secret remote traceback" not in str(exc_info.value)


def test_parent_terminates_child_on_timeout(monkeypatch):
    from workers import data_plane_subprocess as executor

    receive = _ReceiveConnection(available=False)
    process = _FakeProcess(alive=True, exitcode=None)
    context = _FakeContext(receive, process)
    ticks = iter((0.0, 0.0, 2.0, 2.0))
    monkeypatch.setattr(executor.time, "monotonic", lambda: next(ticks, 2.0))

    with pytest.raises(executor.DataPlaneChildTimeout, match="timeout 1s"):
        executor.execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id="task-timeout",
            kwargs={},
            timeout_seconds=1,
            mp_context=context,
        )

    assert process.terminated is True
    assert process.is_alive() is False
    assert receive.closed is True
    assert not Path(context.process_kwargs["args"][-1]).exists()


def test_stale_task_temp_sweep_preserves_recent_directories(
    monkeypatch,
    tmp_path,
):
    from workers import data_plane_subprocess as executor

    stale = tmp_path / "plenora-data-stale"
    recent = tmp_path / "plenora-data-recent"
    stale.mkdir()
    recent.mkdir()
    old = time.time() - 10_000
    os.utime(stale, (old, old))
    monkeypatch.setattr(executor.tempfile, "gettempdir", lambda: str(tmp_path))

    executor._cleanup_stale_task_temp_dirs(older_than_seconds=3_900)

    assert not stale.exists()
    assert recent.is_dir()


def test_settings_default_to_memory_safe_subprocess_mode():
    from app.config import Settings

    assert Settings.DEFAULTS["DATA_PLANE_EXECUTION_MODE"] == "subprocess"
    assert Settings.DEFAULTS["DATA_PLANE_CHILD_TIMEOUT_SECONDS"] == 3900


def test_execute_and_preview_are_allowlisted_in_disposable_process():
    from workers import data_plane_subprocess as executor

    assert executor.SUPPORTED_TASKS == {"pipeline.execute", "pipeline.preview"}


@pytest.mark.parametrize("mode", ["inline", "subprocess", " SUBPROCESS "])
def test_startup_validation_accepts_supported_modes(mode):
    from app.startup_validation import validate_data_plane_config

    errors: list[str] = []
    validate_data_plane_config(
        {
            "DATA_PLANE_EXECUTION_MODE": mode,
            "DATA_PLANE_CHILD_TIMEOUT_SECONDS": "3900",
            "OBJECT_STORAGE_ENABLED": True,
            "OBJECT_STORAGE_BUCKET": "test-runtime",
        },
        errors,
    )
    assert errors == []


def test_startup_validation_rejects_invalid_mode_and_timeout():
    from app.startup_validation import validate_data_plane_config

    errors: list[str] = []
    validate_data_plane_config(
        {
            "DATA_PLANE_EXECUTION_MODE": "fork",
            "DATA_PLANE_CHILD_TIMEOUT_SECONDS": 0,
            "OBJECT_STORAGE_ENABLED": True,
            "OBJECT_STORAGE_BUCKET": "test-runtime",
        },
        errors,
    )
    assert len(errors) == 2
    assert "DATA_PLANE_EXECUTION_MODE" in errors[0]
    assert "DATA_PLANE_CHILD_TIMEOUT_SECONDS" in errors[1]


@pytest.mark.parametrize("intermediate_mode", ["preview_only", "full_pg"])
def test_startup_rejects_postgres_intermediates_on_s3_data_plane(intermediate_mode):
    from app.startup_validation import validate_data_plane_config

    errors: list[str] = []
    validate_data_plane_config(
        {
            "DATA_PLANE_EXECUTION_MODE": "subprocess",
            "DATA_PLANE_CHILD_TIMEOUT_SECONDS": 3900,
            "INTERMEDIATE_PERSISTENCE_MODE": intermediate_mode,
            "OBJECT_STORAGE_ENABLED": True,
            "OBJECT_STORAGE_BUCKET": "test-runtime",
        },
        errors,
    )
    assert errors and "salva righe in PostgreSQL" in errors[0]


def test_actor_contains_subprocess_feature_flag_contract():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "workers" / "dramatiq_app.py"
    ).read_text(encoding="utf-8")
    assert 'settings.get("DATA_PLANE_EXECUTION_MODE", "subprocess")' in source
    assert "execute_task_in_subprocess" in source
    assert 'task_name="pipeline.execute"' in source
    assert 'task_name="pipeline.preview"' in source
