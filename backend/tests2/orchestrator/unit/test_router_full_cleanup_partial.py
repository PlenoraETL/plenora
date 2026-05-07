"""[phase-2-err][batch9] C4 — full-cleanup aggregato failures / 207 Multi-Status.

Audit Batch 9 §3.1 S2 / fix F2: l'endpoint DELETE /api/tasks/{id}/full-cleanup
ritornava 204 No Content anche se 4/6 step falliscono silenziosamente. Claim
GDPR-style "rimuovere ogni traccia" violato senza segnale al caller.

Post-fix:
  - 204 No Content (body vuoto) quando tutti e 4 gli step cleanup succedono
    — clean path INVARIATO.
  - 207 Multi-Status con body `{"status": "partial", "failed_steps": [...]}`
    quando almeno uno dei 4 step (delete_result / unlink_output_file /
    delete_log_runs / celery_forget) solleva.
  - 4 `logger.warning(..., exc_info=True)` promossi per diagnosticita' server.
  - Pre-check 404/403 invariati. Ordine degli step invariato. Path traversal
    safety skip (ValueError sul unlink) NON conta come failed_step.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


# Helpers


def _make_task_info(project_id="p1", result_id="res-1", output_file=None):
    """task_info mock con payload tipico post-esecuzione."""
    result: dict = {"project_id": project_id, "result_id": result_id}
    if output_file is not None:
        result["output_file"] = output_file
    return SimpleNamespace(
        project_id=project_id,
        result=result,
    )


def _patched_cleanup(
    client,
    task_id: str,
    *,
    tmp_path,
    delete_result_side_effect=None,
    delete_result_return=True,
    unlink_raises=False,
    pg_exec_side_effect=None,
    async_result_forget_raises=False,
    output_file_name=None,
):
    """Invoca DELETE full-cleanup con mock configurabili su ogni step."""
    task_info = _make_task_info(project_id="p1", result_id="res-1",
                                 output_file=output_file_name)

    project_dir = tmp_path / "p1"
    project_dir.mkdir(parents=True, exist_ok=True)
    if output_file_name:
        (project_dir / output_file_name).write_text("data", encoding="utf-8")

    # delete_result
    if delete_result_side_effect:
        dr_patch = {"side_effect": delete_result_side_effect}
    else:
        dr_patch = {"return_value": delete_result_return}

    # pg_connection.execute
    if pg_exec_side_effect:
        pg_patch = {"side_effect": pg_exec_side_effect}
    else:
        pg_patch = {"return_value": SimpleNamespace(rowcount=1)}

    # Celery AsyncResult.forget
    forget_mock = (
        SimpleNamespace(forget=lambda: (_ for _ in ()).throw(
            RuntimeError("celery down")
        ))
        if async_result_forget_raises
        else SimpleNamespace(forget=lambda: None)
    )

    # Path.unlink raise
    from pathlib import Path as _RealPath
    real_unlink = _RealPath.unlink

    def patched_unlink(self, *args, **kwargs):
        if unlink_raises and str(self).endswith(output_file_name or ""):
            raise OSError("disk unlink failed")
        return real_unlink(self, *args, **kwargs)

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path)
    ), patch(
        "app.services.task_service.task_service.get_task_status",
        return_value=task_info
    ), patch(
        "app.services.project_service.ProjectService.get_project_metadata",
        return_value={"id": "p1", "group_id": "grp-1"}
    ), patch(
        "app.db.results_db_service.delete_result", **dr_patch
    ), patch(
        "app.db.pg_connection.execute", **pg_patch
    ), patch(
        "celery.result.AsyncResult", return_value=forget_mock
    ), patch.object(
        _RealPath, "unlink", patched_unlink
    ):
        return client.delete(f"/api/tasks/{task_id}/full-cleanup")


# CLEAN PATH (204 invariato)


def test_full_cleanup_returns_204_when_all_steps_succeed(client, tmp_path):
    """Baseline: tutti gli step ok → 204 No Content, body vuoto.
    Questo e' il path di traffico maggioritario e deve restare invariato."""
    response = _patched_cleanup(
        client, "task-ok", tmp_path=tmp_path,
        output_file_name="out.csv",
    )
    assert response.status_code == 204
    # TestClient restituisce body vuoto bytes su 204
    assert response.content == b""


# PARTIAL PATH — singoli step che falliscono (207 Multi-Status)


def test_full_cleanup_returns_207_when_delete_result_fails(client, tmp_path):
    response = _patched_cleanup(
        client, "task-fail-dr", tmp_path=tmp_path,
        delete_result_side_effect=RuntimeError("results DB lock"),
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["delete_result"]


def test_full_cleanup_returns_207_when_unlink_output_file_fails(client, tmp_path):
    response = _patched_cleanup(
        client, "task-fail-unlink", tmp_path=tmp_path,
        output_file_name="out.csv",
        unlink_raises=True,
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["unlink_output_file"]


def test_full_cleanup_returns_207_when_log_runs_delete_fails(client, tmp_path):
    response = _patched_cleanup(
        client, "task-fail-logs", tmp_path=tmp_path,
        pg_exec_side_effect=RuntimeError("log_runs DELETE pool exhausted"),
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["delete_log_runs"]


def test_full_cleanup_returns_207_when_celery_forget_fails(client, tmp_path):
    response = _patched_cleanup(
        client, "task-fail-forget", tmp_path=tmp_path,
        async_result_forget_raises=True,
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["celery_forget"]


# PARTIAL PATH — multipli step falliscono, ordine d'esecuzione preservato


def test_full_cleanup_returns_207_with_multiple_failures(client, tmp_path):
    """Step 3 (delete_result) e step 6 (celery_forget) falliscono →
    failed_steps=[delete_result, celery_forget] nell'ordine d'esecuzione.
    Step 4/5 (unlink/pg_exec) continuano a essere tentati e succedono."""
    response = _patched_cleanup(
        client, "task-fail-multi", tmp_path=tmp_path,
        delete_result_side_effect=RuntimeError("results DB"),
        async_result_forget_raises=True,
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["delete_result", "celery_forget"]


def test_full_cleanup_returns_207_all_four_steps_fail(client, tmp_path):
    """Tutti e 4 gli step cleanup falliscono → failed_steps contiene tutti
    i 4 nomi nell'ordine d'esecuzione. L'endpoint NON solleva — aggrega."""
    response = _patched_cleanup(
        client, "task-fail-all", tmp_path=tmp_path,
        delete_result_side_effect=RuntimeError("dr"),
        output_file_name="out.csv",
        unlink_raises=True,
        pg_exec_side_effect=RuntimeError("pg"),
        async_result_forget_raises=True,
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == [
        "delete_result",
        "unlink_output_file",
        "delete_log_runs",
        "celery_forget",
    ]


# PRE-CHECK 404 invariati (non contati in failed_steps)


def test_full_cleanup_preserves_404_when_no_project_id(client, tmp_path):
    """task_info con project_id=None → 404 invariato, niente 207.
    I pre-check NON entrano in failed_steps."""
    task_info = SimpleNamespace(project_id=None, result={})

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path)
    ), patch(
        "app.services.task_service.task_service.get_task_status",
        return_value=task_info
    ):
        response = client.delete("/api/tasks/task-noproj/full-cleanup")

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Task non trovato o senza progetto associato — cleanup non eseguibile"
    )


# Breadcrumb exc_info=True sui 4 logger.warning promossi


def test_full_cleanup_breadcrumb_has_exc_info_on_failed_step(client, tmp_path):
    """Ogni `logger.warning("cleanup: errore ...")` sui 4 step ora include
    exc_info=True (promozione C4 per diagnosticita' server-side)."""
    with patch(
        "app.core.orchestrator.router.logger"
    ) as mock_logger:
        _patched_cleanup(
            client, "task-diag", tmp_path=tmp_path,
            delete_result_side_effect=RuntimeError("dr fail"),
            output_file_name="out.csv",
            unlink_raises=True,
            pg_exec_side_effect=RuntimeError("pg fail"),
            async_result_forget_raises=True,
        )

    # 4 warning con messaggio "cleanup: errore ..." e exc_info=True sui 4 step
    step_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "cleanup: errore" in str(c)
    ]
    assert len(step_warnings) == 4, (
        f"attesi 4 warning cleanup: errore su step, ricevuto: {step_warnings}"
    )
    for call in step_warnings:
        assert call.kwargs.get("exc_info") is True, (
            f"exc_info=True atteso, ricevuto: {call}"
        )
