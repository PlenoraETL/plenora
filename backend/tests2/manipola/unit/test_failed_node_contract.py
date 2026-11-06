"""Esito di Manipola: quale passo è fallito (06/11/2026, come Arricchisci).

REGOLA (``app.shared.execution.failed_node``): un'eccezione sollevata dentro
l'elaborazione di un nodo (trasformazione, multi_join, GROUP BY del nodo
Output) porta id e tipo del nodo; l'esito di ``execute``/``preview`` aggiunge
``error_node_id``/``error_node_type`` con il MESSAGGIO DI SEMPRE; il worker le
porta nel risultato del task (``result.error_node_id``). Senza nodo l'esito
resta ``{success, message}`` (``test_executor_failure_visibility_contract``).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.manipola.transforms.filtering import op_filter
from app.core.orchestrator.router_mappers import task_result_to_dict
from app.core.orchestrator.task_service import TaskResultPayload
from app.core.projects.canvas_models import CanvasState
from app.shared.execution.failed_node import failed_node_fields, mark_failed_node, node_step

pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]

POS = {"x": 0, "y": 0}
FRAME = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


def _canvas(filter_config: dict, result_extra: dict | None = None) -> CanvasState:
    return CanvasState.model_validate({
        "nodes": [
            {"id": "src", "type": "file_source", "position": POS},
            {"id": "f-ok", "type": "transform", "position": POS, "transform_op": "filter",
             "config": {"column": "a", "operator": ">", "value": "0"}},
            {"id": "f-ko", "type": "transform", "position": POS, "transform_op": "filter", "config": filter_config},
            {"id": "out", "type": "result", "position": POS, **(result_extra or {})},
        ],
        "edges": [
            {"id": "e1", "source": "src", "target": "f-ok"},
            {"id": "e2", "source": "f-ok", "target": "f-ko"},
            {"id": "e3", "source": "f-ko", "target": "out", "join_type": "output"},
        ],
    })


def _executor(monkeypatch) -> ManipolaExecutor:
    executor = ManipolaExecutor(project_service=object())
    monkeypatch.setattr("app.core.manipola.executor.append_step", lambda step: None)
    for name in ("_carica_sorgenti_progetto", "_carica_sorgenti_datasource", "_carica_sorgenti_statiche"):
        monkeypatch.setattr(executor, name, lambda *_a, **_kw: None)

    def _files(_canvas, dataframes, **_kw):
        dataframes["src"] = FRAME.copy()

    monkeypatch.setattr(executor, "_carica_sorgenti_file", _files)
    return executor


def _runtime_message(**params) -> str:
    with pytest.raises(ValueError) as info:
        op_filter(FRAME.copy(), **params)
    return str(info.value)


def test_execute_failure_names_the_failed_transform_with_the_same_message(monkeypatch):
    result = _executor(monkeypatch).execute("p", _canvas({"column": "nope", "operator": "=="}),
                                            execution_context={"group_id": "g"})
    assert result == {"success": False, "message": _runtime_message(column="nope", operator="=="),
                      "error_node_id": "f-ko", "error_node_type": "transform"}


def test_preview_failure_names_the_failed_transform(monkeypatch):
    result = _executor(monkeypatch).preview("p", _canvas({"column": "nope", "operator": "=="}), 10,
                                            execution_context={"group_id": "g"})
    assert result == {"success": False, "message": _runtime_message(column="nope", operator="=="),
                      "error_node_id": "f-ko", "error_node_type": "transform"}


def test_group_by_failure_names_the_output_node(monkeypatch):
    canvas = _canvas({"column": "a", "operator": ">", "value": "1"},
                     {"group_by_enabled": True, "group_by_columns": ["missing"],
                      "aggregations": [{"column": "a", "function": "SUM", "alias": "s"}]})
    result = _executor(monkeypatch).execute("p", canvas, execution_context={"group_id": "g"})
    assert result["success"] is False
    assert (result["error_node_id"], result["error_node_type"]) == ("out", "result")


def test_innermost_node_wins_and_base_exceptions_are_not_marked():
    exc = ValueError("x")
    with pytest.raises(ValueError):
        with node_step(SimpleNamespace(id="outer", type="result")):
            with node_step(SimpleNamespace(id="inner", type="transform")):
                raise exc
    assert failed_node_fields(exc) == {"error_node_id": "inner", "error_node_type": "transform"}
    interrupt = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        with node_step(SimpleNamespace(id="n", type="transform")):
            raise interrupt
    assert failed_node_fields(interrupt) == {}
    assert failed_node_fields(None) == {} and failed_node_fields(ValueError()) == {}
    assert mark_failed_node(exc, "other", "x") is exc and failed_node_fields(exc)["error_node_id"] == "inner"


def test_worker_task_result_carries_the_failed_node(monkeypatch):
    """Percorso fino al frontend: esito -> risultato del task -> /tasks/{id}."""
    from workers.tasks import pipeline_task as pt

    class _Result(dict):
        success = False
        message = "Filter: colonna 'nope' non trovata."
        rows_count = 0
        output_path = Path("x.csv")

    orchestrator = MagicMock()
    orchestrator.execute_pipeline_from_contract.return_value = _Result(
        error_node_id="f-ko", error_node_type="transform")
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(pt, "_update_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(pt, "_close_worker_connection", lambda **kwargs: None)
    monkeypatch.setattr(pt, "set_run_context", lambda **kwargs: None)
    monkeypatch.setattr(pt.async_user_recheck_module, "recheck_eseguibile_authorized", lambda **kwargs: None)
    monkeypatch.setattr(pt.eseguibili_service, "get_eseguibile_run", lambda run_id: {"status": "running"})
    monkeypatch.setattr(pt.eseguibili_service, "complete_eseguibile_run", lambda *args, **kwargs: None)

    result = pt.execute_pipeline_task(
        SimpleNamespace(request=SimpleNamespace(id="task-1"), name="pipeline.execute"),
        project_id="exec_1", run_id="task-1", user_id="user-1",
        execution_contract={
            "project_id": "exec_1", "project_type": "manipola", "group_id": "group-1",
            "canvas": {"nodes": [], "edges": []},
            "execution_subject": {"kind": "eseguibile", "id": "exec_1", "run_id": "erun_1"},
        },
    )
    assert result["success"] is False and result["message"] == _Result.message
    assert (result["error_node_id"], result["error_node_type"]) == ("f-ko", "transform")
    public = task_result_to_dict(TaskResultPayload.from_celery_meta(result))
    assert (public["error_node_id"], public["error_node_type"]) == ("f-ko", "transform")
