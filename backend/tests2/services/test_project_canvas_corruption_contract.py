"""Contract test — canvas corrotto → CanvasCorruptedError esplicito.

Reperto coperto: `ProjectService.get_project_canvas` restituiva un
`CanvasState()` vuoto in caso di canvas corrotto in DB (JSON
malformato, tipo inatteso, schema Pydantic non valido), loggando
solo un warning. Conseguenze:

  1. L'utente apriva il progetto, vedeva canvas vuoto, lo salvava
     sopra il contenuto corrotto → **perdita dati definitiva** (il
     corrotto veniva sovrascritto dal vuoto).
  2. Il warning nel log era facilmente perso: nessun segnale all'utente,
     nessun alert all'admin.
  3. Impossibile distinguere "progetto nuovo senza canvas" da "progetto
     con canvas corrotto" dalla UI.

Il fix: `get_project_canvas` solleva `CanvasCorruptedError` con
`project_id` e `reason` strutturato. Un handler globale in `main.py`
traduce l'eccezione in HTTP 500 con `error_code=canvas_corrupted` e
detail esplicito, cosi' l'utente sa che serve ripristino da backup.

Casi coperti:
  - Canvas valido dict → ok
  - Canvas None / {} / "" (progetto nuovo) → CanvasState vuoto, NO raise
  - Canvas string JSON valida → parsing ok
  - Canvas string JSON malformata → raise con reason "JSON malformato"
  - Canvas lista (tipo inatteso) → raise con reason "tipo inatteso"
  - Canvas con nodi di schema invalido → raise con reason "schema non valido"
  - Progetto inesistente (row=None) → None (non raise)
  - Verifica che original_error sia preservato per logging strutturato
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# =============================================================================
# Helpers
# =============================================================================


@pytest.fixture
def service_with_fake_db(monkeypatch):
    """Istanzia ProjectService con db.get_project patchato.

    Il fake restituisce `_canvas_fixture` (var dict) cosi' ogni test
    setta il valore di canvas e chiama get_project_canvas."""
    import app.db.db_service as real_db
    from app.services.project_service import ProjectService

    state = {"canvas": None, "project_exists": True}

    def fake_get_project(project_id, group_id=None):
        if not state["project_exists"]:
            return None
        # Simula il campo canvas come arriva dal DB
        return {
            "id": project_id,
            "name": "Test",
            "canvas": state["canvas"],
            "project_type": "manipola",
        }

    monkeypatch.setattr(real_db, "get_project", fake_get_project)
    return ProjectService(), state


# =============================================================================
# Canvas valido — nessuna eccezione
# =============================================================================


def test_canvas_valid_dict_parses_ok(service_with_fake_db):
    svc, state = service_with_fake_db
    state["canvas"] = {"nodes": [], "edges": []}
    canvas = svc.get_project_canvas("p1")
    assert canvas is not None
    assert canvas.nodes == []
    assert canvas.edges == []


def test_canvas_valid_json_string_parses_ok(service_with_fake_db):
    """Supporto legacy: canvas salvato come stringa JSON."""
    svc, state = service_with_fake_db
    state["canvas"] = '{"nodes": [], "edges": []}'
    canvas = svc.get_project_canvas("p1")
    assert canvas is not None
    assert canvas.nodes == []


# =============================================================================
# Canvas vuoto legittimo — NO raise, NO log error
# =============================================================================


@pytest.mark.parametrize("empty_canvas", [None, {}, ""])
def test_canvas_empty_is_not_corruption(service_with_fake_db, empty_canvas):
    """None / {} / "" = progetto mai salvato, canvas legittimo vuoto.
    Non deve sollevare CanvasCorruptedError."""
    svc, state = service_with_fake_db
    state["canvas"] = empty_canvas
    canvas = svc.get_project_canvas("p1")
    assert canvas is not None
    assert canvas.nodes == []
    assert canvas.edges == []


def test_canvas_json_string_null_literal_is_not_corruption(service_with_fake_db):
    """La stringa '"null"' (JSON null literal) dopo parse → None → canvas vuoto."""
    svc, state = service_with_fake_db
    state["canvas"] = "null"
    # json.loads("null") → None → ramo canvas vuoto
    canvas = svc.get_project_canvas("p1")
    assert canvas is not None
    assert canvas.nodes == []


def test_canvas_json_string_empty_dict_is_not_corruption(service_with_fake_db):
    svc, state = service_with_fake_db
    state["canvas"] = "{}"
    canvas = svc.get_project_canvas("p1")
    assert canvas is not None
    assert canvas.nodes == []


# =============================================================================
# Progetto inesistente — None, non raise
# =============================================================================


def test_missing_project_returns_none(service_with_fake_db):
    svc, state = service_with_fake_db
    state["project_exists"] = False
    result = svc.get_project_canvas("p-missing")
    assert result is None


# =============================================================================
# Canvas corrotto — raise CanvasCorruptedError con reason esplicito
# =============================================================================


def test_corrupted_json_string_raises(service_with_fake_db):
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    state["canvas"] = '{"nodes": [invalid json'

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    err = exc_info.value
    assert err.project_id == "p1"
    assert "JSON malformato" in err.reason
    # original_error preservato per logging strutturato
    import json
    assert isinstance(err.original_error, json.JSONDecodeError)


def test_canvas_wrong_type_list_raises(service_with_fake_db):
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    state["canvas"] = ["not", "a", "dict"]

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    assert exc_info.value.project_id == "p1"
    assert "tipo inatteso" in exc_info.value.reason
    assert "list" in exc_info.value.reason


def test_canvas_wrong_type_int_raises(service_with_fake_db):
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    state["canvas"] = 42

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    assert "tipo inatteso" in exc_info.value.reason
    assert "int" in exc_info.value.reason


def test_canvas_json_string_decoded_to_list_raises(service_with_fake_db):
    """Edge case: canvas e' una stringa JSON che dopo parse diventa lista.
    Deve essere detectato come tipo inatteso, non come JSON malformato."""
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    state["canvas"] = '["foo", "bar"]'

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    assert "tipo inatteso" in exc_info.value.reason


def test_canvas_invalid_pydantic_schema_raises(service_with_fake_db):
    """Canvas con struttura dict ma campi non rispettano lo schema
    Pydantic di CanvasState (es. `nodes` non e' una lista ma un dict)."""
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    # nodes dovrebbe essere List[Node], passiamo un dict → Pydantic ValidationError
    state["canvas"] = {"nodes": {"not": "a list"}, "edges": []}

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    assert exc_info.value.project_id == "p1"
    assert "schema non valido" in exc_info.value.reason
    # Reason troncato sotto 500 char (verificato limite 300 + "...")
    assert len(exc_info.value.reason) <= 500


def test_canvas_invalid_node_structure_raises(service_with_fake_db):
    """Canvas con nodi che non rispettano lo schema Node → corruzione."""
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    # Node richiede almeno id e type: passiamo dict senza chiavi richieste
    state["canvas"] = {
        "nodes": [{"random": "field"}],
        "edges": [],
    }

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("p1")

    assert "schema non valido" in exc_info.value.reason


# =============================================================================
# Reason non deve leakare contenuto sensibile
# =============================================================================


def test_corrupted_error_str_includes_project_id(service_with_fake_db):
    """L'__str__ dell'eccezione e' usato in logging. Deve contenere
    project_id e reason, non altro."""
    from app.shared.exceptions import CanvasCorruptedError

    svc, state = service_with_fake_db
    state["canvas"] = '{"broken'

    with pytest.raises(CanvasCorruptedError) as exc_info:
        svc.get_project_canvas("project-abc-123")

    msg = str(exc_info.value)
    assert "project-abc-123" in msg
    assert "JSON malformato" in msg


# =============================================================================
# Regressione: il fallback silenzioso a CanvasState() NON deve tornare
# =============================================================================


def test_corrupted_canvas_never_returns_empty_silently(service_with_fake_db):
    """Regressione diretta del reperto: pre-fix, questi input restituivano
    un CanvasState() vuoto senza sollevare. Post-fix, tutti devono
    sollevare CanvasCorruptedError."""
    from app.shared.exceptions import CanvasCorruptedError
    from app.models import CanvasState

    svc, state = service_with_fake_db

    corrupted_inputs = [
        '{"broken json',
        ["not", "a", "dict"],
        42,
        {"nodes": "not a list", "edges": []},
    ]

    for bad in corrupted_inputs:
        state["canvas"] = bad
        with pytest.raises(CanvasCorruptedError):
            result = svc.get_project_canvas("p1")
            # Questo branch non deve mai essere raggiunto — se lo fosse,
            # significherebbe che il fallback silenzioso e' tornato.
            pytest.fail(
                f"Canvas corrotto {bad!r} NON ha sollevato — regressione "
                f"al pre-fix comportamento silent-fallback. Got: {result!r}"
            )
