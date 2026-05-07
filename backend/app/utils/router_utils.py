"""
Utility condivise per i router FastAPI.

Funzioni helper che eliminano pattern ripetuti nella gestione
"""

from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http


def require_service(service, name: str):
    """
    Verifica che un service module-level sia stato inizializzato.

    Da chiamare all'inizio degli endpoint che dipendono da servizi
    iniettati via init_* da main.py. Solleva HTTP 503 con codice
    `service_not_initialized` invece di un generico AttributeError
    su NoneType.

    Args:
        service: il valore del global (_service, _executor, etc.)
        name: nome leggibile del servizio (per il messaggio di errore)

    Raises:
        HTTPException(503): se il service è None, con `error =
        error_codes.SERVICE_NOT_INITIALIZED`.

    Example:
        require_service(_connection_service, "ConnectionService")
        conn = _connection_service.get_connection(connection_id)
    """
    if service is None:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=503,
            message=f"{name} non inizializzato — verificare che init_*() sia stato chiamato da main.py",
        )
