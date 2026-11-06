"""
C4 — full-cleanup aggregato failures / 207 Multi-Status.

Audit finding: l'endpoint DELETE /api/tasks/{id}/full-cleanup
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


# Helpers


def _cleanup_settings(results_dir):
    """Settings finte di task_cleanup: RESULTS_DIR del test, il resto dai DEFAULTS
    (la pulizia parziale si rimette in coda, conferma finale F8/G3)."""
    from app.config import Settings

    return SimpleNamespace(
        RESULTS_DIR=results_dir,
        DEFAULTS=Settings.DEFAULTS,
        get=lambda name, default=None: Settings.DEFAULTS.get(name, default),
    )


def _make_task_info(project_id="p1", result_id="res-1", output_file=None):
    """task_info mock con payload tipico post-esecuzione."""
    result: dict = {"project_id": project_id, "result_id": result_id}
    if output_file is not None:
        result["output_file"] = output_file
    return SimpleNamespace(
        # Conferma finale F8/G3: la pulizia tocca solo un task terminale.
        status="success",
        project_id=project_id,
        result=result,
        subject_kind="project",
        subject_id=project_id,
        group_id="grp-1",
        owner_user_id=None,
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

    # orchestrator log cleanup repository execute
    if pg_exec_side_effect:
        pg_patch = {"side_effect": pg_exec_side_effect}
    else:
        pg_patch = {"return_value": SimpleNamespace(rowcount=1)}

    if async_result_forget_raises:
        forget_patch = {"side_effect": RuntimeError("celery down")}
    else:
        forget_patch = {"return_value": None}

    # Path.unlink raise
    from pathlib import Path as _RealPath
    real_unlink = _RealPath.unlink

    def patched_unlink(self, *args, **kwargs):
        if unlink_raises and str(self).endswith(output_file_name or ""):
            raise OSError("disk unlink failed")
        return real_unlink(self, *args, **kwargs)

    with patch(
        "app.core.orchestrator.task_cleanup.settings",
        new=_cleanup_settings(tmp_path)
    ), patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        return_value=task_info
    ), patch(
        "app.core.projects.service.ProjectService.get_project_metadata",
        return_value={"id": "p1", "group_id": "grp-1"}
    ), patch(
        "app.core.results.cleanup.delete_result", **dr_patch
    ), patch(
        "app.core.orchestrator.log_cleanup_repository.execute", **pg_patch
    ), patch(
        "app.core.orchestrator.task_service.task_service.forget_task_result",
        **forget_patch
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


def test_full_cleanup_authorizes_and_deletes_eseguibile_artifacts(client, tmp_path):
    task_info = SimpleNamespace(
        status="success",
        project_id="exec_1",
        subject_kind="executable",
        subject_id="exec_1",
        group_id="grp-1",
        owner_user_id="u-1",
        result={
            "project_id": "exec_1",
            "result_id": "res-1",
            "is_eseguibile": True,
        },
    )
    with patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        return_value=task_info,
    ), patch(
        "app.core.eseguibili.db.get_eseguibile",
        return_value={"id": "exec_1", "group_id": "grp-1"},
    ), patch(
        "app.core.eseguibili.router._check_visible",
    ), patch(
        "app.core.eseguibili.output_store.delete_task_outputs",
        return_value=1,
    ) as delete_outputs, patch(
        "app.core.results.cleanup.delete_result",
        return_value=True,
    ), patch(
        "app.core.orchestrator.log_cleanup.delete_task_log_runs",
        return_value=1,
    ), patch(
        "app.core.orchestrator.task_service.task_service.forget_task_result",
        return_value=None,
    ), patch(
        "app.core.orchestrator.task_cleanup.settings",
        new=_cleanup_settings(tmp_path),
    ):
        response = client.delete("/api/tasks/task-exec/full-cleanup")

    assert response.status_code == 204
    delete_outputs.assert_called_once_with("task-exec", group_id="grp-1")


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
    """Step delete_result fallisce, gli altri passi sui dati si tentano
    comunque; il record del task (celery_forget) NON si tocca finche' resta
    qualcosa (semplificazione H1: la ripetizione rifa' i passi col payload)."""
    response = _patched_cleanup(
        client, "task-fail-multi", tmp_path=tmp_path,
        delete_result_side_effect=RuntimeError("results DB"),
        pg_exec_side_effect=RuntimeError("pg"),
        async_result_forget_raises=True,
    )
    assert response.status_code == 207
    body = response.json()
    assert body["status"] == "partial"
    assert body["failed_steps"] == ["delete_result", "delete_log_runs"]


def test_full_cleanup_returns_207_all_data_steps_fail(client, tmp_path):
    """Tutti i passi sui dati falliscono → failed_steps li contiene tutti
    nell'ordine d'esecuzione; il record resta (niente celery_forget).
    L'endpoint NON solleva — aggrega."""
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
    ]


# PRE-CHECK 404 invariati (non contati in failed_steps)


def test_full_cleanup_preserves_404_when_no_project_id(client, tmp_path):
    """task_info con project_id=None → 404 invariato, niente 207.
    I pre-check NON entrano in failed_steps."""
    task_info = SimpleNamespace(project_id=None, result={})

    with patch(
        "app.core.orchestrator.task_cleanup.settings",
        new=_cleanup_settings(tmp_path)
    ), patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
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
        "app.core.orchestrator.task_cleanup.logger"
    ) as mock_logger:
        _patched_cleanup(
            client, "task-diag", tmp_path=tmp_path,
            delete_result_side_effect=RuntimeError("dr fail"),
            output_file_name="out.csv",
            unlink_raises=True,
            pg_exec_side_effect=RuntimeError("pg fail"),
            async_result_forget_raises=True,
        )

    # 3 warning "cleanup: errore ..." con exc_info=True sui 3 passi sui dati
    # (il record non si tocca dopo un passo fallito: niente forget).
    step_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "cleanup: errore" in str(c)
    ]
    assert len(step_warnings) == 3, (
        f"attesi 3 warning cleanup: errore su step, ricevuto: {step_warnings}"
    )
    for call in step_warnings:
        assert call.kwargs.get("exc_info") is True, (
            f"exc_info=True atteso, ricevuto: {call}"
        )
