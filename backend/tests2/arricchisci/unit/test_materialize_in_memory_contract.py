"""Contract test FS-5 — materialize_node senza tempfile.

Prima di FS-5, `_resolve_input_path_for_file_processor` creava un
tempfile tramite `tempfile.NamedTemporaryFile(delete=False)` quando un
DataFrame upstream doveva essere passato a un file-processor REST.

Post FS-5: bytes in-memory + io.BytesIO nel multipart POST, zero file
su disco. Questo test blinda:

1. Il branch DataFrame ritorna (bytes, filename), owns=False
2. Il multipart builder accetta il tuple e produce files={name: (filename, BytesIO)}
3. Sentinel: chiamare il resolve NON crea file in /tmp/ con prefix urby_materialize_
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def _make_connector():
    from app.core.arricchisci.api_connector import ApiConnectionService
    c = ApiConnectionService()
    return c


# 1. Resolver input: DataFrame → bytes tuple, no tempfile


def test_resolve_dataframe_upstream_returns_bytes_tuple():
    """Branch 2: upstream e' DataFrame → ritorna (bytes, filename), owns=False."""
    connector = _make_connector()

    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    nodes = [
        {"id": "api-1", "type": "api_enrich"},
        {"id": "rest-1", "type": "rest_file"},
    ]
    edges = [{"source": "api-1", "target": "rest-1"}]
    node_results = {"api-1": df}

    result, owns = connector._resolve_input_path_for_file_processor(
        nodes, edges, "rest-1",
        node_results=node_results,
        materialize_format="xlsx",
    )

    # Shape: tuple (bytes, filename)
    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    data_bytes, filename = result
    assert isinstance(data_bytes, bytes)
    assert len(data_bytes) > 0
    assert filename == "input.xlsx"
    # Bytes non-owned: no cleanup necessario
    assert owns is False


@pytest.mark.parametrize("fmt,expected_filename", [
    ("csv", "input.csv"),
    ("xlsx", "input.xlsx"),
    ("json", "input.json"),
    ("parquet", "input.parquet"),
])
def test_resolve_respects_materialize_format(fmt, expected_filename):
    connector = _make_connector()
    df = pd.DataFrame({"a": [1]})
    nodes = [{"id": "api-1", "type": "api_enrich"}, {"id": "rest-1", "type": "rest_file"}]
    edges = [{"source": "api-1", "target": "rest-1"}]

    result, owns = connector._resolve_input_path_for_file_processor(
        nodes, edges, "rest-1",
        node_results={"api-1": df},
        materialize_format=fmt,
    )
    assert isinstance(result, tuple)
    _, filename = result
    assert filename == expected_filename


def test_resolve_unsupported_format_falls_back_to_xlsx():
    """Format non in-memory-safe (gpkg/shp, ecc.) → fallback a xlsx."""
    connector = _make_connector()
    df = pd.DataFrame({"a": [1]})
    nodes = [{"id": "api-1", "type": "api_enrich"}, {"id": "rest-1", "type": "rest_file"}]
    edges = [{"source": "api-1", "target": "rest-1"}]

    result, owns = connector._resolve_input_path_for_file_processor(
        nodes, edges, "rest-1",
        node_results={"api-1": df},
        materialize_format="gpkg",   # FS-only, non supportato in-memory
    )
    assert isinstance(result, tuple)
    _, filename = result
    assert filename == "input.xlsx"  # fallback


# 2. Sentinel: NO tempfile creato per branch DataFrame


def test_no_tempfile_created_for_dataframe_branch(monkeypatch):
    """Intercept tempfile.NamedTemporaryFile: se viene chiamato, il
    refactor FS-5 e' stato regressione."""
    import tempfile as tf_mod
    calls = []
    orig = tf_mod.NamedTemporaryFile

    def _spy(*args, **kwargs):
        # Non creare davvero il file: alziamo eccezione per catturare
        # tutti i callsite interessati.
        calls.append((args, kwargs))
        raise AssertionError(
            f"tempfile.NamedTemporaryFile chiamato dal path DataFrame! "
            f"args={args} kwargs={kwargs}"
        )

    monkeypatch.setattr(tf_mod, "NamedTemporaryFile", _spy)

    connector = _make_connector()
    df = pd.DataFrame({"a": list(range(100))})
    nodes = [{"id": "api-1", "type": "api_enrich"}, {"id": "rest-1", "type": "rest_file"}]
    edges = [{"source": "api-1", "target": "rest-1"}]

    # Deve completare senza chiamare tempfile
    result, owns = connector._resolve_input_path_for_file_processor(
        nodes, edges, "rest-1",
        node_results={"api-1": df},
        materialize_format="csv",
    )
    assert isinstance(result, tuple)
    assert len(calls) == 0


# 3. Multipart builder: tuple (bytes, filename) → BytesIO nel POST body


def test_multipart_builder_accepts_bytes_tuple():
    """_build_rest_file_multipart riceve tuple (bytes, filename) e
    produce files[name] = (filename, BytesIO, content_type) per requests."""
    connector = _make_connector()

    data_bytes = b"a,b\n1,x\n2,y\n"
    input_ref = (data_bytes, "input.csv")

    file_fields = [{"name": "file", "source": "node_input", "required": True}]
    opened_handles = []

    files, data, err = connector._build_rest_file_multipart(
        file_fields=file_fields,
        static_fields=[],
        input_path=input_ref,
        extra_files_cfg={},
        opened_handles=opened_handles,
    )

    assert err is None
    assert "file" in files
    filename, fh, content_type = files["file"]
    assert filename == "input.csv"
    # fh deve essere BytesIO (NOT un file handle normale)
    assert isinstance(fh, io.BytesIO)
    assert fh.getvalue() == data_bytes
    # opened_handles NON deve contenere il BytesIO (non e' un file
    # handle da chiudere: GC lo gestisce)
    assert opened_handles == []


def test_multipart_builder_path_case_still_works(tmp_path):
    """Retro-compat: se input_path e' un path stringa (file_source /
    upstream file-processor), comportamento invariato."""
    connector = _make_connector()

    local_file = tmp_path / "in.csv"
    local_file.write_bytes(b"col\nval\n")

    file_fields = [{"name": "file", "source": "node_input", "required": True}]
    opened_handles = []

    files, data, err = connector._build_rest_file_multipart(
        file_fields=file_fields,
        static_fields=[],
        input_path=str(local_file),
        extra_files_cfg={},
        opened_handles=opened_handles,
    )

    assert err is None
    assert "file" in files
    # File handle deve essere registrato per cleanup downstream
    assert len(opened_handles) == 1
    # Cleanup manuale per non leakare file handle nel test
    opened_handles[0].close()


def test_multipart_builder_none_input_with_required_returns_error():
    connector = _make_connector()

    file_fields = [{"name": "file", "source": "node_input", "required": True}]
    opened_handles = []

    files, data, err = connector._build_rest_file_multipart(
        file_fields=file_fields,
        static_fields=[],
        input_path=None,
        extra_files_cfg={},
        opened_handles=opened_handles,
    )

    assert err is not None
    assert err["success"] is False
    assert "richiesto" in err["error"].lower() or "input" in err["error"].lower()


def test_multipart_builder_none_input_not_required_skips():
    connector = _make_connector()

    file_fields = [{"name": "file", "source": "node_input", "required": False}]
    opened_handles = []

    files, data, err = connector._build_rest_file_multipart(
        file_fields=file_fields,
        static_fields=[],
        input_path=None,
        extra_files_cfg={},
        opened_handles=opened_handles,
    )

    assert err is None
    assert "file" not in files
