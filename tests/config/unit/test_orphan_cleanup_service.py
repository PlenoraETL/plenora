"""
tests/config/unit/test_orphan_cleanup_service.py
================================================
Unit test per F14: orphan_cleanup_service.

Verifica che find_orphan_* trovi correttamente gli orfani cross-DB,
e che cleanup_orphans cancelli con il pattern dry-run/real-run corretto.
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# find_orphan_result_project_ids
# ============================================================================

class TestFindOrphanResultProjectIds:

    def test_no_orphans(self):
        from app.services.orphan_cleanup_service import find_orphan_result_project_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.db.results_db_service.fetchall") as mock_res:
            # config.projects ha proj-1, proj-2
            mock_cfg.return_value = [{"id": "proj-1"}, {"id": "proj-2"}]
            # results.results referenzia solo project esistenti
            mock_res.return_value = [{"project_id": "proj-1"}, {"project_id": "proj-2"}]
            orphans = find_orphan_result_project_ids()
        assert orphans == []

    def test_one_orphan(self):
        from app.services.orphan_cleanup_service import find_orphan_result_project_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.db.results_db_service.fetchall") as mock_res:
            mock_cfg.return_value = [{"id": "proj-1"}]
            mock_res.return_value = [
                {"project_id": "proj-1"},
                {"project_id": "proj-DELETED"},
            ]
            orphans = find_orphan_result_project_ids()
        assert orphans == ["proj-DELETED"]

    def test_all_orphans(self):
        from app.services.orphan_cleanup_service import find_orphan_result_project_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.db.results_db_service.fetchall") as mock_res:
            mock_cfg.return_value = []  # nessun progetto in config
            mock_res.return_value = [
                {"project_id": "ghost-1"},
                {"project_id": "ghost-2"},
            ]
            orphans = find_orphan_result_project_ids()
        assert set(orphans) == {"ghost-1", "ghost-2"}

    def test_empty_results(self):
        from app.services.orphan_cleanup_service import find_orphan_result_project_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.db.results_db_service.fetchall") as mock_res:
            mock_cfg.return_value = [{"id": "proj-1"}]
            mock_res.return_value = []
            orphans = find_orphan_result_project_ids()
        assert orphans == []

    def test_null_project_id_skipped(self):
        from app.services.orphan_cleanup_service import find_orphan_result_project_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.db.results_db_service.fetchall") as mock_res:
            mock_cfg.return_value = [{"id": "proj-1"}]
            mock_res.return_value = [
                {"project_id": None},  # NULL → ignorato
                {"project_id": "ghost"},
            ]
            orphans = find_orphan_result_project_ids()
        assert orphans == ["ghost"]


# ============================================================================
# find_orphan_run_canvas_ids
# ============================================================================

class TestFindOrphanRunCanvasIds:

    def test_no_orphans(self):
        from app.services.orphan_cleanup_service import find_orphan_run_canvas_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.core.intermediate.db.fetchall") as mock_idb:
            mock_cfg.return_value = [{"id": "proj-1"}]
            mock_idb.return_value = [{"canvas_id": "proj-1"}]
            orphans = find_orphan_run_canvas_ids()
        assert orphans == []

    def test_orphan_run(self):
        from app.services.orphan_cleanup_service import find_orphan_run_canvas_ids
        with patch("app.db.db_service.fetchall") as mock_cfg, \
             patch("app.core.intermediate.db.fetchall") as mock_idb:
            mock_cfg.return_value = [{"id": "proj-1"}]
            mock_idb.return_value = [
                {"canvas_id": "proj-1"},
                {"canvas_id": "proj-DELETED"},
            ]
            orphans = find_orphan_run_canvas_ids()
        assert orphans == ["proj-DELETED"]


# ============================================================================
# cleanup_orphans
# ============================================================================

class TestCleanupOrphans:

    def test_dry_run_does_not_delete(self):
        from app.services import orphan_cleanup_service as ocs
        with patch.object(ocs, "find_orphan_result_project_ids", return_value=["ghost-1"]), \
             patch.object(ocs, "find_orphan_run_canvas_ids", return_value=["ghost-2"]), \
             patch("app.db.results_db_service.delete_results_for_project") as mock_del_res, \
             patch("app.core.intermediate.db.delete_all_runs_for_canvas") as mock_del_run:
            report = ocs.cleanup_orphans(dry_run=True)

        assert report["dry_run"] is True
        assert report["orphan_results"]["project_ids"] == ["ghost-1"]
        assert report["orphan_runs"]["canvas_ids"] == ["ghost-2"]
        # NESSUNA cancellazione in dry-run
        assert report["orphan_results"]["rows_deleted"] == 0
        assert report["orphan_runs"]["runs_deleted"] == 0
        mock_del_res.assert_not_called()
        mock_del_run.assert_not_called()

    def test_real_run_deletes(self):
        from app.services import orphan_cleanup_service as ocs
        with patch.object(ocs, "find_orphan_result_project_ids", return_value=["ghost-1", "ghost-2"]), \
             patch.object(ocs, "find_orphan_run_canvas_ids", return_value=["ghost-3"]), \
             patch("app.db.results_db_service.delete_results_for_project", return_value=5) as mock_del_res, \
             patch("app.core.intermediate.db.delete_all_runs_for_canvas", return_value=2) as mock_del_run:
            report = ocs.cleanup_orphans(dry_run=False)

        assert report["dry_run"] is False
        # 2 project_ids × 5 righe ciascuno = 10 totali
        assert report["orphan_results"]["rows_deleted"] == 10
        # 1 canvas_id × 2 run = 2 totali
        assert report["orphan_runs"]["runs_deleted"] == 2
        assert mock_del_res.call_count == 2
        assert mock_del_run.call_count == 1
        assert report["errors"] == []

    def test_partial_failure_collected(self):
        """Se delete_results_for_project solleva su uno solo, gli altri proseguono e l'errore va in report.errors."""
        from app.services import orphan_cleanup_service as ocs

        def _delete_side_effect(pid):
            if pid == "ghost-bad":
                raise RuntimeError("DB locked")
            return 3

        with patch.object(ocs, "find_orphan_result_project_ids",
                          return_value=["ghost-ok", "ghost-bad", "ghost-ok2"]), \
             patch.object(ocs, "find_orphan_run_canvas_ids", return_value=[]), \
             patch("app.db.results_db_service.delete_results_for_project",
                   side_effect=_delete_side_effect):
            report = ocs.cleanup_orphans(dry_run=False)

        # 2 successi (3+3=6), 1 errore catturato
        assert report["orphan_results"]["rows_deleted"] == 6
        assert len(report["errors"]) == 1
        assert "ghost-bad" in report["errors"][0]

    def test_no_orphans_clean_report(self):
        from app.services import orphan_cleanup_service as ocs
        with patch.object(ocs, "find_orphan_result_project_ids", return_value=[]), \
             patch.object(ocs, "find_orphan_run_canvas_ids", return_value=[]):
            report = ocs.cleanup_orphans(dry_run=False)
        assert report["orphan_results"]["project_ids"] == []
        assert report["orphan_runs"]["canvas_ids"] == []
        assert report["orphan_results"]["rows_deleted"] == 0
        assert report["orphan_runs"]["runs_deleted"] == 0
        assert report["errors"] == []

    def test_finder_failure_logged(self):
        """Se find_orphan_result_project_ids stessa solleva, l'errore va in report.errors ma cleanup_orphans non crasha."""
        from app.services import orphan_cleanup_service as ocs
        with patch.object(ocs, "find_orphan_result_project_ids",
                          side_effect=RuntimeError("config DB down")), \
             patch.object(ocs, "find_orphan_run_canvas_ids", return_value=[]):
            report = ocs.cleanup_orphans(dry_run=True)
        assert len(report["errors"]) == 1
        assert "find_orphan_result_project_ids" in report["errors"][0]
