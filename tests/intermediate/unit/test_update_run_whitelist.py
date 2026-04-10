"""
tests/intermediate/unit/test_update_run_whitelist.py
====================================================
Unit test per il fix HIGH su `update_run` in app.core.intermediate.db.

Verifica che la whitelist `_ALLOWED_RUN_UPDATE_FIELDS` sia applicata e che
campi sensibili (canvas_id, group_id, run_id, pinned) non possano essere
aggiornati via update_run.
"""

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


class TestUpdateRunWhitelist:

    def test_allowed_status(self):
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            update_run("run-1", status="completed")
        assert mock_exec.called
        sql = mock_exec.call_args.args[0]
        assert "UPDATE runs SET status = ?" in sql

    def test_allowed_full_completion_set(self):
        """Tutti i campi che `intermediate.writer.close_run` aggiorna."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            update_run(
                "run-1",
                status="completed",
                input_hash="aaa",
                output_hash="bbb",
                total_exec_time=1.5,
                total_rows_in=100,
                total_rows_out=95,
                total_rows_dropped=5,
                nodes_ok=3,
                nodes_error=0,
                nodes_skipped=0,
            )
        assert mock_exec.called

    def test_rejects_canvas_id_change(self):
        """canvas_id non si tocca: cambierebbe ownership della run."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(ValueError) as exc:
                update_run("run-1", canvas_id="hijacked")
        assert "canvas_id" in str(exc.value)
        assert not mock_exec.called

    def test_rejects_group_id_change(self):
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(ValueError):
                update_run("run-1", group_id="other-group")
        assert not mock_exec.called

    def test_run_id_protected_by_signature(self):
        """run_id è il primo arg posizionale: passarlo come kwarg → TypeError
        di Python, ben prima del check whitelist. Doppia protezione."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(TypeError):
                update_run("run-1", run_id="other-run")
        assert not mock_exec.called

    def test_rejects_pinned_via_update_run(self):
        """pinned ha il suo helper dedicato pin_run, non update_run."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(ValueError):
                update_run("run-1", pinned=1)
        assert not mock_exec.called

    def test_rejects_timestamp_change(self):
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(ValueError):
                update_run("run-1", timestamp="2026-01-01T00:00:00")
        assert not mock_exec.called

    def test_empty_fields_no_op(self):
        """update_run senza campi non tocca il DB."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            update_run("run-1")
        assert not mock_exec.called

    def test_partial_disallowed_rejects_all(self):
        """Anche un solo campo non consentito blocca l'intera UPDATE."""
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            with pytest.raises(ValueError):
                update_run("run-1", status="completed", canvas_id="bad")
        assert not mock_exec.called

    def test_run_id_in_where_clause(self):
        from app.core.intermediate.db import update_run
        with patch("app.core.intermediate.db.execute") as mock_exec:
            update_run("run-xyz", status="failed")
        sql, params = mock_exec.call_args.args
        assert "WHERE run_id = ?" in sql
        assert params[-1] == "run-xyz"
