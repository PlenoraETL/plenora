"""
Contract test — RB.3 (2026-04-27) duplicate columns nella preview ESTRAI.

BUG.md #6:
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.estrai.executor import (
    EstraiExecutor,
    EstraiPreviewResource,
    _build_db_preview_resource_from_cursor_schema,
    _build_preview_resource,
    _dedup_column_names,
    _dedup_dataframe_columns_for_serialization,
)
from app.shared.resources.metadata import ResourceMetadata


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# _dedup_column_names: helper puro, deterministico


def test_dedup_column_names_no_duplicates_is_noop():
    """Senza duplicati: nessuna rinomina, nessun rename."""
    cols = ["id", "nome", "email"]
    new_cols, renames = _dedup_column_names(cols)
    assert new_cols == cols
    assert renames == []


def test_dedup_column_names_simple_duplicate():
    """Caso base: due colonne 'id' -> 'id', 'id_2'."""
    cols = ["id", "nome", "id"]
    new_cols, renames = _dedup_column_names(cols)
    assert new_cols == ["id", "nome", "id_2"]
    assert renames == [("id", "id_2")]


def test_dedup_column_names_triple_duplicate_increments():
    """Tre 'id' -> 'id', 'id_2', 'id_3'."""
    cols = ["id", "id", "id"]
    new_cols, renames = _dedup_column_names(cols)
    assert new_cols == ["id", "id_2", "id_3"]
    assert renames == [("id", "id_2"), ("id", "id_3")]


def test_dedup_column_names_collision_with_existing_suffix_name():
    """Edge case: 'id', 'id_2', 'id' -> 'id', 'id_2', 'id_3'.
    L'algoritmo deve saltare il suffisso _2 (gia' presente come nome
    distinto) e usare _3, evitando di reintrodurre un duplicato."""
    cols = ["id", "id_2", "id"]
    new_cols, renames = _dedup_column_names(cols)
    assert new_cols == ["id", "id_2", "id_3"]
    assert renames == [("id", "id_3")]


def test_dedup_column_names_multiple_duplicate_groups_independent():
    """Piu' gruppi di duplicati diversi: contatori indipendenti."""
    cols = ["a", "b", "a", "b", "c"]
    new_cols, renames = _dedup_column_names(cols)
    assert new_cols == ["a", "b", "a_2", "b_2", "c"]
    assert renames == [("a", "a_2"), ("b", "b_2")]


# _dedup_dataframe_columns_for_serialization: integrazione con DataFrame


def test_dedup_dataframe_no_duplicates_returns_same_frame_no_warnings():
    """Senza duplicati: ritorna lo stesso df, warnings list vuota.
    Ottimizzazione: nessuna copia inutile."""
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    out_df, warnings = _dedup_dataframe_columns_for_serialization(df)
    assert out_df is df
    assert warnings == []


def test_dedup_dataframe_with_duplicates_preserves_all_columns():
    """Caso reale (BUG.md #6): JOIN SQL senza alias produce due colonne 'id'.
    Pre-fix: df.to_dict scartava la seconda silenziosamente.
    Post-fix: tutte le 3 colonne preservate, warning visibile."""
    # Costruisco DataFrame con 2 colonne 'id' (impossibile con dict literal,
    # serve il costruttore via columns=)
    df = pd.DataFrame([[1, "alice", 99], [2, "bob", 100]], columns=["id", "nome", "id"])
    assert df.shape == (2, 3)
    assert list(df.columns) == ["id", "nome", "id"]

    out_df, warnings = _dedup_dataframe_columns_for_serialization(df)

    # Tutte le colonne preservate, dedupate
    assert out_df.shape == (2, 3)
    assert list(out_df.columns) == ["id", "nome", "id_2"]

    # NOTE: visibili nella response
    assert len(warnings) == 1
    assert "id" in warnings[0]
    assert "id_2" in warnings[0]

    # Confronto contenuto colonna per colonna: i valori della 3a colonna
    # originale finiscono in 'id_2', NON sono persi.
    assert list(out_df["id"]) == [1, 2]
    assert list(out_df["id_2"]) == [99, 100]
    assert list(out_df["nome"]) == ["alice", "bob"]

    # Sanity: il df dedupato puo' essere serializzato senza warning pandas
    # (pre-fix emetteva UserWarning + scartava colonne).
    rows = out_df.to_dict(orient="records")
    assert rows[0] == {"id": 1, "nome": "alice", "id_2": 99}
    assert rows[1] == {"id": 2, "nome": "bob", "id_2": 100}


def test_dedup_dataframe_does_not_mutate_input():
    """Idempotenza/safety: l'input df non deve essere modificato."""
    df = pd.DataFrame([[1, 99]], columns=["id", "id"])
    original_columns = list(df.columns)

    out_df, _ = _dedup_dataframe_columns_for_serialization(df)

    # Input intatto
    assert list(df.columns) == original_columns
    # Output e' un oggetto distinto
    assert out_df is not df
    assert list(out_df.columns) == ["id", "id_2"]


# ResourceMetadata pilot: preview DB DataFrame + metadata sidecar


def test_build_preview_resource_pairs_dataframe_with_resource_metadata():
    df = pd.DataFrame({"id": [1, 2], "shape": ["POINT(0 0)", "POINT(1 1)"]})
    columns_info = [
        {"name": "id", "type": "int"},
        {"name": "shape", "type": "geometry:point:4326"},
    ]

    resource = _build_preview_resource(
        df,
        columns_info,
        source_kind="db",
        row_count=2,
        project_id="proj-1",
        group_id="grp-1",
    )

    assert isinstance(resource, EstraiPreviewResource)
    assert resource.dataframe is df
    assert isinstance(resource.metadata, ResourceMetadata)
    assert resource.metadata.source_kind == "db"
    assert resource.metadata.row_count == 2
    assert resource.metadata.project_id == "proj-1"
    assert resource.metadata.group_id == "grp-1"
    assert resource.metadata.geometry_column == "shape"
    assert resource.metadata.crs_epsg == 4326
    assert resource.metadata.to_columns_list() == columns_info


def test_build_db_preview_resource_uses_cursor_columns_before_dataframe_names():
    df = pd.DataFrame({"wrong_id": [1], "wrong_amount": [12.5]})

    resource = _build_db_preview_resource_from_cursor_schema(
        df,
        ["ID", "amount"],
        {"id": "int"},
        row_count=1,
        project_id="proj-1",
    )

    assert resource.dataframe is df
    assert resource.metadata.to_columns_list() == [
        {"name": "ID", "type": "int"},
        {"name": "amount", "type": "real"},
    ]


def test_build_db_preview_resource_dedups_cursor_columns_positionally():
    df = pd.DataFrame([[1, 99]], columns=["id", "id_2"])

    resource = _build_db_preview_resource_from_cursor_schema(
        df,
        ["id", "id"],
        {"id": "int"},
        row_count=1,
        project_id="proj-1",
    )

    assert resource.metadata.to_columns_list() == [
        {"name": "id", "type": "int"},
        {"name": "id_2", "type": "int"},
    ]


def test_preview_database_keeps_public_response_shape_without_metadata_key():
    query_generator = MagicMock()
    query_generator.generate_sql.return_value = {
        "sql": "SELECT id, name FROM people",
        "params": {},
    }
    query_executor = MagicMock()
    query_executor.execute_raw.return_value = ([(1, "alice")], ["id", "name"])

    executor = EstraiExecutor(
        project_service=MagicMock(),
        executor=query_executor,
        generator=query_generator,
        connection_service=MagicMock(),
    )
    canvas = MagicMock()
    canvas.nodes = []
    canvas.edges = []
    result_node = MagicMock()
    result_node.id = "result-1"
    connection = MagicMock()
    connection.type = "postgres"
    connection.db_schema = "public"

    response = executor._preview_database(
        "proj-1",
        canvas,
        result_node,
        connection,
        limit=10,
    )

    assert response == {
        "success": True,
        "columns": [
            {"name": "id", "type": "int"},
            {"name": "name", "type": "str"},
        ],
        "rows": [{"id": 1, "name": "alice"}],
        "total_count": 1,
        "preview_count": 1,
        "truncated": False,
        "query": "SELECT id, name FROM people",
    }
