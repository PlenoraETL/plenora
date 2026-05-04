"""
PLENORA-ETL — Global API error envelope (BE-GO-READY-ERROR-CONTRACT-002).

Helper centrale per costruire risposte di errore con shape standard
go-ready:

    {
      "error":   "code_snake_case",
      "message": "frase italiana per UI / log",
      "details": { ...optional_structured_payload... }
    }

Compatibilita' transitoria
==========================
Per non rompere i ~10 test contract gia' esistenti che lockano
`response.json()["detail"]["error"]` (forma FastAPI legacy), il body
include un `detail` mirror della stessa shape. Schema dual-shape:

    {
      "error":   "...",
      "message": "...",
      "details": {...},
      "detail": { "error": "...", "message": "...", "details": {...} }
    }

Il mirror `detail` e' una copia delle stesse 3 chiavi. Quando il FE
avra' migrato i consumer su `response.json().error`, un batch
successivo rimuovera' il mirror.

Boundary
========
Modulo puro: nessun I/O, nessun env read, nessuna importazione di
servizi/repository. La risorsa esposta e' una funzione che riceve
parametri espliciti e ritorna una `JSONResponse` o un dict
serializzabile. Coerente con BE_RULES §3 (I/O fuori dal core).

Headers di response (CORS, X-Request-ID) vengono passati come
argomento dal caller (l'exception handler ha accesso al `Request`),
quindi questo helper non li costruisce: si limita a includerli
nella risposta finale se forniti.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from fastapi.responses import JSONResponse


def build_error_envelope_body(
    error: str,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Costruisce il body dual-shape dell'error envelope.

    Args:
        error: codice machine-readable, snake_case (es.
            `"internal_error"`, `"canvas_corrupted"`,
            `"connection_in_use"`).
        message: testo italiano user-facing.
        details: dict opzionale di dati strutturati. Se `None`, la
            chiave `details` resta presente con `{}` per mantenere
            stabile il contratto top-level dell'envelope.

    Returns:
        Dict serializzabile in JSON, con:
        - top-level `error`, `message`, `details`;
        - `detail` mirror con le stesse chiavi (compat FastAPI
          legacy).

    La copia mirror e' shallow: `details` e' lo stesso oggetto in
    entrambi i punti. Il body NON viene mutato dopo la costruzione
    (immutabile per convenzione del caller); sharing e' sicuro.
    """
    safe_details = dict(details) if details is not None else {}
    envelope: Dict[str, Any] = {
        "error": error,
        "message": message,
        "details": safe_details,
    }

    mirror: Dict[str, Any] = {
        "error": error,
        "message": message,
        "details": safe_details,
    }

    envelope["detail"] = mirror
    return envelope


def build_error_response(
    status_code: int,
    error: str,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> JSONResponse:
    """Costruisce la `JSONResponse` finale con dual-shape envelope.

    Args:
        status_code: HTTP status (es. 500, 404, 409).
        error: codice machine-readable.
        message: testo user-facing.
        details: dict strutturato opzionale.
        headers: dict di header da aggiungere alla response (CORS,
            altri). `X-Request-ID` NON va passato qui: e' propagato
            automaticamente dal middleware sulla response in uscita.

    Returns:
        `JSONResponse` con body dual-shape e headers forniti.
    """
    body = build_error_envelope_body(error=error, message=message, details=details)
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=dict(headers) if headers else None,
    )


def normalize_http_exception_detail(
    status_code: int,
    detail: Any,
) -> Dict[str, Any]:
    """Normalizza il `detail` di un `HTTPException` nelle 3 chiavi
    canoniche `error`/`message`/`details`.

    Mapping:
    - `detail: dict` con chiavi `error` e `message` → riusa quei
      valori; gli altri campi del dict (es. `dependencies`,
      `project_id`, ecc.) confluiscono in `details` se non e'
      gia' presente; se il dict gia' contiene `details`, viene
      preservato as-is.
    - `detail: dict` senza `error`/`message` → fallback:
      `error = "http_<status>"`, `message = json.dumps-like
      stringification` minimale; `details` = il dict intero.
    - `detail: str` non vuota → `error = "http_<status>"`,
      `message = detail`, `details = None`.
    - `detail: None`/altro → `error = "http_<status>"`,
      `message = "HTTP <status>"`, `details = None`.

    Returns:
        Dict con chiavi: `error` (str), `message` (str), `details`
        (dict|None).
    """
    if isinstance(detail, dict):
        has_error = "error" in detail and isinstance(detail["error"], str)
        has_message = "message" in detail and isinstance(detail["message"], str)
        if has_error and has_message:
            error = detail["error"]
            message = detail["message"]
            if "details" in detail and isinstance(detail["details"], dict):
                details = detail["details"]
            else:
                # Estrarre i campi extra (escluso error/message/details)
                # in `details` per uniformita'. Se non ce ne sono,
                # `details` resta None.
                extras = {
                    k: v for k, v in detail.items()
                    if k not in ("error", "message", "details")
                }
                details = extras if extras else None
            return {"error": error, "message": message, "details": details}
        return {
            "error": f"http_{status_code}",
            "message": str(detail),
            "details": detail,
        }

    if isinstance(detail, str) and detail:
        return {
            "error": f"http_{status_code}",
            "message": detail,
            "details": None,
        }

    return {
        "error": f"http_{status_code}",
        "message": f"HTTP {status_code}",
        "details": None,
    }
