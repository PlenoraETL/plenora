"""
PLENORA-ETL — Global API error envelope .

Helper centrale per costruire risposte di errore con shape standard
"""

from __future__ import annotations

from typing import Any, Mapping, NoReturn, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.shared.contracts.payloads import JsonObject


def build_error_envelope_body(
    error: str,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
) -> JsonObject:
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
        - `detail` mirror con le stesse chiavi (compat FastAPI).

    La copia mirror e' shallow: `details` e' lo stesso oggetto in
    entrambi i punti. Il body NON viene mutato dopo la costruzione
    (immutabile per convenzione del caller); sharing e' sicuro.
    """
    safe_details = dict(details) if details is not None else {}
    envelope: JsonObject = {
        "error": error,
        "message": message,
        "details": safe_details,
    }

    mirror: JsonObject = {
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
) -> JsonObject:
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
                # Estrarre i campi extra (escluso error/message/details
                # in `details` per uniformita'. Se non ce ne sono
                # `details` resta None
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


def raise_typed_http(
    code: str,
    status_code: int,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> NoReturn:
    """Helper opzionale per sollevare `HTTPException` con shape
    structured-detail compatibile con il central handler envelope.

    Solleva `HTTPException(status_code=<status_code>, detail={...})`
    dove `detail` e' la dict-shape `{error, message, details}`
    che il central `StarletteHTTPException` handler in `app/main.py`
    sa normalizzare via `normalize_http_exception_detail` nello
    stesso envelope dual-shape dei call site dict-detail manuali.

    Adoption:
    - Helper opzionale: i call site esistenti restano invariati.
    - Nuovi `raise` POSSONO usare questo helper per beneficiare
      del catalog (`error_codes.<CONST>` o `app.core.<domain>.errors.<CONST>`) + del round-trip
      envelope.
    - L'adozione sui call site esistenti e' lavoro separato.

    Args:
        code: codice machine-readable. Convenzione snake_case.
            Idealmente dal catalogo condiviso o dal modulo errori del dominio.
            Stringa libera ammessa per estendibilita',
            ma il drift test
            `test_error_codes_catalog::test_catalog_covers_all_router_emitted_codes`
            controllera' che i nuovi literal siano nel catalog.
        status_code: HTTP status (es. 409, 500, 503).
        message: testo italiano user-facing per UI.
        details: dict opzionale di dati strutturati (es.
            `{"dependencies": [...]}`). Se `None`, diventa `{}`.
        headers: dict opzionale di header da preservare nella
            response. Necessario per i call site auth-domain che
            emettono `WWW-Authenticate: Bearer` (401 token-related)
            o `Retry-After` (429 rate-limit). Se `None`, la response
            non riceve header custom.

    Raises:
        `HTTPException` con `detail = {"error": code, "message":
        message, "details": details or {}}`; il central handler lo
        normalizza nell'envelope canonico. Status code e headers
        preservati.

    Note:
        Il return type e' `NoReturn` perche' la funzione solleva
        sempre. Tipato esplicitamente per IDE / type-checker.
    """
    if not message:
        raise ValueError("message must be non-empty")

    safe_details: JsonObject = {}
    if details:
        safe_details = {
            k: v for k, v in details.items()
            if k not in ("error", "message")
        }

    detail: JsonObject = {
        "error": code,
        "message": message,
        "details": safe_details,
    }
    if headers is not None:
        raise HTTPException(
            status_code=status_code, detail=detail, headers=dict(headers)
        )
    raise HTTPException(status_code=status_code, detail=detail)


def raise_credential_unavailable(exc: Exception) -> NoReturn:
    """Raise the canonical HTTP response for unavailable secrets.

    This helper intentionally keeps ``detail`` as a string because several
    router-level contract tests mount routes without the global envelope
    handler. In the full app the central handler still normalizes the
    response shape.
    """
    raise HTTPException(status_code=422, detail=str(exc))
