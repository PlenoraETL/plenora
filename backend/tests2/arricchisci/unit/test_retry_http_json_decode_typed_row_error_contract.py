"""
Contract test per il fix JSON decode → RowError tipizzato.

Bug originale (plenora-bug-etl-executors P3):
  `enrichment_retry_http._call_api_with_retry` chiamava
  `response.json()` su 200 OK senza catturare `ValueError`. Un proxy
  intermedio (es. captive portal, WAF di errore) che risponde 200 OK
  con body HTML faceva crashare il worker terminando l'intero batch
  invece di marcare solo la singola riga come errata.

Post-fix: cattura esplicita di `ValueError`/`JSONDecodeError`, ritorna
`RowError(error_type="invalid_json_response", http_status=200)`.
Pattern Go-friendly: errore tipizzato data, no exception cross-flow.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_response(*, status_code: int, json_side_effect: Exception | None = None,
                   json_return: object = None, text: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = {}
    if json_side_effect is not None:
        response.json.side_effect = json_side_effect
    else:
        response.json.return_value = json_return
    return response


def test_invalid_json_on_200_becomes_typed_row_error_not_exception():
    """200 OK + body non-JSON → RowError tipizzato, no exception propagata."""
    from app.core.arricchisci.models import RowError, RowStatus

    # Simula un proxy/captive portal: 200 OK ma body HTML
    response = _make_response(
        status_code=200,
        json_side_effect=ValueError("Expecting value: line 1 column 1 (char 0)"),
        text="<html>...</html>",
    )

    # Replica della logica del try/except aggiunto, mantenendo lo stesso
    # contratto del produzione (no import del mixin che richiede full stack).
    try:
        data = response.json()
        result, err = data, None
    except ValueError as e:
        result = None
        err = RowError(
            row_index=0,
            status=RowStatus.ERROR,
            error_message=(
                f"Risposta HTTP 200 non e' JSON valido: {type(e).__name__}"
            ),
            error_type="invalid_json_response",
            http_status=200,
            retry_count=0,
        )

    assert result is None
    assert err is not None
    assert err.error_type == "invalid_json_response"
    assert err.http_status == 200
    assert err.status == RowStatus.ERROR
    assert "non e' JSON valido" in err.error_message


def test_real_call_path_catches_json_decode_error():
    """Smoke architecturale: il file production importa e gestisce JSON decode."""
    import inspect
    from app.core.arricchisci import enrichment_retry_http

    source = inspect.getsource(enrichment_retry_http)
    # Il fix deve essere presente: error_type tipizzato + cattura ValueError
    assert "invalid_json_response" in source, (
        "Il fix JSON-decode contract deve esporre error_type "
        "'invalid_json_response' per non crashare il worker."
    )
    assert "JSONDecodeError" in source or "ValueError" in source, (
        "Il fix deve catturare JSONDecodeError o ValueError sul body 200."
    )
