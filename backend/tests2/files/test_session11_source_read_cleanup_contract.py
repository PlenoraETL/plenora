"""Sessione 11 P1: source_read cleanup order - stream/spool tombstone.

Storico: il vecchio path-mode chiamava ``gc.collect`` prima del cleanup del
file temporaneo. Il path corrente usa ``open_blob`` come context manager: lo
spool anonimo viene chiuso all'uscita dal ``with`` e solo dopo viene chiamato
``gc.collect``. Non esistono path da pulire manualmente.
"""

from __future__ import annotations

import inspect

import pytest

from tests2.shared._ast_identifiers import all_identifiers


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_source_read_load_dataframe_is_stream_first_with_scoped_spool():
    """Il parser usa lo stream scoped, senza path/cleanup manuali."""
    from app.core.files import source_read

    src = inspect.getsource(source_read.load_dataframe)
    identifiers = all_identifiers(src)

    assert "_cleanup_temp_path" not in identifiers
    assert "temp_file_for" not in identifiers
    assert "open_blob" in identifiers
    assert "load_by_format_stream" in identifiers, (
        "load_dataframe deve passare il file-like seekable direttamente al "
        "parser, senza ricostruire un bytes completo."
    )
    assert src.index("with open_blob") < src.index("gc.collect()")
