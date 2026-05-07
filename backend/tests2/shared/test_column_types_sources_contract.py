"""
Contract test — fonti di tipo colonna nel canvas.

I "tipi canvas delle colonne" vivono in 3 viste parallele dentro lo
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# 1. Format contract — ogni vista ha struttura attesa


def test_columns_metadata_is_list_of_name_type_dicts(monkeypatch):
    """ProjectService.get_project_columns_metadata ritorna list[{name,type}]."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    fake_canvas = {
        "columns_metadata": [
            {"name": "id", "type": "int"},
            {"name": "nome", "type": "str"},
            {"name": "data", "type": "date"},
        ],
        "nodes": [], "edges": [],
    }
    fake_row = {"id": "p1", "canvas": fake_canvas}
    monkeypatch.setattr(ps_mod.project_db, "get_project", lambda pid: fake_row)

    result = ProjectService().get_project_columns_metadata("p1")
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, dict)
        assert "name" in item and isinstance(item["name"], str)
        assert "type" in item and isinstance(item["type"], str)


def test_columns_metadata_empty_when_missing(monkeypatch):
    """Progetto senza columns_metadata -> lista vuota (non None, non error)."""
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    monkeypatch.setattr(
        ps_mod.project_db, "get_project",
        lambda pid: {"id": "p1", "canvas": {"nodes": [], "edges": []}}
    )
    assert ProjectService().get_project_columns_metadata("p1") == []


def test_columns_metadata_handles_canvas_as_json_string(monkeypatch):
    """Backward compat: canvas serializzato come stringa JSON -> deserializza."""
    import json
    from app.services.project_service import ProjectService
    from app.services import project_service as ps_mod

    canvas_str = json.dumps({
        "columns_metadata": [{"name": "x", "type": "real"}],
        "nodes": [], "edges": [],
    })
    monkeypatch.setattr(
        ps_mod.project_db, "get_project",
        lambda pid: {"id": "p1", "canvas": canvas_str}
    )
    result = ProjectService().get_project_columns_metadata("p1")
    assert result == [{"name": "x", "type": "real"}]


# 2. Update contract — write/read idempotente


def test_update_columns_metadata_writes_to_canvas(monkeypatch):
    """update_project_columns_metadata salva dentro canvas["columns_metadata"],
    NON in colonna DB separata. E' un'invariante del contract da preservare.

    Post-refactor la scrittura passa da `update_project_canvas_key_atomic`
    (SELECT FOR UPDATE + merge + UPDATE) invece che da `update_project`
    su payload intero. L'invariante osservabile e' la stessa: la chiave
    "columns_metadata" dentro il canvas JSON."""
    from app.services.project_service import ProjectService
    # BE-GO-READY-DB-ENGINE-007: patch on the projects leaf where
    # the atomic helper now lives + where project_service imports it.
    from app.db.config import projects as _projects_leaf

    captured_calls = []

    def fake_atomic(project_id, key, value):
        captured_calls.append({"project_id": project_id, "key": key, "value": value})
        return True

    monkeypatch.setattr(_projects_leaf, "update_project_canvas_key_atomic", fake_atomic)

    new_meta = [{"name": "id", "type": "int"}]
    ok = ProjectService().update_project_columns_metadata("p1", new_meta)
    assert ok is True

    # Invariante: l'atomic helper viene invocato con key="columns_metadata"
    # sul progetto target. Niente scritture su altre colonne DB.
    assert len(captured_calls) == 1
    assert captured_calls[0]["project_id"] == "p1"
    assert captured_calls[0]["key"] == "columns_metadata"
    assert captured_calls[0]["value"] == new_meta


def test_update_columns_metadata_preserves_other_canvas_fields(monkeypatch):
    """Invariante critica: update non deve droppare nodes/edges esistenti.

    Post-refactor la preservazione e' intrinseca al contract di
    `update_project_canvas_key_atomic`, che fa SELECT FOR UPDATE + merge
    sul singolo key del canvas JSON (testato end-to-end in
    tests2/integration/test_project_columns_metadata_atomic_integration.py).
    Qui verifichiamo solo che il service non invii mai chiavi diverse da
    "columns_metadata" — scenari dove una regressione ri-serializza il
    canvas intero e droppa nodi/edge."""
    from app.services.project_service import ProjectService
    from app.db.config import projects as _projects_leaf

    captured_calls = []
    monkeypatch.setattr(
        _projects_leaf,
        "update_project_canvas_key_atomic",
        lambda pid, key, value: captured_calls.append({"pid": pid, "key": key, "value": value}) or True,
    )

    new_meta = [{"name": "new_col", "type": "int"}]
    ProjectService().update_project_columns_metadata("p1", new_meta)

    assert len(captured_calls) == 1
    assert captured_calls[0]["key"] == "columns_metadata"
    assert captured_calls[0]["value"] == new_meta


# 3. Consumer inventory — sentinel sui punti che leggono i tipi
# Se un refactor sposta la sorgente di verita', dobbiamo aggiornare
# questi consumer. I test fungono da sentinel: grep AST-based che
# assicura che i pattern noti continuino a esistere nel codebase.


def _module_references_any(module, names):
    """R-04b helper: True se almeno un callable top-level del modulo
    referenzia uno dei `names` (nei co_names del bytecode, anche
    ricorsivamente nei nested code objects). Refactor-resilient
    sentinel per "il modulo usa il helper X o Y"."""
    target_set = set(names)

    def _walk(co):
        if target_set & set(co.co_names):
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if hasattr(attr, "__code__") and _walk(attr.__code__):
            return True
        if inspect.isclass(attr):
            for m_name in dir(attr):
                m = getattr(attr, m_name, None)
                if hasattr(m, "__code__") and _walk(m.__code__):
                    return True
    return False


def test_estrai_still_updates_columns_metadata_post_run():
    """ESTRAI deve aggiornare columns_metadata dopo run.

    Replacement R-04b di `inspect.getsource(estrai_mod) + helper in src`:
    bytecode probe sui callee identifier (refactor-resilient).
    """
    from app.core.estrai import executor as estrai_mod
    assert _module_references_any(estrai_mod, [
        "save_columns_metadata_with_retry",
        "update_project_columns_metadata",
    ]), (
        "ESTRAI executor non aggiorna columns_metadata post-run. "
        "Senza, columns_metadata resta vuoto -> bridge N11 no-op."
    )


def test_arricchisci_still_updates_columns_metadata_post_run():
    """Analogo per ARRICCHISCI (refactor-resilient)."""
    from app.core.arricchisci import executor as enrich_mod
    assert _module_references_any(enrich_mod, [
        "save_columns_metadata_with_retry",
        "update_project_columns_metadata",
    ])


def test_column_mapper_reads_canvas_types_from_node_data():
    """ColumnMapper.auto_map riceve canvas_types dal node_data CARICA.
    Contract tra orchestrator e ColumnMapper."""
    from app.core.carica.column_mapper import ColumnMapper

    sig = inspect.signature(ColumnMapper.auto_map)
    assert "canvas_types" in sig.parameters, (
        "ColumnMapper.auto_map non accetta canvas_types. Qualsiasi refactor "
        "su questo contract deve essere intenzionale e coordinato."
    )


def test_enricher_bridge_reads_from_columns_metadata():
    """N11 enricher legge columns_metadata come sorgente di verita' semantica.

    Replacement R-04b di `inspect.getsource(enricher_mod) + str in src`:
    bytecode probe sui callee identifier.
    """
    from app.shared.execution import canvas_types_enricher as enricher_mod
    assert _module_references_any(enricher_mod, [
        "get_project_columns_metadata",
    ]), (
        "Enricher bridge non legge columns_metadata - N11 contract rotto."
    )


# 4. Format stability — tipi canvas ammessi
# I tipi canvas sono una enum informale esposta da
# `app.shared.resources.column_types.normalize_canvas_type` (delega del mapper).
# Qualsiasi refactor che tocca il vocabolario deve preservare i tipi base.


def test_canvas_type_vocabulary_stable():
    """Tipi canvas riconosciuti: int, real, str, bool, date, geometry.
    Se un refactor aggiunge/rimuove tipi, il frontend e il db mapper
    devono essere aggiornati in lockstep."""
    from app.core.carica.column_mapper import ColumnMapper

    mapper = ColumnMapper(db_type="postgres")
    recognized = set()
    for candidate in ["int", "real", "str", "bool", "date", "geometry",
                      "INT", "Real", "geometry:Point:4326"]:
        result = mapper._infer_type_from_canvas(candidate)
        if result:
            recognized.add(result)

    # Vocabolario minimo garantito
    expected = {"int", "real", "str", "bool", "date", "geometry"}
    missing = expected - recognized
    assert not missing, (
        f"Tipi canvas mancanti dal vocabolario: {missing}. "
        "Tutti i tipi base devono restare riconosciuti."
    )
