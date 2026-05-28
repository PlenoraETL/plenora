"""
Contract test per `dtype_backend="numpy_nullable"` sui 3 call site di
`pd.read_parquet` in `app/core/dataframes/frame_io.py` (D1 fix).

Cluster bug coperto (audit D1):

  Pre-fix: `pd.read_parquet(...)` senza `dtype_backend` instradava
  ogni colonna integer-con-null su NumPy `float64` (con `NaN` al
  posto del null) — round-trip non lossless per tipi integer. La
  consequenza visibile a valle: una colonna `Int64` salvata su
  parquet veniva riletta come `float64`, e i null integer
  perdevano la distinzione "valore mancante" diventando un sentinel
  floating-point.

  Post-fix: `dtype_backend="numpy_nullable"` instrada le colonne
  integer-con-null su `pd.Int64Dtype()` e preserva i null come
  `pd.NA`. Il round-trip e' lossless: il dtype rimane Int64 e
  i null restano distinti.

Sicurezza consumer (verificato in audit D1):
  - `sanitize_for_json` rileva `pd.NA` via `pd.isna(value)` prima
    dei branch `isinstance np.integer/np.floating/np.bool_`.
  - `to_dict("records")` su Int64/Float64 ritorna gia' scalari
    Python nativi (int/float) per i valori non-null.

Scope ristretto: i test verificano l'invariante di dtype/null
preservation, non testano l'intera pipeline a valle.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from app.core.dataframes.frame_io import (
    _load_dataframe_from_bytes,
    load_dataframe_from_artifact,
    load_dataframe_from_file,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _build_nullable_integer_parquet(tmp_dir: Path) -> Path:
    """Materialize a parquet with an Int64 nullable column and a null.

    Path-only helper: ritorna il Path su disco. Caller responsabile
    di rimuovere il tmp_dir (gestito via pytest tmp_path fixture).
    """
    df = pd.DataFrame(
        {
            "id": pd.array([1, 2, None, 4], dtype="Int64"),
            "amount": pd.array([10.5, None, 30.0, 40.25], dtype="Float64"),
            "label": ["alpha", "beta", "gamma", "delta"],
        }
    )
    out_path = tmp_dir / "nullable.parquet"
    df.to_parquet(out_path)
    return out_path


# ----------------------------------------------------------------------
# Call site 1: `load_dataframe_from_file` (estensione .parquet)
# ----------------------------------------------------------------------


def test_load_from_file_preserves_int64_nullable_dtype(tmp_path):
    """Una colonna `Int64` salvata su parquet sopravvive al round-trip
    come `Int64`, non degrada a `float64`."""
    parquet_path = _build_nullable_integer_parquet(tmp_path)

    df = load_dataframe_from_file(parquet_path)

    assert str(df["id"].dtype) == "Int64", (
        f"Expected Int64 dtype, got {df['id'].dtype!r}. "
        f"Senza dtype_backend='numpy_nullable' pandas converte "
        f"integer+null in float64, perdendo il tipo originale."
    )


def test_load_from_file_preserves_null_as_pd_na_not_nan(tmp_path):
    """I null integer restano `pd.NA` invece di diventare `NaN`
    (NaN e' il sentinel floating-point usato da NumPy classico)."""
    parquet_path = _build_nullable_integer_parquet(tmp_path)

    df = load_dataframe_from_file(parquet_path)

    null_cell = df.loc[2, "id"]
    assert null_cell is pd.NA, (
        f"Expected pd.NA, got {null_cell!r} (type={type(null_cell).__name__}). "
        f"NaN sarebbe il fallback float64 pre-fix."
    )
    assert pd.isna(null_cell), "pd.isna deve riconoscere comunque il null."


def test_load_from_file_preserves_float64_nullable_dtype(tmp_path):
    """Anche Float64 nullable sopravvive (nullable float pandas, non
    float64 NumPy)."""
    parquet_path = _build_nullable_integer_parquet(tmp_path)

    df = load_dataframe_from_file(parquet_path)

    assert str(df["amount"].dtype) == "Float64", (
        f"Expected Float64 nullable dtype, got {df['amount'].dtype!r}"
    )


# ----------------------------------------------------------------------
# Call site 2: `load_dataframe_from_artifact` (fmt='parquet', path-based)
# ----------------------------------------------------------------------


def test_load_from_artifact_path_preserves_int64_nullable(tmp_path):
    """Path artifact via fmt='parquet' deve avere stesso contratto."""
    parquet_path = _build_nullable_integer_parquet(tmp_path)

    df = load_dataframe_from_artifact(path=parquet_path, format_hint="parquet")

    assert str(df["id"].dtype) == "Int64"
    assert df.loc[2, "id"] is pd.NA


# ----------------------------------------------------------------------
# Call site 3: `_load_dataframe_from_bytes` (parquet blob in-memory)
# ----------------------------------------------------------------------


def test_load_from_bytes_preserves_int64_nullable():
    """Blob bytes parquet via `_load_dataframe_from_bytes` deve avere
    stesso contratto del path-based loader."""
    df_src = pd.DataFrame(
        {
            "id": pd.array([1, 2, None, 4], dtype="Int64"),
            "amount": pd.array([10.5, None, 30.0, 40.25], dtype="Float64"),
            "label": ["alpha", "beta", "gamma", "delta"],
        }
    )
    buf = io.BytesIO()
    df_src.to_parquet(buf)
    raw = buf.getvalue()

    df = _load_dataframe_from_bytes(raw, format_hint="parquet")

    assert str(df["id"].dtype) == "Int64"
    assert df.loc[2, "id"] is pd.NA
    assert str(df["amount"].dtype) == "Float64"


# ----------------------------------------------------------------------
# Consumer compatibility: sanitize_for_json downstream
# ----------------------------------------------------------------------


def test_loaded_parquet_sanitize_for_json_returns_none_for_nulls(tmp_path):
    """Round-trip + `sanitize_for_json` deve produrre `None` per i
    null integer (non `NaN`, non `pd.NA`)."""
    from app.core.dataframes.tabular import sanitize_for_json

    parquet_path = _build_nullable_integer_parquet(tmp_path)
    df = load_dataframe_from_file(parquet_path)

    records = df.to_dict(orient="records")
    sanitized = sanitize_for_json(records)

    # Riga 2: id=null. Il sanitize_for_json deve convertire pd.NA -> None.
    assert sanitized[2]["id"] is None, (
        f"Expected None for null integer, got {sanitized[2]['id']!r}"
    )
    # Riga 1: amount=null.
    assert sanitized[1]["amount"] is None
    # Valori non-null restano numerici nativi (int/float Python).
    assert sanitized[0]["id"] == 1
    assert isinstance(sanitized[0]["id"], int)
    assert sanitized[0]["amount"] == 10.5
    assert isinstance(sanitized[0]["amount"], float)
