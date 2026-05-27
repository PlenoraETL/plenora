"""
Contract test per `app.shared.execution.canvas_types`.

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

from app.shared.execution import canvas_types as ct


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
    Lock post fix-drift: ["abort", "skip", "force"].

    Pre-fix il canvas Pydantic accettava "update" — bug pre-esistente,
    valore non corrispondente a nulla nell'executor
    `ConflictPolicy` (SKIP/FORCE/ABORT). Il valore canonico ora e'
    "force", allineato all'executor e al FE
    (`frontend/src/constants/canvasTypes.js`).

    Il validator `Node._normalize_conflict_policy` rimappa
    "update" -> "force" per preservare canvas legacy eventualmente
    salvati con il valore sbagliato (vedi
    LEGACY_CONFLICT_POLICY_ALIASES).
    """
    assert set(ct.VALID_CONFLICT_POLICIES) == {"abort", "skip", "force"}


def test_conflict_policy_legacy_alias_update_maps_to_force():
    """Lock del mapping legacy "update" -> "force"."""
    assert ct.LEGACY_CONFLICT_POLICY_ALIASES == {"update": "force"}


def test_rest_methods_lockin():
    """
    Lock post fix-drift: POST/PUT/PATCH. DELETE rimosso — il FE non
    l'ha mai esposto in UI e la semantica CARICA su DELETE non era
    definita. Canvas legacy con rest_method="DELETE" (solo bypass
    scripts, mai dal FE) vengono ora rifiutati dal Pydantic; nessun
    validator legacy aggiunto perche` la fattispecie e' teorica.
    """
    assert set(ct.VALID_REST_METHODS) == {"POST", "PUT", "PATCH"}


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


def _make_carica_node(conflict_policy: str):
    """Helper: minimal Node payload con dest_type=db e una conflict_policy."""
    from app.core.projects.canvas_models import Node
    return Node.model_validate({
        "id": "n1",
        "type": "carica",
        "position": {"x": 0, "y": 0},
        "dest_type": "db",
        "conflict_policy": conflict_policy,
    })


def test_conflict_policy_legacy_update_maps_to_force():
    """Canvas legacy con conflict_policy="update" caricano come "force"."""
    node = _make_carica_node("update")
    assert node.conflict_policy == "force"


def test_conflict_policy_canonical_force_passes_through():
    node = _make_carica_node("force")
    assert node.conflict_policy == "force"


def test_conflict_policy_canonical_abort_passes_through():
    node = _make_carica_node("abort")
    assert node.conflict_policy == "abort"


def test_conflict_policy_canonical_skip_passes_through():
    node = _make_carica_node("skip")
    assert node.conflict_policy == "skip"


def test_conflict_policy_unknown_value_raises():
    """Valori fuori canonical e fuori legacy alias falliscono validation."""
    import pytest as _pytest
    from pydantic import ValidationError
    with _pytest.raises(ValidationError):
        _make_carica_node("garbage_policy")
