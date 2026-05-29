"""Step C — unificazione preview ARRICCHISCI.

`EnrichExecutor.preview` non reimplementa piu' il loop per-nodo: delega al
loop canonico `execute_canvas_enrichment` passando `preview_limit` e un
`finalizer` che costruisce le righe in-memory senza persistere il blob.

Questi test coprono il codice NUOVO introdotto dal refactor:
- il wiring (preview_limit + finalizer + mapping degli early-return);
- `_build_preview_response` (tipizzazione colonne, troncamento, total_count).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.arricchisci.executor import EnrichExecutor


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def _canvas(nodes=None, edges=None):
    """Fake canvas con solo `model_dump` (cio' che usa preview)."""
    return SimpleNamespace(
        model_dump=lambda: {"nodes": nodes or [], "edges": edges or []}
    )


def test_preview_delegates_with_preview_limit_and_finalizer():
    """preview chiama execute_canvas_enrichment con preview_limit==limit e un
    finalizer callable, e ritorna la shape prodotta dal finalizer."""
    captured = {}

    def _fake_exec(svc, **kwargs):
        captured["svc"] = svc
        captured.update(kwargs)
        # Simula il loop canonico che invoca il finalizer sul df finale.
        df = pd.DataFrame({"a": [1, 2]})
        return kwargs["finalizer"](df, "out", "csv", "proj-1")

    with patch(
        "app.core.arricchisci.executor.execute_canvas_enrichment",
        side_effect=_fake_exec,
    ):
        result = EnrichExecutor(MagicMock()).preview("proj-1", _canvas(), limit=7)

    assert captured["preview_limit"] == 7
    assert callable(captured["finalizer"])
    assert result["success"] is True
    assert result["rows"] == [{"a": 1}, {"a": 2}]
    assert result["columns"] == [{"name": "a", "type": "int"}]


def test_preview_maps_canonical_error_to_message():
    """Un early-return di errore del loop canonico (chiave `error`) viene
    mappato al contratto preview {success: False, message}."""
    with patch(
        "app.core.arricchisci.executor.execute_canvas_enrichment",
        return_value={"success": False, "error": "Nessun nodo nel canvas"},
    ):
        result = EnrichExecutor(MagicMock()).preview("proj-1", _canvas(), limit=10)

    assert result == {"success": False, "message": "Nessun nodo nel canvas"}


def test_preview_maps_file_result_to_message():
    """Un risultato file-based non e' previewabile come righe."""
    with patch(
        "app.core.arricchisci.executor.execute_canvas_enrichment",
        return_value={"success": True, "output_type": "file", "result_id": "r1"},
    ):
        result = EnrichExecutor(MagicMock()).preview("proj-1", _canvas(), limit=10)

    assert result["success"] is False
    assert "file" in result["message"].lower()


def test_build_preview_response_types_rows_and_truncation():
    """_build_preview_response: tipizza le colonne, tronca a `limit`,
    espone total_count e il flag truncated."""
    df = pd.DataFrame(
        {
            "n": [1, 2, 3, 4, 5],
            "name": ["a", "b", "c", "d", "e"],
            "geometry": ["POINT (1 1)"] * 5,
        }
    )
    out = EnrichExecutor(MagicMock())._build_preview_response(df, limit=3)

    assert out["success"] is True
    assert out["total_count"] == 5
    assert out["preview_count"] == 3
    assert out["truncated"] is True
    types = {c["name"]: c["type"] for c in out["columns"]}
    assert types["n"] == "int"
    assert types["name"] == "string"
    assert types["geometry"] == "geometry"


def test_build_preview_response_empty_dataframe_returns_message():
    out = EnrichExecutor(MagicMock())._build_preview_response(pd.DataFrame(), limit=10)
    assert out["success"] is False
    assert "Nessun dato" in out["message"]
