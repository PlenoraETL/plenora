"""
tests/manipola/unit/test_transforms_base3.py
=============================================
Test aggiuntivi per coprire i branch mancanti in transforms/base.py:
  - op_join: auto-conversione tipi chiavi (object vs numeric, numeric vs numeric)
  - op_join: dedup colonne duplicate, chiave duplicata nel DataFrame
  - op_filter: eccezione nel filtro (colonna con tipo incompatibile)
  - op_geo_from_coords: mode=wkt, mode non supportato, lon mancante
  - op_formula: formula con metodi stringa (.str.upper()), costante, potenza
  - op_aggregate: concat con separator/distinct/skip_null, first/last
  - op_conditional: between, isnull/notnull, operatore non valido skippato
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.base import (
    op_join,
    op_filter,
    op_formula,
    op_aggregate,
    op_conditional,
    op_geo_from_coords,
    op_sample,
    op_melt,
    op_add_row_number,
    op_date_extract,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
    op_string_extract,
    op_cross_join,
    op_transpose,
    op_reorder_columns,
    op_string_pad,
    op_string_length,
    op_dedup_advanced,
    op_window_function,
    op_uuid_generator,
)




# =============================================================================
# op_dedup_advanced — deduplicazione avanzata
# =============================================================================

class TestOpDedupAdvanced:

    def test_dedup_with_order(self):
        """Deduplicazione con ordinamento — mantiene il valore più alto."""
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "valore": [100, 200, 300],
        })
        result = op_dedup_advanced(df, subset=["comune"],
                                   order_column="valore", ascending=False, keep="first")
        roma = result[result["comune"] == "Roma"]
        assert len(roma) == 1
        assert roma["valore"].iloc[0] == 200

    def test_keep_last(self):
        """keep='last' mantiene l'ultima riga tra i duplicati."""
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "valore": [100, 200, 300],
        })
        result = op_dedup_advanced(df, subset=["comune"], keep="last")
        roma = result[result["comune"] == "Roma"]
        assert len(roma) == 1
        assert roma["valore"].iloc[0] == 200

    def test_subset_columns(self):
        """Deduplicazione su sotto-insieme di colonne."""
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "zona": ["A", "B", "A"],
            "valore": [100, 200, 300],
        })
        result = op_dedup_advanced(df, subset=["comune"])
        assert len(result) == 2


# =============================================================================
# op_window_function — funzioni finestra
# =============================================================================

class TestOpWindowFunction:

    def test_rank(self):
        """function='rank' → colonna ranking."""
        df = _immobili()
        result = op_window_function(df, column="valore_omi", function="rank")
        out_col = "valore_omi_rank"
        assert out_col in result.columns
        assert len(result) == len(df)

    def test_cumsum(self):
        """function='cumsum' → somma cumulativa."""
        df = pd.DataFrame({"v": [10, 20, 30]})
        result = op_window_function(df, column="v", function="cumsum")
        out_col = "v_cumsum"
        assert out_col in result.columns
        assert result[out_col].tolist() == [10, 30, 60]

    def test_lag(self):
        """function='lag' → valore precedente."""
        df = pd.DataFrame({"v": [10, 20, 30]})
        result = op_window_function(df, column="v", function="lag")
        out_col = "v_lag"
        assert out_col in result.columns
        assert pd.isna(result[out_col].iloc[0])
        assert result[out_col].iloc[1] == 10

    def test_grouped(self):
        """group_by → ranking separato per ogni gruppo."""
        df = pd.DataFrame({
            "gruppo": ["A", "A", "A", "B", "B", "B"],
            "valore": [30, 10, 20, 60, 40, 50],
        })
        result = op_window_function(df, column="valore", function="rank",
                                    group_by="gruppo")
        out_col = "valore_rank"
        assert out_col in result.columns
        assert len(result) == 6


# =============================================================================
# op_uuid_generator — generazione UUID
# =============================================================================

class TestOpUuidGenerator:

    def test_uuid_base(self):
        """Colonna uuid presente, tutti valori unici."""
        df = _immobili(5)
        result = op_uuid_generator(df)
        assert "uuid" in result.columns
        assert result["uuid"].nunique() == 5

    def test_output_column_custom(self):
        """Nome colonna personalizzato."""
        df = _immobili(3)
        result = op_uuid_generator(df, output_column="id_univoco")
        assert "id_univoco" in result.columns
        assert result["id_univoco"].nunique() == 3
