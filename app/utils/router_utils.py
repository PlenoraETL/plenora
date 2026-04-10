"""
Utility condivise per i router FastAPI.

Funzioni helper che eliminano pattern ripetuti nella gestione
dell'inizializzazione lazy dei moduli.
"""

from fastapi import HTTPException


def require_service(service, name: str):
    """
    Verifica che un service module-level sia stato inizializzato.

    Da chiamare all'inizio degli endpoint che dipendono da servizi
    iniettati via init_*() da main.py. Solleva HTTP 503 con messaggio
    chiaro invece di un generico AttributeError su NoneType.

    Args:
        service: il valore del global (_service, _executor, etc.)
        name:    nome leggibile del servizio (per il messaggio di errore)

    Raises:
        HTTPException(503): se il service è None

    Example:
        require_service(_connection_service, "ConnectionService")
        conn = _connection_service.get_connection(connection_id)
    """
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=f"{name} non inizializzato — verificare che init_*() sia stato chiamato da main.py"
        )
