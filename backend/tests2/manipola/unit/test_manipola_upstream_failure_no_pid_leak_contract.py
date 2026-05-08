"""
Contract test Bug #25 — `_carica_sorgenti_progetto` non deve

propagare `project_id` dell'upstream quando questo fallisce.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.artifacts.types import PipelineArtifact
from app.core.manipola.executor import ManipolaExecutor


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id, ntype, **attrs):
    payload = {
        "id": node_id,
        "type": ntype,
        "data": attrs.pop("data", None),
        "transform_op": attrs.pop("transform_op", None),
    }
    payload.update(attrs)
    return SimpleNamespace(**payload)


def _canvas(nodes, edges=None):
    return SimpleNamespace(nodes=nodes, edges=edges or [])


_EXECUTOR_GROUP_ID = "g1"


def _executor_with_callback(callback) -> ManipolaExecutor:
    """ManipolaExecutor con orchestrator_callback iniettato.
    TE-009: group_id viene passato esplicitamente ad ogni chiamata."""
    executor = ManipolaExecutor(
        project_service=object(),
        orchestrator_callback=callback,
    )
    return executor


def _failing_upstream_artifact(source_pid: str) -> PipelineArtifact:
    """PipelineArtifact che simula l'upstream fallito (es. SSO scaduto,
    project non recuperabile, ecc.)."""
    return PipelineArtifact({
        "project_id": source_pid,
        "success": False,
        "message": "AWS credentials expired. Run 'aws sso login'.",
    })


# 1. upstream failure NON propaga project_id source


def test_upstream_failure_does_not_leak_source_project_id():
    """Pre-fix: il dict di ritorno aveva `project_id == source_pid`.
    Post-fix: niente `project_id` nel dict, oppure se presente uguale
    al progetto corrente (la regola enforced da
    `from_executor_result`)."""
    source_pid = "proj_source_v1"
    callback = MagicMock(return_value=_failing_upstream_artifact(source_pid))
    executor = _executor_with_callback(callback)

    canvas = _canvas([
        _node("ps1", "project_source", source_project_id=source_pid),
    ])
    dataframes: dict = {}

    # Bypass create_run upstream e open_run upstream (DB-bound).
    with patch("app.utils.logger.open_run"), \
         patch("app.core.intermediate.db.create_run"), \
         patch("app.core.intermediate.writer.close_run"), \
         patch("app.utils.logger.close_run"), \
         patch("app.core.projects.db.get_project", return_value={
             "id": source_pid, "group_id": "g1", "project_type": "estrai",
         }):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=True,
            group_id=_EXECUTOR_GROUP_ID,
        )

    assert result is not None, "ramo error deve ritornare dict, non None"
    assert result.get("success") is False
    leaked = result.get("project_id")
    assert leaked != source_pid, (
        f"upstream project_id={source_pid!r} non deve fuoriuscire dal "
        f"return value: result={result!r}"
    )


# 2. wrappable da from_executor_result senza ValueError


def test_upstream_failure_result_is_wrappable_without_pid_mismatch():
    """`OrchestratorService.execute_pipeline` chiama
    `PipelineArtifact.from_executor_result(result, project_id=<corrente>)`.
    Pre-fix sollevava ValueError. Post-fix non solleva."""
    source_pid = "proj_source_v1"
    current_pid = "proj_current_v1"
    callback = MagicMock(return_value=_failing_upstream_artifact(source_pid))
    executor = _executor_with_callback(callback)

    canvas = _canvas([
        _node("ps1", "project_source", source_project_id=source_pid),
    ])
    dataframes: dict = {}

    with patch("app.utils.logger.open_run"), \
         patch("app.core.intermediate.db.create_run"), \
         patch("app.core.intermediate.writer.close_run"), \
         patch("app.utils.logger.close_run"), \
         patch("app.core.projects.db.get_project", return_value={
             "id": source_pid, "group_id": "g1", "project_type": "estrai",
         }):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=True,
            group_id=_EXECUTOR_GROUP_ID,
        )

    # La chiamata deve passare senza ValueError. Il safety net resta
    # intatto: se il dict avesse ancora project_id=source_pid, il
    # wrap solleverebbe.
    artifact = PipelineArtifact.from_executor_result(
        result, project_id=current_pid,
    )
    assert artifact["project_id"] == current_pid
    assert artifact.success is False


# 3. messaggio include source_project_id per diagnostica


def test_upstream_failure_message_includes_source_project_id():
    """L'operatore deve poter capire QUALE upstream e' fallito leggendo
    il messaggio. `source_project_id` e il messaggio upstream nudo
    devono essere preservati nel testo."""
    source_pid = "proj_source_v1_diag"
    upstream_msg = "AWS credentials expired. Run 'aws sso login'."
    callback = MagicMock(return_value=PipelineArtifact({
        "project_id": source_pid,
        "success": False,
        "message": upstream_msg,
    }))
    executor = _executor_with_callback(callback)

    canvas = _canvas([
        _node("ps1", "project_source", source_project_id=source_pid),
    ])
    dataframes: dict = {}

    with patch("app.utils.logger.open_run"), \
         patch("app.core.intermediate.db.create_run"), \
         patch("app.core.intermediate.writer.close_run"), \
         patch("app.utils.logger.close_run"), \
         patch("app.core.projects.db.get_project", return_value={
             "id": source_pid, "group_id": "g1", "project_type": "estrai",
         }):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=True,
            group_id=_EXECUTOR_GROUP_ID,
        )

    msg = result.get("message", "")
    assert source_pid in msg, (
        f"messaggio deve citare il source_project_id colpevole: {msg!r}"
    )
    assert "AWS credentials expired" in msg or "aws sso login" in msg, (
        f"messaggio deve preservare la causa upstream nuda: {msg!r}"
    )


# 4. upstream success path invariato


def test_upstream_success_path_unchanged():
    """Anti-regression: quando upstream.success=True, il flow procede
    a `materialize_latest` e popola `dataframes[node.id]`. Niente
    early-return dal ramo error."""
    source_pid = "proj_source_ok"
    success_artifact = PipelineArtifact({
        "project_id": source_pid,
        "success": True,
        "output_path": "/tmp/non-esiste-ma-non-importa.csv",
        "rows_count": 10,
        "message": "ok",
    })
    callback = MagicMock(return_value=success_artifact)
    executor = _executor_with_callback(callback)

    canvas = _canvas([
        _node("ps1", "project_source", source_project_id=source_pid),
    ])
    dataframes: dict = {}

    fake_df = pd.DataFrame({"col": [1, 2, 3]})

    # Mocchiamo:
    # - logger open/close run + intermediate create/close
    # - get_project per il source
    # - ArtifactResolver.materialize_latest che ritorna un materialized
    #   con un path leggibile
    # - load_dataframe_from_file per evitare I/O reale
    fake_materialized = MagicMock()
    fake_materialized.path = "/tmp/fake.csv"
    fake_materialized.__enter__ = MagicMock(return_value=fake_materialized)
    fake_materialized.__exit__ = MagicMock(return_value=False)

    with patch("app.utils.logger.open_run"), \
         patch("app.core.intermediate.db.create_run"), \
         patch("app.core.intermediate.writer.close_run"), \
         patch("app.utils.logger.close_run"), \
         patch("app.core.projects.db.get_project", return_value={
             "id": source_pid, "group_id": "g1", "project_type": "estrai",
         }), patch(
             "app.core.artifacts.ArtifactResolver"
         ) as MockResolver, patch(
             "app.core.manipola.executor.load_dataframe_from_file",
             return_value=fake_df,
         ), patch(
             "app.core.manipola.executor.Path"
         ) as MockPath:
        # Path("...").exists() -> False -> fallback a materialize_latest
        instance_path = MagicMock()
        instance_path.exists.return_value = False
        MockPath.return_value = instance_path
        MockResolver.return_value.materialize_latest.return_value = fake_materialized

        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=True,
            group_id=_EXECUTOR_GROUP_ID,
        )

    # Success path: nessun dict di errore (None == continue dal loader)
    assert result is None, (
        f"upstream success non deve produrre dict di errore: {result!r}"
    )
    assert "ps1" in dataframes, "DataFrame deve essere popolato per il node"
