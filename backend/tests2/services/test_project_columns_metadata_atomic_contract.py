"""
Contract test — update_project_columns_metadata e' atomico (FOR UPDATE).

Reperto coperto: il vecchio `update_project_columns_metadata` faceva
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixture — mock della pg transaction()


@pytest.fixture
def mock_pg_conn(monkeypatch):
    """Sostituisce `transaction()` con un mock che registra l'ordine
    delle SQL eseguite sul cursor.

    BE-GO-READY-DB-ENGINE-007: la funzione atomica vive ora nel leaf
    `app.db.config.projects`, quindi patch `transaction` sul leaf
    (non piu' sul facade `config_db_service`)."""
    from app.db.config import projects as projects_leaf
    from contextlib import contextmanager

    executed: list[tuple[str, tuple]] = []
    state = {
        "canvas_in_db": json.dumps({"nodes": [{"id": "n1"}], "edges": []}),
        "project_exists": True,
    }

    class FakeCursor:
        def __init__(self):
            self._last_result = None

        def execute(self, sql, params=()):
            executed.append((sql, params))
            sql_lower = sql.lower()
            if "select canvas" in sql_lower:
                if state["project_exists"]:
                    self._last_result = {"canvas": state["canvas_in_db"]}
                else:
                    self._last_result = None
            elif sql_lower.startswith("update projects set canvas"):
                # Simula il write: canvas_in_db = params[0]
                state["canvas_in_db"] = params[0]

        def fetchone(self):
            r = self._last_result
            self._last_result = None
            return r

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        yield FakeConn()

    monkeypatch.setattr(projects_leaf, "transaction", fake_transaction)

    return {"executed": executed, "state": state}


# SEQUENCE — SELECT FOR UPDATE deve precedere UPDATE, entrambi in transaction


def test_select_for_update_precedes_update(mock_pg_conn):
    from app.db import config_db_service as cfg_db

    result = cfg_db.update_project_canvas_key_atomic(
        "p1", "columns_metadata", [{"name": "a", "type": "str"}]
    )
    assert result is True

    executed = mock_pg_conn["executed"]
    assert len(executed) == 2
    select_sql, select_params = executed[0]
    update_sql, update_params = executed[1]

    # SELECT FOR UPDATE e' la PRIMA statement
    assert "select canvas" in select_sql.lower()
    assert "for update" in select_sql.lower()
    assert select_params == ("p1",)

    # UPDATE e' la SECONDA statement
    assert update_sql.lower().startswith("update projects set canvas")
    # Il primo param e' il canvas JSON serializzato completo
    new_canvas = json.loads(update_params[0])
    assert new_canvas["columns_metadata"] == [{"name": "a", "type": "str"}]
    # Campi esistenti preservati (merge, non overwrite intero canvas)
    assert new_canvas["nodes"] == [{"id": "n1"}]
    assert new_canvas["edges"] == []


# MERGE — scrivere una key non deve cancellare le altre


def test_writing_columns_metadata_preserves_nodes_and_edges(mock_pg_conn):
    from app.db import config_db_service as cfg_db

    mock_pg_conn["state"]["canvas_in_db"] = json.dumps({
        "nodes": [{"id": "n1", "type": "source"}, {"id": "n2"}],
        "edges": [{"source": "n1", "target": "n2"}],
        "retention_n": 5,
    })

    cfg_db.update_project_canvas_key_atomic(
        "p1", "columns_metadata", [{"name": "col1", "type": "int"}]
    )

    final_canvas = json.loads(mock_pg_conn["state"]["canvas_in_db"])
    assert final_canvas["columns_metadata"] == [{"name": "col1", "type": "int"}]
    assert final_canvas["nodes"] == [
        {"id": "n1", "type": "source"},
        {"id": "n2"},
    ]
    assert final_canvas["edges"] == [{"source": "n1", "target": "n2"}]
    # Altre chiavi non toccate
    assert final_canvas["retention_n"] == 5


# PROJECT NOT FOUND — False, no crash


def test_project_not_found_returns_false(mock_pg_conn):
    from app.db import config_db_service as cfg_db
    mock_pg_conn["state"]["project_exists"] = False

    result = cfg_db.update_project_canvas_key_atomic(
        "p-ghost", "columns_metadata", []
    )
    assert result is False
    # Nessuna UPDATE eseguita — solo il SELECT
    executed = mock_pg_conn["executed"]
    assert len(executed) == 1
    assert "select" in executed[0][0].lower()


# CANVAS CORROTTO — raise, no silent overwrite


def test_corrupted_canvas_raises_instead_of_overwriting(mock_pg_conn):
    """Se il canvas in DB e' JSON malformato, la funzione atomica deve
    sollevare invece di sovrascriverlo. Allineato con la policy della
    sezione canvas corruption (reperto precedente): mai sovrascrivere
    silent i dati originali."""
    from app.db import config_db_service as cfg_db
    mock_pg_conn["state"]["canvas_in_db"] = '{"broken json'

    with pytest.raises(ValueError, match="Canvas corrotto"):
        cfg_db.update_project_canvas_key_atomic(
            "p1", "columns_metadata", []
        )

    # NESSUN UPDATE eseguito (rollback automatico via transaction)
    update_statements = [
        (s, p) for s, p in mock_pg_conn["executed"]
        if s.lower().strip().startswith("update")
    ]
    assert len(update_statements) == 0


def test_canvas_unexpected_type_raises(mock_pg_conn):
    from app.db import config_db_service as cfg_db
    # Un tipo DB inatteso (numero grezzo) → raise
    mock_pg_conn["state"]["canvas_in_db"] = 42

    with pytest.raises(ValueError, match="tipo inatteso"):
        cfg_db.update_project_canvas_key_atomic(
            "p1", "columns_metadata", []
        )


def test_canvas_null_treated_as_empty_dict(mock_pg_conn):
    """Canvas=NULL in DB: progetto appena creato con canvas non ancora
    salvato. La funzione deve trattarlo come dict vuoto e salvare la key."""
    from app.db import config_db_service as cfg_db
    mock_pg_conn["state"]["canvas_in_db"] = None

    result = cfg_db.update_project_canvas_key_atomic(
        "p1", "columns_metadata", [{"name": "x", "type": "str"}]
    )
    assert result is True
    final_canvas = json.loads(mock_pg_conn["state"]["canvas_in_db"])
    assert final_canvas == {"columns_metadata": [{"name": "x", "type": "str"}]}


# project_service.update_project_columns_metadata delega alla versione atomica


def test_project_service_delegates_to_atomic_helper(monkeypatch):
    """BE-GO-READY-DB-ENGINE-007: project_service now imports
    `update_project_canvas_key_atomic` from the projects leaf
    directly (lazy), so the patch target is the leaf module
    rather than the `config_db_service` facade."""
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf

    captured = {}

    def fake_atomic(project_id, key, value):
        captured["project_id"] = project_id
        captured["key"] = key
        captured["value"] = value
        return True

    monkeypatch.setattr(projects_leaf, "update_project_canvas_key_atomic", fake_atomic)

    svc = ProjectService()
    meta = [{"name": "c1", "type": "int"}]
    result = svc.update_project_columns_metadata("proj-42", meta)

    assert result is True
    assert captured["project_id"] == "proj-42"
    assert captured["key"] == "columns_metadata"
    assert captured["value"] == meta


def test_project_service_propagates_atomic_exception(monkeypatch):
    """Se la funzione atomica solleva (canvas corrotto, DB error, ecc.),
    il service deve PROPAGARE al caller, non ingoiare con `return False`.

    Regressione diretta del baco: pre-fix, `update_project_columns_metadata`
    aveva un try/except Exception → ritornava False. L'executor ESTRAI
    non vedeva l'eccezione, il retry su transient non partiva, nessun
    `ColumnsMetadataSaveError` veniva mai sollevato, e `res` risultava
    scritto come se fosse successo (falso positivo).

    Post-fix il service propaga: i caller (ESTRAI executor con retry,
    oppure altri caller con try/except locale) decidono cosa fare.
    """
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf

    def raising_atomic(*args, **kwargs):
        raise ValueError("Canvas corrotto per progetto xyz")

    monkeypatch.setattr(projects_leaf, "update_project_canvas_key_atomic", raising_atomic)

    svc = ProjectService()
    # DEVE propagare, NON tornare False silenziosamente
    with pytest.raises(ValueError, match="Canvas corrotto"):
        svc.update_project_columns_metadata("proj-42", [])


def test_project_service_propagates_transient_db_error(monkeypatch):
    """OperationalError (transient) propagato al caller → il retry
    dell'executor ESTRAI puo' agire. Pre-fix veniva ingoiato."""
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf
    import psycopg2

    def transient_fail(*args, **kwargs):
        raise psycopg2.OperationalError("pool exhausted")

    monkeypatch.setattr(projects_leaf, "update_project_canvas_key_atomic", transient_fail)

    svc = ProjectService()
    with pytest.raises(psycopg2.OperationalError):
        svc.update_project_columns_metadata("proj-42", [])


def test_project_service_returns_false_when_project_missing(monkeypatch):
    """Unico caso in cui il service ritorna False (non solleva):
    la funzione atomica ha ritornato False (progetto inesistente).
    Questo NON e' un errore, e' un segnale pulito al caller."""
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf

    monkeypatch.setattr(
        projects_leaf, "update_project_canvas_key_atomic",
        lambda *a, **kw: False,  # progetto non trovato
    )

    svc = ProjectService()
    result = svc.update_project_columns_metadata("p-ghost", [])
    assert result is False


def test_project_service_returns_true_on_success(monkeypatch):
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf

    monkeypatch.setattr(
        projects_leaf, "update_project_canvas_key_atomic",
        lambda *a, **kw: True,
    )

    svc = ProjectService()
    result = svc.update_project_columns_metadata("p1", [{"name": "a", "type": "int"}])
    assert result is True
