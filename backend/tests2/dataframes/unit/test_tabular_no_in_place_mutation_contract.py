"""
Contract test per l'invariante "no in-place mutation" nel data layer.

Cluster bug coperti (plenora-bug-data-layer P0):

  1. `sanitize_for_json` (`tabular.py:264-266`)
     Pre-fix: il loop `row[chiave] = _sanitizza_valore(row[chiave])` mutava
     in-place ogni dict di input. I caller che riusavano `rows` dopo la
     chiamata vedevano stato corrotto (numpy/Timestamp gia' convertiti in
     str). Post-fix: list-comprehension di nuovi dict.

  2. `stringify_columns_preserving_nulls` (`tabular.py:656-665`)
     Pre-fix: `df[col] = df[col].astype(str)` mutava il DataFrame del
     caller. Post-fix: `df = df.copy()` come prima riga.

  3. `geometry_series_columns_to_wkt` (`tabular.py:668-677`)
     Pre-fix: stesso pattern di mutazione in-place. Post-fix: `df.copy()`.

Test paradigma Go-friendly: funzioni pure (no fixture stato), assertions
deterministiche, scenari table-driven dove sensato.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.dataframes.tabular import (
    geometry_series_columns_to_wkt,
    sanitize_for_json,
    stringify_columns_preserving_nulls,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
]


class TestSanitizeForJsonNoMutation:
    """`sanitize_for_json` NON deve mutare ne' la lista esterna ne' i
    dict interni del caller.
    """

    def test_does_not_mutate_input_rows_list_identity(self):
        rows = [{"x": 1}, {"x": 2}]
        result = sanitize_for_json(rows)
        # Il risultato deve essere una NUOVA lista
        assert result is not rows

    def test_does_not_mutate_inner_dicts_identity(self):
        original_dict = {"x": 1, "y": "a"}
        rows = [original_dict]
        result = sanitize_for_json(rows)
        # Il dict ritornato deve essere un NUOVO oggetto
        assert result[0] is not original_dict

    def test_input_unchanged_after_call(self):
        # Caso reale: dict con valori che la sanitize TRASFORMEREBBE
        # se mutasse in-place (numpy int -> python int).
        import numpy as np
        rows = [{"count": np.int64(5), "name": "alice"}]
        snapshot = [dict(row) for row in rows]
        sanitize_for_json(rows)
        # Pre-fix: rows[0]["count"] sarebbe stato int Python (mutato).
        # Post-fix: resta np.int64.
        assert isinstance(rows[0]["count"], np.int64)
        assert rows == snapshot

    def test_returns_sanitized_values(self):
        """Functional check: il fix non rompe il comportamento atteso."""
        import numpy as np
        rows = [{"count": np.int64(5)}]
        result = sanitize_for_json(rows)
        assert isinstance(result[0]["count"], int)
        assert result[0]["count"] == 5


class TestStringifyColumnsNoMutation:
    """`stringify_columns_preserving_nulls` NON deve mutare il DataFrame
    del caller.
    """

    def test_does_not_mutate_input_dataframe(self):
        df = pd.DataFrame({"a": [1, 2, None], "b": [10.0, 20.0, 30.0]})
        snapshot_a = df["a"].copy()
        result = stringify_columns_preserving_nulls(df, ["a"])
        # Il caller mantiene il df originale int/float, NON stringificato.
        pd.testing.assert_series_equal(df["a"], snapshot_a)
        # Il risultato e' un NUOVO oggetto.
        assert result is not df

    def test_returned_df_has_stringified_column(self):
        # Pandas promuove la colonna a float64 quando contiene None +
        # interi; `astype(str)` produce "1.0" / "2.0". Il contract della
        # funzione preserva i null ma NON normalizza la rappresentazione.
        df = pd.DataFrame({"a": [1, 2, None]})
        result = stringify_columns_preserving_nulls(df, ["a"])
        assert result["a"].iloc[0] == "1.0"
        assert result["a"].iloc[1] == "2.0"
        assert result["a"].iloc[2] is None


class TestGeometryColumnsToWktNoMutation:
    """`geometry_series_columns_to_wkt` NON deve mutare il DataFrame del
    caller.
    """

    def test_does_not_mutate_input_dataframe_when_no_geom(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        snapshot = df.copy()
        result = geometry_series_columns_to_wkt(df)
        # Identita': il caller ha ancora il df originale.
        pd.testing.assert_frame_equal(df, snapshot)
        # Il risultato e' un nuovo oggetto anche se nessuna colonna era
        # geometry (forte garanzia di immutabilita').
        assert result is not df
