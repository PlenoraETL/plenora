"""
Contract test per i fix puntuali su transforms/base.py.

Dalla sessione di audit 2026-04-17:
"""

from __future__ import annotations

import pytest
import pandas as pd
import hashlib


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# 1. op_filter: silent failure -> raise


def test_filter_raises_on_missing_column():
    """Filtro su colonna non esistente deve sollevare ValueError
    esplicito invece di ritornare il df invariato in silenzio."""
    from app.core.manipola.transforms.base import op_filter

    df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
    with pytest.raises(ValueError) as exc:
        op_filter(df, column="colonna_inesistente", operator="==", value=42)
    assert "colonna_inesistente" in str(exc.value).lower() or \
           "non trovata" in str(exc.value).lower() or \
           "not found" in str(exc.value).lower()


def test_filter_works_on_existing_column():
    """Regression: il filtro normale deve continuare a funzionare."""
    from app.core.manipola.transforms.base import op_filter
    df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
    result = op_filter(df, column="a", operator=">", value=1)
    assert len(result) == 2
    assert list(result["a"]) == [2, 3]


# 2. op_concat_columns skip_null vectorized


def test_concat_columns_skip_null_basic():
    from app.core.manipola.transforms.base import op_concat_columns
    df = pd.DataFrame({
        "cognome": ["Rossi", "Verdi", "Bianchi"],
        "nome": ["Mario", "Luigi", "Anna"],
    })
    result = op_concat_columns(df, columns=["cognome", "nome"], separator=" ", skip_null=True)
    assert list(result["concatenated"]) == ["Rossi Mario", "Verdi Luigi", "Bianchi Anna"]


def test_concat_columns_skip_null_with_nan():
    """skip_null=True: NaN devono essere skippati, non inclusi come 'nan'."""
    from app.core.manipola.transforms.base import op_concat_columns
    df = pd.DataFrame({
        "a": ["x", None, "z"],
        "b": ["1", "2", None],
    })
    result = op_concat_columns(df, columns=["a", "b"], separator="-", skip_null=True)
    assert list(result["concatenated"]) == ["x-1", "2", "z"]


def test_concat_columns_skip_null_all_nan_returns_none():
    """Se tutte le colonne sono NaN per una riga, output e' None."""
    from app.core.manipola.transforms.base import op_concat_columns
    df = pd.DataFrame({"a": [None, "x"], "b": [None, "y"]})
    result = op_concat_columns(df, columns=["a", "b"], separator="-", skip_null=True)
    assert result["concatenated"].iloc[0] is None or pd.isna(result["concatenated"].iloc[0])
    assert result["concatenated"].iloc[1] == "x-y"


def test_concat_columns_no_skip_null_treats_as_empty():
    """skip_null=False: NaN trattato come stringa vuota (comportamento esistente)."""
    from app.core.manipola.transforms.base import op_concat_columns
    df = pd.DataFrame({"a": ["x", None], "b": ["y", "z"]})
    result = op_concat_columns(df, columns=["a", "b"], separator="-", skip_null=False)
    # "x-y" e "-z" (NaN -> "")
    assert list(result["concatenated"]) == ["x-y", "-z"]


# 3. op_md5_hash vectorized


def _expected_md5(values, separator="\x1f", normalize=True):
    """Calcola MD5 atteso con la stessa logica dell'implementazione."""
    parts = []
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            parts.append("")
        elif hasattr(v, 'wkt'):
            parts.append(v.wkt)
        else:
            s = str(v)
            if normalize:
                s = s.strip().lower()
            parts.append(s)
    return hashlib.md5(separator.join(parts).encode('utf-8')).hexdigest()


def test_md5_hash_deterministic():
    """Stesso input -> stesso hash."""
    from app.core.manipola.transforms.base import op_md5_hash
    df = pd.DataFrame({"a": ["hello", "world"], "b": [1, 2]})
    r1 = op_md5_hash(df, columns=["a", "b"])
    r2 = op_md5_hash(df, columns=["a", "b"])
    assert list(r1["md5_hash"]) == list(r2["md5_hash"])


def test_md5_hash_value_matches_expected():
    """L'hash generato deve matchare calcolo manuale."""
    from app.core.manipola.transforms.base import op_md5_hash
    df = pd.DataFrame({"a": ["Hello"], "b": [42]})
    result = op_md5_hash(df, columns=["a", "b"], normalize=True)
    # Nota: sorted alfabeticamente => ["a", "b"]
    expected = _expected_md5(["Hello", 42], normalize=True)
    assert result["md5_hash"].iloc[0] == expected


def test_md5_hash_handles_nan():
    """NaN -> stringa vuota nell'hash."""
    from app.core.manipola.transforms.base import op_md5_hash
    df = pd.DataFrame({"a": ["x", None], "b": ["y", "z"]})
    result = op_md5_hash(df, columns=["a", "b"])
    expected_row0 = _expected_md5(["x", "y"])
    expected_row1 = _expected_md5([None, "z"])
    assert result["md5_hash"].iloc[0] == expected_row0
    assert result["md5_hash"].iloc[1] == expected_row1


def test_md5_hash_columns_sorted_alphabetically():
    """Le colonne sono sempre ordinate prima dell'hash — determinismo
    indipendente dall'ordine in input."""
    from app.core.manipola.transforms.base import op_md5_hash
    df = pd.DataFrame({"z": ["x"], "a": ["y"]})
    r1 = op_md5_hash(df, columns=["z", "a"])
    r2 = op_md5_hash(df, columns=["a", "z"])
    assert r1["md5_hash"].iloc[0] == r2["md5_hash"].iloc[0]


# 4. op_table_diff correctness (no iterrows)


def test_table_diff_identifies_added_deleted_modified():
    from app.core.manipola.transforms.base import op_table_diff
    old_df = pd.DataFrame({
        "id": [1, 2, 3],
        "nome": ["Anna", "Beto", "Carlo"],
        "eta": [30, 40, 50],
    })
    new_df = pd.DataFrame({
        "id": [2, 3, 4],
        "nome": ["Beto", "Carlos", "Dario"],   # Carlos e' MODIFIED (era Carlo)
        "eta": [40, 50, 60],
    })
    result = op_table_diff(
        inputs=[old_df, new_df],
        left_keys=["id"],
        right_keys=["id"],
        include_unchanged="no",
    )
    statuses = result["_diff_status"].value_counts().to_dict()
    assert statuses.get("DELETED", 0) == 1  # id=1 (Anna)
    assert statuses.get("ADDED", 0) == 1    # id=4 (Dario)
    assert statuses.get("MODIFIED", 0) == 1 # id=3 (Carlo -> Carlos)


def test_table_diff_unchanged_excluded_by_default():
    """include_unchanged='no': UNCHANGED non nel risultato."""
    from app.core.manipola.transforms.base import op_table_diff
    old_df = pd.DataFrame({"id": [1], "v": ["x"]})
    new_df = pd.DataFrame({"id": [1], "v": ["x"]})  # identico
    result = op_table_diff(
        inputs=[old_df, new_df],
        left_keys=["id"], right_keys=["id"],
        include_unchanged="no",
    )
    assert "UNCHANGED" not in result["_diff_status"].values
    assert len(result) == 0


def test_table_diff_modified_tracks_changed_columns():
    """Su MODIFIED, _diff_columns contiene le colonne cambiate."""
    from app.core.manipola.transforms.base import op_table_diff
    old_df = pd.DataFrame({"id": [1], "a": ["x"], "b": ["y"]})
    new_df = pd.DataFrame({"id": [1], "a": ["x"], "b": ["z"]})
    result = op_table_diff(
        inputs=[old_df, new_df],
        left_keys=["id"], right_keys=["id"],
        include_unchanged="yes",
    )
    modified = result[result["_diff_status"] == "MODIFIED"]
    assert len(modified) == 1
    assert "b" in (modified["_diff_columns"].iloc[0] or "")
    assert "a" not in (modified["_diff_columns"].iloc[0] or "")


def test_table_diff_numeric_tolerance():
    """Differenze numeriche < 1e-9 non sono considerate MODIFIED."""
    from app.core.manipola.transforms.base import op_table_diff
    old_df = pd.DataFrame({"id": [1], "v": [1.0]})
    new_df = pd.DataFrame({"id": [1], "v": [1.0 + 1e-15]})   # tolleranza OK
    result = op_table_diff(
        inputs=[old_df, new_df],
        left_keys=["id"], right_keys=["id"],
        include_unchanged="yes",
    )
    assert result["_diff_status"].iloc[0] == "UNCHANGED"


def test_table_diff_nan_nan_are_equal():
    """NaN == NaN -> UNCHANGED (non MODIFIED)."""
    from app.core.manipola.transforms.base import op_table_diff
    old_df = pd.DataFrame({"id": [1], "v": [None]})
    new_df = pd.DataFrame({"id": [1], "v": [None]})
    result = op_table_diff(
        inputs=[old_df, new_df],
        left_keys=["id"], right_keys=["id"],
        include_unchanged="yes",
    )
    assert result["_diff_status"].iloc[0] == "UNCHANGED"
