"""Cluster 4 contract test: engine="pyarrow" esplicito + atomic write.

Bug originali (audit P0 cluster):
- data-layer P0 #1 (`tabular.py:157`) — `tabular_dataframe_to_bytes` chiama
  `df.to_parquet(buf, index=False)` senza `engine=` esplicito. Su install
  con fastparquet preferito, Int64 nullable degrada a float64 nel round-trip.
- data-layer P0 #2 (`frame_io.py:708`) — `save_tabular_dataframe` chiama
  `df.to_parquet(final_path, index=False)` con due problemi: engine
  implicito (stesso bug) + scrittura non-atomica (un reader concorrente
  puo' vedere file parziale).

Post-fix:
- tabular.py: `engine="pyarrow"` esplicito.
- frame_io.py: `engine="pyarrow"` + write su `.tmp` + `rename` atomico +
  cleanup del .tmp su errore.

Allinea il write path al read path (gia' fixato in D1 con
`dtype_backend="numpy_nullable"`). Pattern Go-friendly: helper modulo,
no classi, mappable a `parquetWriteAtomic(path, df) error`.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _nullable_int_df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": pd.array([1, 2, None, 4], dtype="Int64"),
        "value": pd.array([10, None, 30, 40], dtype="Int64"),
        "label": ["a", "b", "c", "d"],
    })


# Path 1: tabular.tabular_dataframe_to_bytes

def test_tabular_dataframe_to_bytes_preserves_int64_nullable():
    """Pre-fix: engine non specificato → su install con fastparquet preferito,
    Int64 con null diventa float64 silenziosamente nel round-trip.
    Post-fix: engine="pyarrow" preserva Int64Dtype + pd.NA."""
    from app.core.dataframes.tabular import tabular_dataframe_to_bytes

    df_in = _nullable_int_df()
    blob = tabular_dataframe_to_bytes(df_in, "parquet")

    df_out = pd.read_parquet(io.BytesIO(blob), dtype_backend="numpy_nullable")

    assert df_out["id"].dtype == pd.Int64Dtype(), (
        f"Int64 nullable perso nel round-trip tabular_dataframe_to_bytes: "
        f"dtype={df_out['id'].dtype}. Causa: engine non pyarrow."
    )
    assert pd.isna(df_out["id"].iloc[2]), (
        "pd.NA deve essere preservato, non degradato a float NaN"
    )
    assert not pd.isna(df_out["id"].iloc[0])
    assert int(df_out["id"].iloc[0]) == 1


def test_tabular_to_bytes_passes_pyarrow_engine_explicit(monkeypatch):
    """Spy su to_parquet: verifica che engine='pyarrow' sia passato
    esplicito (non implicito 'auto')."""
    from app.core.dataframes.tabular import tabular_dataframe_to_bytes

    calls_kwargs: list[dict] = []
    original = pd.DataFrame.to_parquet

    def _spy(self, path_or_buf, **kwargs):
        calls_kwargs.append(dict(kwargs))
        return original(self, path_or_buf, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _spy)
    tabular_dataframe_to_bytes(_nullable_int_df(), "parquet")

    assert calls_kwargs, "to_parquet non e' stato chiamato"
    assert calls_kwargs[0].get("engine") == "pyarrow", (
        f"engine deve essere 'pyarrow' esplicito, trovato: "
        f"{calls_kwargs[0].get('engine')!r}"
    )


# Path 2: post-Phase5 — `save_tabular_dataframe` (path-based) rimossa.
# Il fix engine=pyarrow + atomic write copriva un flow path-based che non
# esiste piu': il dispatcher e' `tabular_dataframe_to_bytes` (Path 1) che
# usa engine=pyarrow su buffer in-memory — anti-regression preserved.


def test_save_tabular_dataframe_tombstone_post_phase5():
    """`save_tabular_dataframe` rimossa: nessun caller live in app/
    materializa parquet path-based. Il bug del round-trip Int64
    nullable e' coperto dal Path 1 (bytes-first) sopra."""
    from app.core.dataframes import frame_io

    assert not hasattr(frame_io, "save_tabular_dataframe")


# Path 3: architectural lint

def test_all_to_parquet_callsites_use_pyarrow_engine():
    """Architectural lint: tutti i `df.to_parquet(...)` in tabular.py
    e frame_io.py devono passare engine='pyarrow'. Anti-regression contro
    futuri commit che re-introducano l'engine implicito."""
    import inspect
    from app.core.dataframes import tabular, frame_io

    for mod, label in [(tabular, "tabular"), (frame_io, "frame_io")]:
        source = inspect.getsource(mod)
        # Trova tutte le linee con `.to_parquet(`
        for line_no, line in enumerate(source.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if ".to_parquet(" in line:
                # La linea o la successiva deve contenere engine="pyarrow"
                # (il call puo' essere multi-linea con kwargs separati)
                window = "\n".join(
                    source.splitlines()[max(0, line_no - 1):line_no + 4]
                )
                assert 'engine="pyarrow"' in window or "engine='pyarrow'" in window, (
                    f"{label}.py linea {line_no}: `.to_parquet(...)` senza "
                    f"engine='pyarrow' esplicito. Pre-fix: round-trip Int64 "
                    f"nullable poteva degradare a float64 su install "
                    f"fastparquet. Codice: {line.strip()[:100]}"
                )
