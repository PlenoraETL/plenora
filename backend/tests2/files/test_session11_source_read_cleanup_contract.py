"""Sessione 11 P1: source_read cleanup order — tombstone post-bytes-first.

Storico:
- files/source_read.py:96-104 (vecchio path-mode) — gc.collect chiamato PRIMA
  di `_cleanup_temp_path`. Su worker memory-constrained con GeoDataFrame
  grandi (shp/gdb) il gc tentava di liberare memoria del GeoDataFrame
  PRIMA del cleanup FS → picco memoria spurious.
- Post-fix Sessione 11: cleanup FS PRIMA, gc.collect DOPO.
- Post-Phase4 (bytes-first): `load_dataframe` consuma il blob direttamente
  in RAM via `load_by_format_bytes` / `list_vector_layers`, senza piu'
  materializzare temp file. `_cleanup_temp_path` non esiste piu' nel
  codice, quindi il bug originale e' impossibile by-construction.

Questo test resta come tombstone: garantisce che non si torni MAI piu'
a un pattern path-mode con temp file in `load_dataframe`.
"""

from __future__ import annotations

import inspect

import pytest

from tests2.shared._ast_identifiers import all_identifiers


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_source_read_load_dataframe_is_bytes_first_no_tempfile():
    """`load_dataframe` post-Phase4: deve consumare il blob in RAM,
    niente piu' `_cleanup_temp_path`, niente piu' `temp_file_for`
    nel finally block."""
    from app.core.files import source_read

    src = inspect.getsource(source_read.load_dataframe)

    assert "_cleanup_temp_path" not in all_identifiers(src), (
        "load_dataframe non deve piu' usare _cleanup_temp_path post-Phase4 "
        "(bytes-first via load_by_format_bytes). Se torna, e' una regressione "
        "verso il path-mode che il pattern Sessione 11 doveva proteggere."
    )
    assert "temp_file_for" not in all_identifiers(src), (
        "load_dataframe non deve materializzare tempfile post-Phase4 "
        "(bytes-first). Se torna, contraddice il refactor che ha "
        "eliminato il bounce su disco."
    )
    # Conferma che il path nuovo bytes-first sia in uso.
    assert "load_by_format_bytes" in all_identifiers(src), (
        "load_dataframe deve consumare il blob via load_by_format_bytes "
        "(bytes-first contract). Se la helper cambia nome, aggiornare qui."
    )
