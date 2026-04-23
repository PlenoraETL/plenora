"""Contract test per sanitizzazione ``str(e)`` in ``test_connection``.

Blinda il fix Batch 8 / C1 (audit storage/provider 2026-04-22, S1→F1):
il ramo ``except Exception`` di ``DataSourceService.test_connection`` NON
deve propagare ``str(e)`` nella response model ``DataSourceTestResult.message``.

Motivazione:
- La response viene serializzata JSON verso il FE, inclusa audit log ed
  eventuali export/screenshot di supporto.
- ``str(e)`` degli SDK provider (Office365-REST-Python-Client, paramiko,
  ftplib) può contenere URL autenticati, token OAuth, credenziali parziali
  e path interni: leak equivalente a ``HTTPException(500, detail=str(e))``.

Invariants bloccati:
- ``result.success is False`` su qualunque eccezione nel catch.
- ``result.message`` === literal fisso (sanitizzato, user-facing).
- Stacktrace completo preservato lato server via ``logger.error(exc_info=True)``.
- Il log server-side mantiene il tag ``[GESTITO]`` per coerenza col cluster
  storage/provider.

Regressione protetta:
- Un refactor che reintroducesse ``message=str(e)`` (oppure
  ``message=f"... {e}"``) rompe il test e viene rigettato in review.
"""

from __future__ import annotations

import logging

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_SAFE_FALLBACK = "Impossibile testare la connessione: riprova piu' tardi"
_SERVICE_LOGGER_NAME = "app.core.datasources.service"


class _RecordCollector(logging.Handler):
    """Collector locale dei ``LogRecord`` del logger ``service``.

    Necessario perche' ``StructuredLogger`` (``app/utils/logger.py``) configura
    ``propagate=False`` sui logger applicativi → ``caplog`` (che aggancia il
    root logger) non riceve i record. Attacchiamo un handler direttamente
    al logger di modulo per test isolati e deterministici.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_service_logs():
    """Attacca un collector al logger del service e lo rimuove in teardown."""
    logger = logging.getLogger(_SERVICE_LOGGER_NAME)
    handler = _RecordCollector()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    """``app.db.pg_connection`` at-import-time richiede ``DATABASE_URL``.

    Placeholder sintetico: nessuna query reale viene eseguita nei test.
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fake:fake@localhost:5432/fake",
    )
    yield


def test_test_connection_exception_path_returns_sanitized_message(
    monkeypatch, capture_service_logs,
):
    """Core blindage C1: su eccezione nel backend, ``message`` è il literal
    sanitizzato e segreti sintetici non finiscono nella response.
    """
    from app.core.datasources.service import DataSourceService
    from app.core.datasources.models import DataSourceTestResult
    import app.core.datasources.service as svc_module

    service = DataSourceService()

    # Config valido: il flusso arriva fino a ``create_backend``.
    monkeypatch.setattr(
        service,
        "_get_full_config",
        lambda ds_id, **kw: {"id": ds_id, "type": "local", "root_path": "/tmp"},
    )

    # Segreti sintetici dentro str(e) — emula il leak SDK Office365 che a
    # volte include URL con query OAuth o header token nel detail.
    leaked_token = "SENSITIVE_TOKEN_abc123_xyz789"
    leaked_url = "https://internal-host.company.tld/secret-path?token=xyz"

    def _raising_create_backend(config):
        raise RuntimeError(
            f"Connection failed: token={leaked_token} url={leaked_url}",
        )

    monkeypatch.setattr(svc_module, "create_backend", _raising_create_backend)

    result = service.test_connection("ds-under-test")

    # Contract 1: tipo & campi di base
    assert isinstance(result, DataSourceTestResult)
    assert result.success is False, (
        f"success DEVE essere False su eccezione, ottenuto {result.success!r}"
    )

    # Contract 2: message è il literal fisso
    assert result.message == _SAFE_FALLBACK, (
        f"message DEVE essere il fallback sanitizzato "
        f"'{_SAFE_FALLBACK}', ottenuto {result.message!r}"
    )

    # Contract 3: nessun leak di segreti sintetici nella response
    assert leaked_token not in result.message, (
        "Token sensibile NON deve finire in result.message (leak via response)."
    )
    assert leaked_url not in result.message, (
        "URL interna NON deve finire in result.message (leak via response)."
    )
    assert "Connection failed" not in result.message, (
        "Il prefisso dell'exception SDK NON deve propagare alla response."
    )

    # Contract 4: server-side logging preservato con stacktrace (exc_info=True)
    error_records = [
        r for r in capture_service_logs.records if r.levelno >= logging.ERROR
    ]
    assert error_records, (
        "Deve esserci almeno un log ERROR lato server (visibilità operativa)."
    )
    tagged = [r for r in error_records if "[GESTITO]" in r.getMessage()]
    assert tagged, (
        "Il log ERROR deve usare il prefisso '[GESTITO]' per coerenza "
        "taxonomy cluster storage/provider."
    )
    # exc_info=True → record.exc_info è una tupla (type, value, tb) non-None.
    assert any(r.exc_info for r in tagged), (
        "logger.error DEVE essere chiamato con exc_info=True per preservare "
        "lo stacktrace server-side (audit §3.1 S1)."
    )


def test_test_connection_not_found_branch_unaffected_by_sanitize_fix(monkeypatch):
    """Il branch 'ds_id non trovato' (config None) precede il try/except
    ed è già safe: il fix C1 NON deve averlo toccato. Un refactor che
    uniformasse tutti i branch allo stesso literal nasconderebbe la
    distinzione 'non esiste' vs 'errore runtime' — debugging regression.
    """
    from app.core.datasources.service import DataSourceService

    service = DataSourceService()
    monkeypatch.setattr(service, "_get_full_config", lambda ds_id, **kw: None)

    result = service.test_connection("ds-missing")

    assert result.success is False
    # Il branch not-found mantiene il suo messaggio specifico.
    assert "ds-missing" in result.message
    assert "non trovato" in result.message
    # Difensivo: NON collassare con il fallback sanitizzato.
    assert result.message != _SAFE_FALLBACK, (
        "Branch not-found e branch exception DEVONO restare distinti "
        "(UX diversa, causa diversa)."
    )


def test_test_connection_success_path_unaffected_by_sanitize_fix(monkeypatch):
    """Difensivo: il path di successo non deve essere stato inquinato dal
    fix. Se la backend.test_connection() ritorna ok, la response riflette
    esattamente quella — niente sanitization applicata per errore.
    """
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.service as svc_module

    service = DataSourceService()
    monkeypatch.setattr(
        service,
        "_get_full_config",
        lambda ds_id, **kw: {"id": ds_id, "type": "local", "root_path": "/tmp"},
    )

    class _OkBackend:
        def test_connection(self):
            return {
                "ok": True,
                "message": "Connessione riuscita (3 file trovati)",
                "details": {"count": 3},
            }

    monkeypatch.setattr(svc_module, "create_backend", lambda cfg: _OkBackend())

    result = service.test_connection("ds-ok")

    assert result.success is True
    assert result.message == "Connessione riuscita (3 file trovati)"
    assert result.details == {"count": 3}
    assert result.message != _SAFE_FALLBACK
