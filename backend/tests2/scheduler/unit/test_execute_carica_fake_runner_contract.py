"""
Contratto fake-runner per la scrittura CARICA schedulata (G4 roadmap).

`ExecutionManager` non importa piu' `carica.executor`: costruisce il writer
via factory composition-root (`build_scheduled_carica_executor`), iniettabile
dai test. Qui un fake runner attraversa `_execute_carica` end-to-end e blinda:

- la factory riceve la ConnectionService della manager (identita');
- l'executor riceve node_data derivato dalla destination dello schedule,
  triggered_by="scheduler" e run_id None senza scheduled_for;
- il risultato e' mappato su SchedulerCaricaResult.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from app.core.carica.models import WriteStatus
from app.core.scheduler.execution_manager import ExecutionManager

pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _disable_concurrency_table_bootstrap(monkeypatch):
    """ExecutionManager unit tests must not open a real PostgreSQL pool."""
    monkeypatch.setattr(
        "app.core.scheduler.concurrency_manager.ConcurrencyManager._ensure_table",
        lambda _self: None,
    )


class _FakeCaricaExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            status=WriteStatus.SUCCESS,
            rows_written=3,
            rows_inserted=2,
            rows_updated=1,
            rows_skipped=0,
            error_message=None,
        )


class _Materialized:
    """Context manager minimale con il df inline gia' risolto."""

    def __enter__(self):
        return SimpleNamespace(
            df=pd.DataFrame({"a": [1, 2, 3]}), data=None, path=None, format=None
        )

    def __exit__(self, *exc) -> bool:
        return False


def _schedule_config() -> SimpleNamespace:
    return SimpleNamespace(
        id="sched-1",
        destination=SimpleNamespace(
            connection_id="conn-1",
            schema_name="public",
            table_name="dest_table",
            write_mode="append",
            match_columns=None,
            batch_size=500,
        ),
        pipeline=SimpleNamespace(project_id="proj-1"),
    )


def test_execute_carica_runs_through_injected_fake_runner(monkeypatch):
    fake_executor = _FakeCaricaExecutor()
    factory_calls: list[dict] = []
    lock_calls: dict[str, object] = {}
    connection_sentinel = object()

    def fake_factory(**kwargs):
        factory_calls.append(kwargs)
        return fake_executor

    manager = ExecutionManager(
        connection_service=connection_sentinel,
        carica_executor_factory=fake_factory,
    )

    fake_artifact = SimpleNamespace(
        artifact_kind="run",
        to_run_artifact_ref=lambda: object(),
    )
    target = SimpleNamespace(key="carica:db:test", kind="db")
    handle = SimpleNamespace(target=target, owner_token="scheduler-test")

    def resolve_lock(**kwargs):
        lock_calls["resolve"] = kwargs
        return target

    def acquire_lock(**kwargs):
        lock_calls["acquire"] = kwargs
        return handle

    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.require_carica_target_lock",
        resolve_lock,
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.acquire_carica_target_lock",
        acquire_lock,
    )

    with patch(
        "app.core.scheduler.execution_manager.artifact_module.PipelineArtifact"
    ) as artifact_cls, patch(
        "app.core.scheduler.execution_manager.artifact_module.ArtifactResolver"
    ) as resolver_cls:
        artifact_cls.from_executor_result.return_value = fake_artifact
        resolver_cls.return_value.materialize_run.return_value = _Materialized()

        result = manager._execute_carica(
            _schedule_config(),
            pipeline_result={"success": True},
            group_id="group-1",
            scheduled_for=None,
        )

    # La factory composition-root riceve la ConnectionService iniettata.
    assert factory_calls == [{"connection_service": connection_sentinel}]
    assert lock_calls["resolve"]["group_id"] == "group-1"
    assert lock_calls["acquire"]["target"] is target

    # L'executor riceve la destination dello schedule, non il nodo canvas.
    assert len(fake_executor.calls) == 1
    call = fake_executor.calls[0]
    assert call["project_id"] == "proj-1"
    assert call["triggered_by"] == "scheduler"
    assert call["run_id"] is None  # nessuno scheduled_for -> guard no-op
    node_data = call["node_data"]
    assert node_data["connection_id"] == "conn-1"
    assert node_data["table_name"] == "dest_table"
    assert node_data["write_mode"] == "append"

    # Mappatura del write_result sul payload scheduler.
    assert result["success"] is True
    assert result["rows_written"] == 3


def test_successful_scheduled_write_requires_completion_marker(monkeypatch):
    fake_executor = _FakeCaricaExecutor()
    manager = ExecutionManager(
        connection_service=object(),
        carica_executor_factory=lambda **_kwargs: fake_executor,
    )
    fake_artifact = SimpleNamespace(
        artifact_kind="run",
        to_run_artifact_ref=lambda: object(),
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.intermediate_runs_db.get_run",
        lambda _run_id: None,
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.intermediate_run_lifecycle.create_run",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.intermediate_runs_db.update_run",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("marker down")),
    )

    with patch(
        "app.core.scheduler.execution_manager.artifact_module.PipelineArtifact"
    ) as artifact_cls, patch(
        "app.core.scheduler.execution_manager.artifact_module.ArtifactResolver"
    ) as resolver_cls:
        artifact_cls.from_executor_result.return_value = fake_artifact
        resolver_cls.return_value.materialize_run.return_value = _Materialized()
        result = manager._execute_carica(
            _schedule_config(),
            pipeline_result={"success": True},
            group_id="group-1",
            scheduled_for="2026-07-20T10:00:00+00:00",
        )

    assert len(fake_executor.calls) == 1
    assert result["success"] is False
    assert result["error"] == "carica_completion_uncertain"


def test_orchestrator_factory_is_injectable():
    """G3: lo scheduler consuma un orchestrator costruito da factory
    iniettabile — i test usano un fake runner senza patchare
    build_orchestrator_stack."""
    fake_orchestrator = object()
    manager = ExecutionManager(orchestrator_factory=lambda: fake_orchestrator)
    assert manager._get_orchestrator() is fake_orchestrator


def test_default_orchestrator_factory_uses_composition_root(monkeypatch):
    """Senza iniezione, l'orchestrator viene dallo stack composition-root
    con la ConnectionService della manager (identita')."""
    sentinel_stack = SimpleNamespace(orchestrator=object())
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return sentinel_stack

    monkeypatch.setattr(
        "app.core.scheduler.execution_manager.build_orchestrator_stack",
        fake_build,
    )
    connection_sentinel = object()
    manager = ExecutionManager(connection_service=connection_sentinel)

    assert manager._get_orchestrator() is sentinel_stack.orchestrator
    assert captured == {"connection_service": connection_sentinel}


def test_default_factory_is_composition_root_builder():
    """Senza iniezione, la factory e' quella del composition root: lo
    scheduler non costruisce CaricaExecutor in proprio."""
    from app.runtime.services import build_scheduled_carica_executor

    manager = ExecutionManager()
    assert manager._carica_executor_factory is build_scheduled_carica_executor


def test_execution_manager_module_does_not_import_carica_executor():
    """G4: l'unico carica ammesso in execution_manager e' `carica.models`
    (contratto tipizzato WriteStatus); l'executor passa dalla factory."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[3]
        / "app" / "core" / "scheduler" / "execution_manager.py"
    ).read_text(encoding="utf-8")

    offending: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module.startswith("app.core.carica.executor"):
            offending.append(node.module)
        elif node.module == "app.core.carica":
            offending.extend(
                f"app.core.carica.{alias.name}"
                for alias in node.names
                if alias.name == "executor"
            )

    assert offending == [], (
        "scheduler/execution_manager non deve importare carica.executor: "
        f"{offending}"
    )
