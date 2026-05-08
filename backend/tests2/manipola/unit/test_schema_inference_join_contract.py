"""
Contract test — `SchemaInferenceService._schema_join` suffix logic.

Bug #40 (V1 baseline 2026-04-17): la condizione ternaria che decideva
"""

from __future__ import annotations

import pytest

from app.core.projects.schema_inference import (
    ColumnSchema,
    SchemaInferenceService,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _col(name: str, ctype: str = "str") -> ColumnSchema:
    return ColumnSchema(name=name, type=ctype, source_node=None)


def _join(left, right, *, left_on, right_on):
    from unittest.mock import MagicMock
    svc = SchemaInferenceService(project_service=MagicMock())
    return svc._schema_join(
        input_schemas=[left, right],
        config={"left_on": left_on, "right_on": right_on},
        node_id="join_node",
        edges_by_target={},  # non usato dal merge logic, basta dict vuoto
    )


# Different keys: right_on resta col nome originale (BUG #40)


def test_different_keys_right_join_key_keeps_original_name():
    """left_on != right_on: la chiave destra DEVE restare 'id_right',
    non 'id_right_R' (questo era il bug pre-fix)."""
    left = [_col("id_left"), _col("name_l")]
    right = [_col("id_right"), _col("name_r")]
    result = _join(left, right, left_on="id_left", right_on="id_right")

    names = [c.name for c in result]
    # Sinistra
    assert "id_left" in names      # left_on senza suffisso
    assert "name_l_L" in names     # altre col sinistra → _L
    # Destra
    assert "id_right" in names     # right_on senza suffisso (FIX)
    assert "id_right_R" not in names  # bug regression guard
    assert "name_r_R" in names     # altre col destra → _R


def test_different_keys_no_collision_no_duplicates():
    """Schema risultante non deve avere nomi duplicati."""
    left = [_col("id_left"), _col("attr_a")]
    right = [_col("id_right"), _col("attr_b")]
    result = _join(left, right, left_on="id_left", right_on="id_right")

    names = [c.name for c in result]
    assert len(names) == len(set(names)), f"Nomi duplicati: {names}"


# Same keys: join key di destra skipped (no duplicato)


def test_same_keys_skips_right_join_key():
    """left_on == right_on == 'id': la join key destra e' skip-pata
    (sarebbe duplicato della sinistra)."""
    left = [_col("id"), _col("name_l")]
    right = [_col("id"), _col("name_r")]
    result = _join(left, right, left_on="id", right_on="id")

    names = [c.name for c in result]
    assert "id" in names              # da sinistra (left_on)
    assert "name_l_L" in names        # altre col sinistra
    assert "name_r_R" in names        # altre col destra
    # NESSUN duplicato 'id' o 'id_R'
    assert names.count("id") == 1
    assert "id_R" not in names


# Tipi e is_calculated preservati


def test_column_metadata_preserved_through_suffix_rename():
    """Type e is_calculated della colonna originale restano dopo
    l'aggiunta del suffisso."""
    left = [
        ColumnSchema(name="key", type="int", is_calculated=False),
        ColumnSchema(name="formula_col", type="real", is_calculated=True),
    ]
    right = [
        ColumnSchema(name="key", type="int", is_calculated=False),
        ColumnSchema(name="qty", type="int", is_calculated=False),
    ]
    result = _join(left, right, left_on="key", right_on="key")

    by_name = {c.name: c for c in result}
    assert by_name["formula_col_L"].type == "real"
    assert by_name["formula_col_L"].is_calculated is True
    assert by_name["qty_R"].type == "int"
    assert by_name["qty_R"].is_calculated is False


# Source node tagged


def test_result_columns_tagged_with_join_node_id():
    left = [_col("a"), _col("b")]
    right = [_col("c"), _col("d")]
    result = _join(left, right, left_on="a", right_on="c")
    for col in result:
        assert col.source_node == "join_node"
