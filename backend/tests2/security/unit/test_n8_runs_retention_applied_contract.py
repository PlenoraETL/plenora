"""Contract test N8 — apply_runs_retention invocato dopo close_run.

Pre-fix: `apply_runs_retention()` in `intermediate/db.py:330` era definita
ma mai chiamata da alcun callsite. Risultato: `intermediate.runs`,
`node_meta` e `node_rows` crescevano indefinitamente causando disk fill
sul server nel tempo (non un crash, ma accumulo silenzioso).

Fix (2026-04-17): chiamata inline in `writer.close_run` subito dopo
`db.update_run(...)` che marca la run come 'completed'/'failed'.
Best-effort: un errore nella retention non rollbacka l'update di stato.

Questo contract congela due cose:
  1. apply_runs_retention esiste ed e' referenziata dal writer (source check)
  2. L'invocazione parte DOPO update_run (logica di close), non prima
     (non eliminare righe che ancora stiamo aggiornando)
  3. Un fallimento di retention non propaga l'eccezione fuori da close_run
     (il task non deve morire per un cleanup non riuscito)
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_apply_runs_retention_is_called_from_close_run():
    """Il source di writer.close_run deve referenziare apply_runs_retention."""
    from app.core.intermediate.writer import close_run

    src = inspect.getsource(close_run)
    assert "apply_runs_retention" in src, (
        "writer.close_run non chiama apply_runs_retention. La funzione "
        "esiste in intermediate/db.py ma nessuno la invoca -> disk fill "
        "nel tempo su intermediate.runs + node_meta + node_rows."
    )


def test_retention_called_after_update_run_not_before():
    """apply_runs_retention deve essere invocata DOPO db.update_run, non
    prima: una retention prematura eliminerebbe righe che stiamo ancora
    aggiornando con il nuovo status/hash/stats."""
    from app.core.intermediate.writer import close_run

    src = inspect.getsource(close_run)
    # Trova le posizioni delle due chiamate e verifica l'ordine
    idx_update = src.find("db.update_run(")
    idx_retention = src.find("apply_runs_retention")
    assert idx_update > -1 and idx_retention > -1, (
        "Almeno una delle due chiamate manca nel source."
    )
    assert idx_retention > idx_update, (
        "apply_runs_retention deve essere chiamata DOPO db.update_run, "
        "non prima (altrimenti cancella righe non ancora finalizzate)."
    )


def test_close_run_invokes_retention_with_correct_args(monkeypatch):
    """Test funzionale: close_run fa list_node_meta + update_run + get_run +
    apply_runs_retention(canvas_id, group_id) nell'ordine giusto."""
    from app.core.intermediate import writer as wr

    calls = []

    def spy_list_node_meta(run_id):
        calls.append(("list_node_meta", run_id))
        return [{"status": "ok", "row_count": 10}]

    def spy_update_run(run_id, **kwargs):
        calls.append(("update_run", run_id, kwargs.get("status")))

    def spy_get_run(run_id):
        calls.append(("get_run", run_id))
        return {"canvas_id": "proj_1", "group_id": "group_A"}

    def spy_retention(canvas_id, group_id):
        calls.append(("apply_runs_retention", canvas_id, group_id))

    monkeypatch.setattr(wr.db, "list_node_meta", spy_list_node_meta)
    monkeypatch.setattr(wr.db, "update_run", spy_update_run)
    monkeypatch.setattr(wr.db, "get_run", spy_get_run)
    monkeypatch.setattr(wr.db, "apply_runs_retention", spy_retention)

    wr.close_run(run_id="r1", input_hash="a", output_hash="b",
                 total_exec_time=1.23, success=True)

    # Sequenza attesa: list_node_meta -> update_run -> get_run -> apply_runs_retention
    ops = [c[0] for c in calls]
    assert "update_run" in ops
    assert "apply_runs_retention" in ops
    # retention DOPO update_run
    assert ops.index("apply_runs_retention") > ops.index("update_run")
    # retention con i canvas_id/group_id corretti dal get_run
    retention_call = next(c for c in calls if c[0] == "apply_runs_retention")
    assert retention_call[1:] == ("proj_1", "group_A")


def test_retention_failure_does_not_propagate(monkeypatch):
    """Se apply_runs_retention esplode (DB lock, constraint, ecc.), la
    chiusura della run DEVE comunque ritornare senza propagare: un
    cleanup fallito non deve rompere il task."""
    from app.core.intermediate import writer as wr

    monkeypatch.setattr(wr.db, "list_node_meta", lambda run_id: [])
    monkeypatch.setattr(wr.db, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(wr.db, "get_run", lambda rid: {"canvas_id": "p", "group_id": "g"})

    def boom(*a, **k):
        raise RuntimeError("DB lock during retention")
    monkeypatch.setattr(wr.db, "apply_runs_retention", boom)

    # Non deve sollevare — il fallimento e' loggato come warning
    wr.close_run(run_id="r1", input_hash="a", output_hash="b",
                 total_exec_time=1.0, success=True)


def test_apply_runs_retention_signature_stable():
    """Signature stabile di apply_runs_retention: (canvas_id, group_id)."""
    from app.core.intermediate.db import apply_runs_retention

    sig = inspect.signature(apply_runs_retention)
    params = list(sig.parameters.keys())
    assert params[:2] == ["canvas_id", "group_id"], (
        f"apply_runs_retention signature cambiata: {params}. "
        "Breaking per writer.close_run che la chiama positional."
    )
