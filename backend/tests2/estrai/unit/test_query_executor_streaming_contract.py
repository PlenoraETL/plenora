"""
Contract test — streaming path di QueryExecutor (post QUERY-1 step 2).

Storia:
- Pre-QUERY-1: `QueryExecutor._execute_streaming_to_file` delegava a
  `QueryStreamingWriter.execute(...)` (port adapter) che scriveva
  CSV/Parquet su `output_path` filesystem; `_result_persistence
  .persist_streamed_file(path)` poi faceva il save_result_blob.
- QUERY-1 step 1 (commit `b720c958`): introdotto
  `StreamingResultBlobWriter` + `execute_streaming_to_writer` per
  flushare bytes cursor → S3 multipart / DB BYTEA. `_execute_streaming
  _to_file` rimase come legacy helper.
- QUERY-1 step 2 (questo file): tombstoned definitivamente
  `QueryStreamingWriter`, `_execute_streaming_to_file`,
  `execute_streaming_to_file`, e il param `streaming_writer` di
  `QueryExecutor`. Lo streaming canonical e' single-call via
  `persist_streaming_query_to_storage`.

Questo file resta come sentinel anti-regression sul tombstone.
"""

from __future__ import annotations

import pytest

from app.core.query_execution.query_executor import QueryExecutor


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract, pytest.mark.regression]


def test_query_executor_streaming_writer_legacy_tombstoned():
    """`_execute_streaming_to_file` rimosso dal QueryExecutor.
    Anti-regression sul tombstone QUERY-1 step 2."""
    assert not hasattr(QueryExecutor, "_execute_streaming_to_file"), (
        "_execute_streaming_to_file deve essere tombstoned post-QUERY-1 "
        "step 2. Lo streaming branch chiama direttamente "
        "`persist_streaming_query_to_storage` sul `_result_persistence`."
    )


def test_query_streaming_writer_class_legacy_tombstoned():
    """`QueryStreamingWriter` rimosso da `query_streaming`.
    Anti-regression sul port adapter dead."""
    import app.core.query_execution.query_streaming as qs
    assert not hasattr(qs, "QueryStreamingWriter"), (
        "QueryStreamingWriter port adapter rimosso post-QUERY-1 step 2."
    )
    assert not hasattr(qs, "execute_streaming_to_file"), (
        "execute_streaming_to_file(path) rimosso post-QUERY-1 step 2. "
        "Usa execute_streaming_to_writer(file-like)."
    )


def test_query_executor_does_not_take_streaming_writer_param():
    """`QueryExecutor.__init__` non accetta piu' `streaming_writer`.
    Anti-regression sul tombstone del param."""
    import inspect
    sig = inspect.signature(QueryExecutor.__init__)
    assert "streaming_writer" not in sig.parameters, (
        "streaming_writer param rimosso post-QUERY-1 step 2."
    )


def test_query_executor_uses_streaming_persist_path():
    """Source-level pin: lo streaming branch invoca
    `persist_streaming_query_to_storage`. Cattura regressioni che
    re-introducano il path-based legacy.

    Il match gira sull'AST: gli identificatori sono colti solo come codice,
    quindi i tombstone comment che menzionano i nomi rimossi non danno falsi
    positivi (niente strip manuale dei commenti) e una riformattazione non puo'
    reintrodurre il path legacy non visto.
    """
    from pathlib import Path

    from tests2.shared._ast_identifiers import (
        all_identifiers,
        imports_symbol_from,
        self_attribute_names,
        uses_attribute,
    )

    raw = (
        Path(__file__).resolve().parents[3]
        / "app" / "core" / "query_execution" / "query_executor.py"
    ).read_text(encoding="utf-8")
    identifiers = all_identifiers(raw)

    assert "persist_streaming_query_to_storage" in identifiers, (
        "QueryExecutor deve invocare persist_streaming_query_to_storage "
        "nel branch _streamable."
    )
    # Il vecchio pattern legacy NON deve essere tornato.
    assert "QueryStreamingWriter" not in identifiers, "import legacy non deve tornare"
    assert "_streaming_writer" not in self_attribute_names(raw), (
        "_streaming_writer attribute non deve tornare."
    )
    assert not imports_symbol_from(
        raw, "app.core.results.result_store", "save_result_blob"
    )
    assert not uses_attribute(raw, "csv", "writer")
    assert "ParquetWriter" not in identifiers
    assert "pyarrow" not in identifiers
