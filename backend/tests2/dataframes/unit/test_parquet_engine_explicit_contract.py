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


# Path 2: frame_io.save_tabular_dataframe

def test_save_tabular_dataframe_passes_pyarrow_engine(tmp_path, monkeypatch):
    """save_tabular_dataframe deve passare engine='pyarrow' esplicito."""
    from app.core.dataframes.frame_io import save_tabular_dataframe

    calls_kwargs: list[dict] = []
    original = pd.DataFrame.to_parquet

    def _spy(self, path_or_buf, **kwargs):
        calls_kwargs.append(dict(kwargs))
        return original(self, path_or_buf, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _spy)

    save_tabular_dataframe(_nullable_int_df(), tmp_path / "output", "parquet")

    assert calls_kwargs, "to_parquet non e' stato chiamato"
    assert calls_kwargs[0].get("engine") == "pyarrow", (
        f"engine deve essere 'pyarrow' esplicito, trovato: "
        f"{calls_kwargs[0].get('engine')!r}"
    )


def test_save_tabular_dataframe_atomic_write_no_tmp_residual(tmp_path):
    """save_tabular_dataframe scrive .tmp + rename atomico. Il .tmp NON
    deve restare su disco dopo la write success.
    Pre-fix: scrittura diretta su final_path; un reader concorrente
    vedeva file parziale durante la write."""
    from app.core.dataframes.frame_io import save_tabular_dataframe

    final_path = tmp_path / "output.parquet"
    save_tabular_dataframe(_nullable_int_df(), tmp_path / "output", "parquet")

    tmp_residual = tmp_path / "output.parquet.tmp"
    assert not tmp_residual.exists(), (
        "File .tmp residuo trovato: il rename atomico non e' avvenuto "
        "o ha lasciato residui sul filesystem"
    )
    assert final_path.exists(), "Il file finale deve esistere dopo il rename"
    assert final_path.stat().st_size > 0, "Il file finale non deve essere vuoto"


def test_save_tabular_dataframe_cleanup_tmp_on_write_failure(tmp_path, monkeypatch):
    """Se to_parquet solleva, il .tmp NON deve restare orfano sul disco.
    Anti-regression sul cleanup del fallback try/except."""
    from app.core.dataframes.frame_io import save_tabular_dataframe

    def _raise(self, path_or_buf, **kwargs):
        # Tocco il file per simulare scrittura parziale, poi sollevo
        from pathlib import Path
        if isinstance(path_or_buf, (str, Path)):
            Path(path_or_buf).touch()
        raise RuntimeError("Simulated parquet write failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _raise)

    with pytest.raises(RuntimeError, match="Simulated"):
        save_tabular_dataframe(_nullable_int_df(), tmp_path / "output", "parquet")

    final_path = tmp_path / "output.parquet"
    tmp_residual = tmp_path / "output.parquet.tmp"
    assert not tmp_residual.exists(), (
        ".tmp orfano dopo write failure: cleanup nel except mancante"
    )
    assert not final_path.exists(), (
        "final_path non deve essere creato se la write e' fallita"
    )


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
