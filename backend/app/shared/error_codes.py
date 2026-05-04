"""
PLENORA-ETL — Catalog dei codici stringa per error envelope
(BE-GO-READY-ERROR-CONTRACT-002).

Costanti per i codici `error` machine-readable usati dall'envelope
go-ready (`build_error_envelope_body`). Centralizzare i codici
previene typo nei router e fornisce un'unica sorgente di verita'
quando il sistema verra' portato in Go (i tag JSON delle struct di
risposta puntano a queste stringhe).

Convenzioni:
- `snake_case`;
- specificita' progressiva (`http_500` generico, `internal_error`
  per il catch-all, codici domain-specific per casi business);
- nessuna i18n: i codici sono stringhe stabili, i testi italiani
  vivono in `message`.

Inventario iniziale: derivato dai codici gia' usati nei test
contract pre-existing (es. `connection_in_use`,
`project_has_dependencies`) + i nuovi introdotti dal pilot
(`internal_error`, `canvas_corrupted`, `http_<status>`).

NOTA: questo modulo NON valida che ogni router usi solo codici
elencati qui. E' un catalog di riferimento + facility per le import
nei test. I router possono usare stringhe libere durante la
transizione; il ridimensionamento a enum/registry typed e' Phase
go-ready dedicata.
"""

from __future__ import annotations


# ============================================================
# Generic / framework-level
# ============================================================

INTERNAL_ERROR = "internal_error"
"""Catch-all per eccezioni Python non gestite (status 500)."""

CANVAS_CORRUPTED = "canvas_corrupted"
"""Canvas DB-side corrotto/non parsabile (status 500)."""

VALIDATION_ERROR = "validation_error"
"""Errore di validazione richiesta (status 422). Mappato su
`RequestValidationError` di FastAPI/Pydantic."""


def http_status_code(status_code: int) -> str:
    """Genera il codice generico `http_<status>` per
    `HTTPException(detail=<str>)` senza payload strutturato.

    Esempi: `http_404`, `http_409`, `http_400`. Usato come
    fallback dall'`HTTPException` handler quando l'eccezione non
    espone un codice domain-specific.
    """
    return f"http_{status_code}"


# ============================================================
# Domain-specific (codici gia' lockati nei test esistenti)
# ============================================================

CONNECTION_IN_USE = "connection_in_use"
"""409: tentativo di delete di una connection referenziata."""

API_CONNECTION_IN_USE = "api_connection_in_use"
"""409: tentativo di delete di un'api_connection referenziata."""

PROJECT_HAS_DEPENDENCIES = "project_has_dependencies"
"""409: tentativo di delete progetto con discendenti."""

PROJECT_CLEANUP_PARTIAL = "project_cleanup_partial"
"""500: cleanup progetto fallito su step derivati (row preservata)."""

DATASOURCE_IN_USE = "datasource_in_use"
"""409: tentativo di delete di un datasource referenziato."""
