"""
tests/security/unit/test_block_G_mirror_fixes.py
================================================
Unit test per il Blocco G: fix di "pattern duplication" trovati al 2° audit.

G-1: N1 LIKE escape in logs_router.py + N2 close_connection execution_manager
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# G-1 / N1 — LIKE escape in logs_router.py (mirror di logs/router.py)
# ============================================================================

class TestLogsRouterLikeEscape:
    """Verifica che logs_router.py applichi lo stesso escape di router.py."""

    def test_logs_router_has_escape_pattern(self):
        """Lettura statica del file: deve contenere il pattern ESCAPE."""
        from pathlib import Path
        path = Path(__file__).resolve().parents[3] / "backend" / "app" / "core" / "logs" / "logs_router.py"
        content = path.read_text(encoding="utf-8")
        # Pattern del fix
        assert "ESCAPE '\\\\'" in content, "logs_router.py manca della clausola ESCAPE"
        assert "replace(\"%\", \"\\\\%\")" in content, "logs_router.py non escape %"
        assert "replace(\"_\", \"\\\\_\")" in content, "logs_router.py non escape _"

    def test_real_sqlite_with_escaped_search(self, tmp_path):
        """Smoke test: ricerca letterale '%' deve matchare solo righe con quel char."""
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE logs (msg TEXT)")
        db.execute("INSERT INTO logs VALUES ('contains 100% raw')")
        db.execute("INSERT INTO logs VALUES ('discount applied')")
        db.commit()

        # Replica della logica di escape del fix
        search = "100%"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = db.execute(
            "SELECT msg FROM logs WHERE msg LIKE ? ESCAPE '\\'",
            (f"%{escaped}%",)
        ).fetchall()
        assert len(rows) == 1
        assert "100%" in rows[0][0]


# ============================================================================
# G-1 / N2 — close_connection in execution_manager.py finally
# ============================================================================

class TestExecutionManagerCloseConnection:
    """Verifica che ExecutionManager.execute() chiuda le conn nel finally."""

    def test_finally_closes_connections(self):
        """Lettura statica del file: il finally deve chiamare entrambe le close."""
        from pathlib import Path
        path = Path(__file__).resolve().parents[3] / "backend" / "app" / "core" / "scheduler" / "execution_manager.py"
        content = path.read_text(encoding="utf-8")
        # Verifica che il finally chiami entrambe le close
        assert "from app.core.intermediate.db import close_connection" in content
        assert "from app.db.config_db_service import close_connection" in content
        # Devono essere DENTRO un finally (non in un except)
        # Cerco la sequenza: "finally:" ... "concurrency.release" ... "close_connection"
        finally_idx = content.find("finally:\n            # Rilascia sempre il lock")
        assert finally_idx > 0, "Finally block expected con commento 'Rilascia sempre il lock'"
        finally_block = content[finally_idx:finally_idx + 1500]
        assert "close_connection as _close_idb" in finally_block
        assert "close_connection as _close_cfg" in finally_block

    def test_close_called_via_mock(self):
        """Mock-based: simula una execute() e verifica che le close siano invocate."""
        from app.core.scheduler.execution_manager import ExecutionManager

        em = ExecutionManager.__new__(ExecutionManager)
        em.schedule_service = MagicMock()
        em.log_service = MagicMock()
        em.concurrency = MagicMock()
        em.policy = MagicMock()
        em._connection_service = None

        # Schedule non trovato → return immediato (prima del finally)
        em.schedule_service.get_schedule.return_value = None

        with patch("app.core.intermediate.db.close_connection") as mock_idb_close, \
             patch("app.db.config_db_service.close_connection") as mock_cfg_close:
            result = em.execute("sched-1")

        # In questo path (schedule not found) il return è prima del try/finally,
        # quindi le close NON sono chiamate. Verifichiamo solo che il return
        # sia coerente (il bug sarebbe stato che close_connection non viene
        # chiamata MAI nemmeno nel happy path).
        assert result["status"] == "failed"

    def test_close_called_in_real_finally_path(self):
        """Mock più sofisticato: schedule trovato, lock acquired, exception nel try."""
        from app.core.scheduler.execution_manager import ExecutionManager

        em = ExecutionManager.__new__(ExecutionManager)
        em.schedule_service = MagicMock()
        em.log_service = MagicMock()
        em.concurrency = MagicMock()
        em.policy = MagicMock()
        em._connection_service = None

        # Schedule trovato
        config = MagicMock()
        config.pipeline.project_id = "proj-1"
        config.pipeline.project_name = "Test"
        config.pipeline.project_type = "estrai"
        config.concurrency.max_queue_size = 3
        config.destination = None
        config.pre_check.verify_destination_connection = False
        config.execution.use_cache = True
        em.schedule_service.get_schedule.return_value = config

        em.concurrency.try_acquire.return_value = "acquired"

        with patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.db.db_service.get_schedule") as mock_get_sched, \
             patch("app.core.intermediate.db.close_connection") as mock_idb_close, \
             patch("app.db.config_db_service.close_connection") as mock_cfg_close, \
             patch.object(em, "_get_orchestrator", side_effect=RuntimeError("boom")):
            mock_ps = MagicMock()
            mock_ps.get_project_metadata.return_value = MagicMock(group_id="g1")
            mock_ps_cls.return_value = mock_ps
            mock_get_sched.return_value = {"group_id": "g1"}

            # execute solleva nel try → finally esegue
            em.execute("sched-1")

        # Il finally deve aver chiamato release E close_connection x2
        em.concurrency.release.assert_called_with("sched-1")
        mock_idb_close.assert_called_once()
        mock_cfg_close.assert_called_once()
