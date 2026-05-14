"""
Contract test per `app.shared.canvas_types`.

Lock-in dei valori dei `Literal` shared FE↔BE: il modulo canvas_types
e' la single source of truth. Una modifica accidentale (rimozione,
rinomina, riordino case-sensitive) verrebbe rilevata qui prima del
deploy. Un'aggiunta (additive) richiede di bumpare il test E la
costante FE quando il mirror JS sara` in piedi.

Convention:
    - aggiungere un valore al frozenset atteso PRIMA di aggiungerlo
      al Literal della source. CI fallisce → bump test + mirror FE.
"""

from __future__ import annotations

import pytest

from app.shared import canvas_types as ct


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_node_types_lockin():
    """Lock dei 14 node types attualmente supportati dal canvas."""
    assert set(ct.VALID_NODE_TYPES) == {
        "source", "result", "project_source", "file_source", "feature_source",
        "transform", "api_enrich", "staticValues", "carica", "carica_arcgis",
        "multi_join", "comment", "datasource_node", "imdf_publisher",
    }
    # Sentinel: se qualcuno cambia un caso (es. "result" -> "Result") il
    # canvas legacy rompe — qui catturiamo prima del deploy.
    assert "result" in ct.VALID_NODE_TYPES
    assert "carica_arcgis" in ct.VALID_NODE_TYPES  # snake_case canonico


def test_column_types_base_lockin():
    assert set(ct.VALID_COLUMN_TYPES_BASE) == {
        "int", "real", "str", "bool", "date", "geometry",
    }


def test_write_modes_lockin():
    assert set(ct.VALID_WRITE_MODES) == {
        "create", "replace", "append", "upsert", "update", "truncate_insert",
    }


def test_dest_types_lockin():
    assert set(ct.VALID_DEST_TYPES) == {"db", "arcgis", "rest"}


def test_transaction_modes_lockin():
    assert set(ct.VALID_TRANSACTION_MODES) == {"all_or_nothing", "per_batch"}


def test_conflict_policies_lockin():
    """
    Lock attuale: ["abort", "skip", "update"].

    DRIFT NOTO (separato da questo task di centralizzazione): il FE
    invia "force" e l'enum executor `ConflictPolicy.FORCE` di
    `app.core.carica.models` usa "force" come canonico. Canvas
    Pydantic accetta "update" — possibile bug pre-esistente. Il fix
    richiede:
        (a) decidere il valore canonico (probabile: "force")
        (b) migration sui canvas legacy con conflict_policy="update"
        (c) bump qui + bump in canvas_models.py + mirror FE
    Fuori scope di questo lockin.
    """
    assert set(ct.VALID_CONFLICT_POLICIES) == {"abort", "skip", "update"}


def test_rest_methods_lockin():
    """
    Lock attuale: POST/PUT/PATCH/DELETE.

    DRIFT NOTO: il FE espone solo POST/PUT/PATCH (RestDestinationSection
    REST_METHODS). DELETE e' accettato dal BE ma non offerto in UI —
    intenzionale perche' CARICA su DELETE non ha semantica (cancella
    cosa?). Da decidere se rimuovere "DELETE" dal Literal canvas o
    aggiungerlo all'UI.
    """
    assert set(ct.VALID_REST_METHODS) == {"POST", "PUT", "PATCH", "DELETE"}


def test_rest_batch_modes_lockin():
    assert set(ct.VALID_REST_BATCH_MODES) == {"batch", "per_record"}


def test_multi_join_types_lockin():
    assert set(ct.VALID_MULTI_JOIN_TYPES) == {"inner", "left", "outer"}


def test_canvas_models_imports_centralized_types():
    """Verifica che canvas_models.py usi i TypeAlias centralizzati."""
    import app.core.projects.canvas_models as cm

    # Il modello Node deve avere `type: NodeTypeLiteral` (stesso tipo
    # del modulo shared, non un Literal anonimo locale).
    node_fields = cm.Node.model_fields
    assert "type" in node_fields
    # Pydantic v2 preserva il TypeAlias originale in `annotation`.
    assert node_fields["type"].annotation is ct.NodeTypeLiteral
