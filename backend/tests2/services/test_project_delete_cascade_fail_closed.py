"""
C5 — delete_project fail-closed su step critici.

Audit finding: `ProjectService.delete_project` faceva cascade
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.projects.db import ProjectDetailRow


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Helpers


def _mock_project_row():
    """Row tipica restituita da db.get_project."""
    return ProjectDetailRow(
        id="p-target",
        name="Progetto Target",
        project_type="manipola",
        group_id="grp-1",
        canvas={},
        connection_id=None,
        created_at=None,
        updated_at=None,
        created_by=None,
    )


def _patch_cascade(
    *,
    schedules_side_effect=None,
    intermediate_side_effect=None,
    results_side_effect=None,
    comparison_keys_side_effect=None,
    db_delete_side_effect=None,
):
    """Contesto patch centralizzato per tutti i 5 step cascade.

    side_effect=None -> no-op success. side_effect=Exception(...) -> raise.
    """
    import contextlib

    @contextlib.contextmanager
    def ctx():
        # Schedule service: ritorna lista vuota per default (zero schedule).
        # Se vogliamo forzare un fallimento, side_effect sul mock.
        fake_schedule_svc = MagicMock()
        if schedules_side_effect:
            fake_schedule_svc.get_schedules_for_project.side_effect = (
                schedules_side_effect
            )
        else:
            fake_schedule_svc.get_schedules_for_project.return_value = []

        # delete_all_runs_for_canvas
        if intermediate_side_effect:
            intermediate_patch = {"side_effect": intermediate_side_effect}
        else:
            intermediate_patch = {"return_value": 0}

        # delete_results_for_project
        if results_side_effect:
            results_patch = {"side_effect": results_side_effect}
        else:
            results_patch = {"return_value": 0}

        # delete_comparison_keys
        if comparison_keys_side_effect:
            ck_patch = {"side_effect": comparison_keys_side_effect}
        else:
            ck_patch = {"return_value": None}

        # db.delete_project (il config DELETE finale)
        if db_delete_side_effect:
            db_del_patch = {"side_effect": db_delete_side_effect}
        else:
            db_del_patch = {"return_value": True}

        with patch(
            "app.core.projects.service.project_db.get_project_row",
            return_value=_mock_project_row()
        ), patch(
            "app.core.orchestrator.task_service.task_service.list_active_task_ids_for_project",
            return_value=[],
        ), patch(
            # Epic F (F5): delete_project vive nel sub-service lifecycle, che
            # importa `list_eseguibili_by_source` a module level — il binding
            # da patchare e' il call-site su `lifecycle_service` (stessa
            # regola documentata per `intermediate.cleanup` sotto).
            "app.core.projects.lifecycle_service.list_eseguibili_by_source",
            return_value=[]
        ), patch(
            "app.core.projects.service.ProjectService._find_dependent_projects",
            return_value=[]
        ), patch(
            "app.core.scheduler.schedule_service.ScheduleService",
            return_value=fake_schedule_svc
        ), patch(
            # `app.core.intermediate.cleanup` imports `delete_all_runs_for_canvas`
            # from `app.core.intermediate.retention`, so the local name to patch
            # is the one bound on `cleanup` (the call site) — patching the
            # `db` module would target a different copy of the symbol that
            # the cleanup module never reads.
            "app.core.intermediate.cleanup.delete_all_runs_for_canvas",
            **intermediate_patch
        ), patch(
            "app.core.results.cleanup.delete_results_for_project",
            **results_patch
        ), patch(
            "app.core.orchestrator.preview_payload_store.delete_project_preview_payloads",
            return_value=0,
        ), patch(
            "app.core.projects.db.delete_comparison_keys",
            **ck_patch
        ), patch(
            "app.core.projects.service.project_db.delete_project",
            **db_del_patch
        ) as mock_db_delete:
            yield mock_db_delete

    return ctx()


# HAPPY PATH


def test_delete_project_happy_path_returns_true_and_deletes_config():
    """Baseline: tutti i 4 step cleanup ok → db.delete_project chiamato
    → return True. Nessun RuntimeError sollevato."""
    from app.core.projects.service import ProjectService

    with _patch_cascade() as mock_db_delete:
        svc = ProjectService()
        result = svc.delete_project("p-target")

    assert result is True
    mock_db_delete.assert_called_once_with("p-target", group_id=None)


# FAIL-CLOSED — step critical


def test_delete_project_fail_closed_when_intermediate_runs_delete_fails():
    """Step 2 (intermediate_runs) solleva → ProjectCleanupPartialError propaga,
    db.delete_project NON chiamato (config row preservata), attributi
    tipizzati contengono 'intermediate_runs'."""
    from app.core.projects.service import ProjectService
    from app.core.projects.exceptions import ProjectCleanupPartialError

    with _patch_cascade(
        intermediate_side_effect=RuntimeError("intermediate DB lock timeout")
    ) as mock_db_delete:
        svc = ProjectService()
        with pytest.raises(ProjectCleanupPartialError) as exc_info:
            svc.delete_project("p-target")

    # Invariante fail-closed: config row NON rimossa
    mock_db_delete.assert_not_called()
    # Attributi tipizzati: project_id + critical list + derived vuota
    assert exc_info.value.project_id == "p-target"
    assert exc_info.value.critical_failed == ["intermediate_runs"]
    assert exc_info.value.derived_failed == []


def test_delete_project_fail_closed_when_results_blob_delete_fails():
    """Step 3 (results_blobs) solleva → ProjectCleanupPartialError, db.delete_project
    NON chiamato, attributi contengono 'results_blobs'."""
    from app.core.projects.service import ProjectService
    from app.core.projects.exceptions import ProjectCleanupPartialError

    with _patch_cascade(
        results_side_effect=RuntimeError("S3 timeout on blob delete")
    ) as mock_db_delete:
        svc = ProjectService()
        with pytest.raises(ProjectCleanupPartialError) as exc_info:
            svc.delete_project("p-target")

    mock_db_delete.assert_not_called()
    assert exc_info.value.critical_failed == ["results_blobs"]
    assert exc_info.value.derived_failed == []


def test_delete_project_fail_closed_with_multiple_critical():
    """Step 2 e 3 entrambi falliscono → ProjectCleanupPartialError con entrambi
    nel `critical_failed`, ordine d'esecuzione preservato."""
    from app.core.projects.service import ProjectService
    from app.core.projects.exceptions import ProjectCleanupPartialError

    with _patch_cascade(
        intermediate_side_effect=RuntimeError("intermediate down"),
        results_side_effect=RuntimeError("results down"),
    ) as mock_db_delete:
        svc = ProjectService()
        with pytest.raises(ProjectCleanupPartialError) as exc_info:
            svc.delete_project("p-target")

    mock_db_delete.assert_not_called()
    # Ordine d'esecuzione preservato nella lista critical_failed
    assert exc_info.value.critical_failed == ["intermediate_runs", "results_blobs"]
    assert exc_info.value.derived_failed == []


# BEST-EFFORT — step derived (non bloccano step 5)


def test_delete_project_best_effort_when_schedule_orphan_fails():
    """Step 1 (schedules_orphan) solleva → NON raise, step 2-5 comunque
    eseguiti, db.delete_project chiamato, return True. Warning con exc_info."""
    from app.core.projects.service import ProjectService

    # Epic F (F5): delete_project vive nel sub-service lifecycle, i suoi
    # warning escono dal logger di quel modulo.
    with patch(
        "app.core.projects.lifecycle_service.logger"
    ) as mock_logger, _patch_cascade(
        schedules_side_effect=RuntimeError("scheduler DB connection reset")
    ) as mock_db_delete:
        svc = ProjectService()
        result = svc.delete_project("p-target")

    assert result is True
    mock_db_delete.assert_called_once()
    # NOTE: con exc_info=True
    schedule_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "Cleanup schedules fallito" in str(c)
    ]
    assert len(schedule_warnings) == 1
    assert schedule_warnings[0].kwargs.get("exc_info") is True


def test_delete_project_best_effort_when_comparison_keys_fails():
    """Step 4 (comparison_keys) solleva → NON raise, step 5 eseguito,
    return True. Warning con exc_info."""
    from app.core.projects.service import ProjectService

    with patch(
        "app.core.projects.lifecycle_service.logger"
    ) as mock_logger, _patch_cascade(
        comparison_keys_side_effect=RuntimeError("config DB lock")
    ) as mock_db_delete:
        svc = ProjectService()
        result = svc.delete_project("p-target")

    assert result is True
    mock_db_delete.assert_called_once()
    ck_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "Cleanup comparison_keys fallito" in str(c)
    ]
    assert len(ck_warnings) == 1
    assert ck_warnings[0].kwargs.get("exc_info") is True


# FAIL-CLOSED wins over derived (critical + derived mix)


def test_delete_project_mixed_failure_critical_wins():
    """Step 1 derived + step 3 critical falliscono → ProjectCleanupPartialError
    (critical prevale); attributi contengono sia critical che derived."""
    from app.core.projects.service import ProjectService
    from app.core.projects.exceptions import ProjectCleanupPartialError

    with _patch_cascade(
        schedules_side_effect=RuntimeError("scheduler blip"),
        results_side_effect=RuntimeError("s3 down"),
    ) as mock_db_delete:
        svc = ProjectService()
        with pytest.raises(ProjectCleanupPartialError) as exc_info:
            svc.delete_project("p-target")

    # Fail-closed: config row non rimossa (critical wins)
    mock_db_delete.assert_not_called()
    # Critical presente
    assert exc_info.value.critical_failed == ["results_blobs"]
    # Derived anche riportato (per diagnosticita')
    assert exc_info.value.derived_failed == ["schedules_orphan"]


# INVARIANT — config row preservata su qualsiasi critical failure


def test_delete_project_invariant_config_preserved_on_critical_fail():
    """Invariante fail-closed esplicita: su QUALSIASI fallimento critical,
    db.delete_project NON deve essere chiamato. Test con solo intermediate down."""
    from app.core.projects.service import ProjectService
    from app.core.projects.exceptions import ProjectCleanupPartialError

    with _patch_cascade(
        intermediate_side_effect=RuntimeError("boom"),
    ) as mock_db_delete:
        svc = ProjectService()
        with pytest.raises(ProjectCleanupPartialError):
            svc.delete_project("p-target")

    # Invariante chiave del fix: la row config.projects resta in DB
    mock_db_delete.assert_not_called()
