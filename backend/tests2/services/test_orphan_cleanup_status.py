"""[phase-2-err][batch9] C3 — orphan_cleanup_service status esplicito.

Audit Batch 9 §3.2 S7 / fix F5: `cleanup_orphans` ritornava un dict con
`errors: [...]` ma senza chiave che distinguesse "scan completato con
successo, nessun orfano" da "scan/delete parzialmente fallito in silenzio".
Un caller admin che leggeva `rows_deleted=0, errors=[...]` non aveva modo
dict-shape-level di distinguere dal caso "nothing to do".

Post-fix:
  - chiave `status` aggiunta al report: `"clean"` di default, `"partial"`
    se `errors` non vuoto al termine.
  - log finale promosso da `info` a `warning` quando `status == "partial"`.
  - `exc_info=True` sui 2 outer catch gia' presente (verifica).

Tutti i fix sono **additive**: nessun cambio di contratto esistente
(chiavi precedenti intatte), solo aggiunta di `status` nel dict returned
+ livello log piu' severo su partial.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# status "clean" — scan ok senza orfani


def test_cleanup_orphans_status_clean_when_no_orphans():
    """Scan ok, 0 orfani in entrambi i DB → status='clean', errors=[]."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=[]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ):
        report = cleanup_orphans(dry_run=False)

    assert report["status"] == "clean"
    assert report["errors"] == []
    assert report["orphan_results"]["rows_deleted"] == 0
    assert report["orphan_runs"]["runs_deleted"] == 0


def test_cleanup_orphans_status_clean_when_all_deletes_succeed():
    """Scan trova orfani, delete tutti ok → status='clean'."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=["p-orphan-1", "p-orphan-2"]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=["c-orphan-1"]
    ), patch(
        "app.db.results_db_service.delete_results_for_project",
        return_value=3
    ), patch(
        "app.core.intermediate.db.delete_all_runs_for_canvas",
        return_value=2
    ):
        report = cleanup_orphans(dry_run=False)

    assert report["status"] == "clean"
    assert report["errors"] == []
    assert report["orphan_results"]["rows_deleted"] == 6  # 3 * 2 pid
    assert report["orphan_runs"]["runs_deleted"] == 2


# status "partial" — almeno un fallimento inner o outer


def test_cleanup_orphans_status_partial_when_per_pid_delete_fails():
    """Inner per-pid delete solleva su un orfano → status='partial',
    errors non vuoto, gli altri orfani comunque elaborati."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    def flaky_delete_results(pid):
        if pid == "p-broken":
            raise RuntimeError("storage down for this pid")
        return 5

    with patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=["p-ok-1", "p-broken", "p-ok-2"]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ), patch(
        "app.db.results_db_service.delete_results_for_project",
        side_effect=flaky_delete_results
    ):
        report = cleanup_orphans(dry_run=False)

    assert report["status"] == "partial"
    assert len(report["errors"]) == 1
    assert "p-broken" in report["errors"][0]
    # Gli altri 2 pid sono stati cancellati (5 * 2 = 10 righe)
    assert report["orphan_results"]["rows_deleted"] == 10


def test_cleanup_orphans_status_partial_when_scan_fails():
    """Outer scan (find_orphan_result_project_ids) solleva → status='partial',
    scan intermediate procede comunque."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        side_effect=RuntimeError("results DB unreachable")
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ):
        report = cleanup_orphans(dry_run=False)

    assert report["status"] == "partial"
    assert len(report["errors"]) == 1
    assert "find_orphan_result_project_ids" in report["errors"][0]
    # lo scan intermediate e' stato tentato (ha trovato 0 orfani, ok)
    assert report["orphan_runs"]["canvas_ids"] == []


def test_cleanup_orphans_status_partial_when_both_scans_fail():
    """Entrambi gli outer scan sollevano → status='partial', 2 errors
    distinti, `exc_info=True` sui log [CRASH] (sanity check)."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.logger"
    ) as mock_logger, patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        side_effect=RuntimeError("results DB down")
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        side_effect=RuntimeError("intermediate DB down")
    ):
        report = cleanup_orphans(dry_run=False)

    assert report["status"] == "partial"
    assert len(report["errors"]) == 2
    # exc_info=True sui due outer catch (mantenuto da fix precedenti)
    error_calls_with_exc_info = [
        c for c in mock_logger.error.call_args_list
        if c.kwargs.get("exc_info") is True
    ]
    assert len(error_calls_with_exc_info) == 2, (
        f"attesi 2 logger.error con exc_info=True, ricevuto: "
        f"{mock_logger.error.call_args_list}"
    )


# Log level: warning su partial, info su clean


def test_cleanup_orphans_final_log_is_warning_when_partial():
    """Il log riassuntivo finale deve essere `logger.warning` quando lo
    status e' 'partial' — rende visibile in alerting un cleanup fallito."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.logger"
    ) as mock_logger, patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=["p-broken"]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ), patch(
        "app.db.results_db_service.delete_results_for_project",
        side_effect=RuntimeError("delete failed")
    ):
        cleanup_orphans(dry_run=False)

    # Il riassunto finale deve essere warning (non info) + menzionare "status=partial"
    summary_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "orphan_cleanup:" in str(c) and "status=partial" in str(c)
    ]
    assert summary_warnings, (
        f"atteso warning riassuntivo 'orphan_cleanup: status=partial ...', "
        f"ricevuto warnings: {mock_logger.warning.call_args_list}"
    )


def test_cleanup_orphans_final_log_is_info_when_clean():
    """Il log riassuntivo finale resta `logger.info` quando status='clean'
    — comportamento invariato rispetto al pre-fix per il caso happy path."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.logger"
    ) as mock_logger, patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=[]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ):
        cleanup_orphans(dry_run=False)

    # Riassunto emesso come info (non warning)
    summary_infos = [
        c for c in mock_logger.info.call_args_list
        if "orphan_cleanup:" in str(c) and "status=clean" in str(c)
    ]
    assert summary_infos, (
        f"atteso info riassuntivo 'orphan_cleanup: status=clean ...', "
        f"ricevuto infos: {mock_logger.info.call_args_list}"
    )
    # Nessun warning riassuntivo emesso
    summary_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "orphan_cleanup:" in str(c) and "status=" in str(c)
    ]
    assert not summary_warnings


# Backwards compat: chiavi pre-esistenti intatte


def test_cleanup_orphans_report_backward_compat_keys_present():
    """Sanity: chiavi del dict report pre-fix devono restare presenti
    con la stessa forma (dry_run, orphan_results, orphan_runs, errors).
    `status` e' additivo."""
    from app.services.orphan_cleanup_service import cleanup_orphans

    with patch(
        "app.services.orphan_cleanup_service.find_orphan_result_project_ids",
        return_value=[]
    ), patch(
        "app.services.orphan_cleanup_service.find_orphan_run_canvas_ids",
        return_value=[]
    ):
        report = cleanup_orphans(dry_run=True)

    # Chiavi legacy intatte
    assert "dry_run" in report
    assert report["dry_run"] is True
    assert "orphan_results" in report
    assert "project_ids" in report["orphan_results"]
    assert "rows_deleted" in report["orphan_results"]
    assert "orphan_runs" in report
    assert "canvas_ids" in report["orphan_runs"]
    assert "runs_deleted" in report["orphan_runs"]
    assert "errors" in report
    # Chiave nuova additiva
    assert "status" in report
