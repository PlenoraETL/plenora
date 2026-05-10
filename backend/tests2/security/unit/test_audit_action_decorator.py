"""
Contract test decorator @audit_action — audit trail automatico.

Scope:
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# PR #34 P1-B: pattern B2.0 — handler stdlib agganciato al logger
# reale invece di monkeypatch del symbol module-level con MagicMock.
# La fixture `audit_write_raises` ora ritorna un `_ListHandler` invece
# di un MagicMock spy: i 3 consumer (line ~235, 239, 268) leggono i
# record reali emessi dal logger.
class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


_AUDIT_LOGGER = "app.observability.audit_action"


# Fixture: cattura chiamate a pg_connection.execute via monkeypatch


@pytest.fixture
def audit_writes(monkeypatch):
    """Sostituisce `_execute_write` nel modulo audit_action con uno spy
    che registra gli argomenti di ogni INSERT.

    Patcha il layer di indirection interno (non `pg_connection.execute`
    direttamente) cosi' il test non dipende dall'import di `app.db`:
    `pg_connection` fa check `DATABASE_URL` al top-level e rompe l'import
    del modulo audit se l'env non e' settata. Vedi audit_action.py:
    `_execute_write` usa lazy import di pg_connection proprio per questo.

    Ritorna una lista di tuple (sql, params). Azzera anche le metriche
    in-process per garantire isolamento cross-test.
    """
    from app.observability import audit_action as audit_mod

    audit_mod._reset_metrics_for_tests()

    writes: list[tuple[str, tuple]] = []

    def _fake_execute(sql, params):
        writes.append((sql, tuple(params)))

    monkeypatch.setattr(audit_mod, "_execute_write", _fake_execute)
    return writes


@pytest.fixture
def audit_write_raises(monkeypatch):
    """Fa sollevare ogni tentativo di INSERT su audit_actions.

    Patcha `_execute_write` (non `pg_connection.execute`) per la stessa
    ragione descritta in `audit_writes`: evitare di triggerare il check
    `DATABASE_URL` durante l'import del modulo audit nei test.

    Usato nel test di fail-open. Ritorna un MagicMock spy del logger di
    modulo cosi' il test puo' verificare che il warning sia stato emesso.
    """
    from app.observability import audit_action as audit_mod

    audit_mod._reset_metrics_for_tests()

    boom = RuntimeError("simulated PG outage")

    def _raising_execute(sql, params):
        raise boom

    monkeypatch.setattr(audit_mod, "_execute_write", _raising_execute)

    # PR #34 P1-B: handler stdlib clean-up via yield (no monkeypatch
    # del symbol logger).
    target_logger = logging.getLogger(_AUDIT_LOGGER)
    handler = _ListHandler()
    target_logger.addHandler(handler)
    try:
        yield SimpleNamespace(exc=boom, handler=handler)
    finally:
        target_logger.removeHandler(handler)


def _fake_user(user_id="u_42"):
    return SimpleNamespace(
        user_id=user_id, username="alice", group_id="g_1", role="user"
    )


def _fake_request(host="10.0.0.7"):
    client = SimpleNamespace(host=host)
    return SimpleNamespace(client=client)


# 1. Success path


def test_success_path_writes_audit_row_with_status_200(audit_writes):
    """Quando la funzione wrappata ritorna, l'audit deve scrivere una riga
    con il user_id corretto, action specificata, target estratto dal kwarg
    indicato, status=200 e error_message=NULL.
    """
    from app.observability.audit_action import audit_action, get_metrics

    @audit_action(action="project.delete", target_param="project_id")
    def delete_project(
        project_id: str, current_user=None, request=None
    ):
        return {"deleted": project_id}

    result = delete_project(
        project_id="proj_abc",
        current_user=_fake_user("user_42"),
        request=_fake_request("192.0.2.10"),
    )

    assert result == {"deleted": "proj_abc"}, "valore di ritorno passthrough"
    assert len(audit_writes) == 1, "esattamente una INSERT audit"
    sql, params = audit_writes[0]
    assert "audit_actions" in sql
    assert params == (
        "user_42",           # user_id
        "project.delete",    # action
        "proj_abc",          # target
        200,                 # status
        "192.0.2.10",        # ip_address
        None,                # error_message
    ), f"params inatteso: {params}"

    # Metric check: write_ok incrementato, write_fail no.
    metrics = get_metrics()
    assert metrics["counters"]["write_ok"] == 1
    assert metrics["counters"]["write_fail"] == 0
    assert metrics["last_ok_epoch"] is not None


# 2. Failure path applicativo (HTTPException) — audit comunque tentato


def test_http_exception_is_audited_with_status_and_reraised(audit_writes):
    """Quando la funzione solleva HTTPException(404), l'audit registra
    status=404 e error_message dal detail. L'HTTPException viene
    ri-sollevata al chiamante (mai swallowed).
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="project.delete", target_param="project_id")
    def delete_project(project_id, current_user=None):
        raise HTTPException(status_code=404, detail="Progetto non trovato")

    with pytest.raises(HTTPException) as excinfo:
        delete_project(project_id="proj_missing", current_user=_fake_user("user_7"))

    assert excinfo.value.status_code == 404, "HTTPException ri-sollevata"
    assert excinfo.value.detail == "Progetto non trovato"

    assert len(audit_writes) == 1, "audit scritto anche sul failure path"
    _sql, params = audit_writes[0]
    assert params == (
        "user_7",
        "project.delete",
        "proj_missing",
        404,
        None,              # nessun request in kwargs → ip_address=None
        "Progetto non trovato",
    )


def test_generic_exception_is_audited_as_500_and_reraised(audit_writes):
    """Eccezione non-HTTP → audit status=500 + message type-prefixed,
    eccezione ri-sollevata. Copre il ramo 'crash' del decorator.
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="datasource.create")
    def create_datasource(payload, current_user=None):
        raise RuntimeError("driver oracledb non installato")

    with pytest.raises(RuntimeError, match="oracledb"):
        create_datasource(payload={"kind": "oracle"}, current_user=_fake_user())

    assert len(audit_writes) == 1
    _sql, params = audit_writes[0]
    assert params[1] == "datasource.create"
    assert params[3] == 500, "status=500 per eccezioni non-HTTP"
    assert params[5].startswith("RuntimeError:"), (
        "error_message prefissato col tipo eccezione per disambiguazione"
    )
    assert "oracledb" in params[5]


# 3. Fail-open: audit write failure NON rompe il path principale


def test_audit_write_failure_does_not_break_business_path(audit_write_raises):
    """Se pg_connection.execute solleva (PG down, pool exhausted),
    il decorator deve:
      - NON propagare l'eccezione di audit alla funzione wrappata
      - restituire il valore originale della funzione (success path)
      - emettere un logger.warning con contesto diagnostico
      - incrementare la metrica write_fail
    """
    from app.observability.audit_action import audit_action, get_metrics

    @audit_action(action="user.logout")
    def logout(current_user=None):
        return {"ok": True}

    # Il business path DEVE completare normalmente, anche se la write
    # audit solleva dietro le quinte.
    result = logout(current_user=_fake_user("user_logout"))
    assert result == {"ok": True}, (
        "REGRESSIONE fail-open: la business function deve restituire il "
        "proprio valore, non propagare l'errore di audit"
    )

    # NOTE: osservabile: il segnale che l'audit non ha scritto.
    warnings = [
        r for r in audit_write_raises.handler.records
        if r.levelno == logging.WARNING
    ]
    assert warnings, (
        "REGRESSIONE fail-open: il warning di audit_action_write_failed "
        "deve essere emesso per dare visibilita' al problema"
    )
    assert any(
        "audit_action_write_failed" in r.getMessage() for r in warnings
    )

    # Metrica incrementata.
    metrics = get_metrics()
    assert metrics["counters"]["write_fail"] == 1
    assert metrics["counters"]["write_ok"] == 0


def test_audit_write_failure_does_not_mask_business_exception(audit_write_raises):
    """Se il business path solleva HTTPException E la write audit fallisce,
    deve uscire l'eccezione di business (non quella di audit, che resta
    confinata al warning).
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="project.delete", target_param="project_id")
    def delete_project(project_id, current_user=None):
        raise HTTPException(status_code=403, detail="Permesso negato")

    with pytest.raises(HTTPException) as excinfo:
        delete_project(project_id="p1", current_user=_fake_user())

    # L'eccezione che esce e' quella business, non quella audit.
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Permesso negato"

    # NOTE: comunque emesso: l'audit fail-open ha comunque tentato di
    # tracciare il 403 prima di propagare.
    assert any(
        r.levelno == logging.WARNING for r in audit_write_raises.handler.records
    )


# 4. Anonymous / system action — user assente gestito esplicitamente


def test_anonymous_action_writes_null_user_id(audit_writes):
    """Quando `current_user` non compare nei kwargs (es. endpoint pubblico
    o task di sistema), l'audit deve scrivere con user_id=None — NON con
    sentinel stringa tipo 'anonymous' o '' — cosi' le query di forensics
    possono filtrare `WHERE user_id IS NULL` per isolare azioni di sistema.
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="auth.login")
    def login(username: str):
        # login flow: nessun current_user nel body, e' proprio l'esito
        # che determina l'identita' dell'utente.
        return {"login": "ok", "user": username}

    result = login(username="alice")
    assert result["login"] == "ok"

    assert len(audit_writes) == 1
    _sql, params = audit_writes[0]
    assert params[0] is None, (
        "REGRESSIONE: user_id deve essere None (SQL NULL) per azioni "
        "senza current_user, non sentinel stringa"
    )
    assert params[1] == "auth.login"


def test_current_user_explicit_none_writes_null_user_id(audit_writes):
    """Caso bordo: current_user=None esplicito → user_id=None.
    Equivalente al caso sopra, ma con kwarg presente: serve a bloccare
    regressioni dove `kwargs.get('current_user')` fosse sostituito da
    `kwargs['current_user'].user_id` (KeyError su None).
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="system.cleanup")
    def cleanup_task(current_user=None):
        return {"cleaned": 3}

    cleanup_task(current_user=None)
    assert len(audit_writes) == 1
    _sql, params = audit_writes[0]
    assert params[0] is None


# 5. Async path — stessa semantica su funzione async


def test_async_function_success_writes_audit(audit_writes):
    """Il decorator wrappa anche async def conservando la semantica sync:
    row scritta su success con status=200.
    """
    import asyncio

    from app.observability.audit_action import audit_action

    @audit_action(action="file.upload", target_param="file_id")
    async def upload_file(file_id, current_user=None):
        return {"uploaded": file_id}

    result = asyncio.run(
        upload_file(file_id="f_123", current_user=_fake_user("user_async"))
    )
    assert result == {"uploaded": "f_123"}
    assert len(audit_writes) == 1
    _sql, params = audit_writes[0]
    assert params == (
        "user_async",
        "file.upload",
        "f_123",
        200,
        None,
        None,
    )


def test_async_function_http_exception_audited_and_reraised(audit_writes):
    """Branch failure su async: HTTPException re-raised, audit con status."""
    import asyncio

    from app.observability.audit_action import audit_action

    @audit_action(action="file.upload")
    async def upload_file(current_user=None):
        raise HTTPException(status_code=413, detail="Payload too large")

    async def _invoke():
        await upload_file(current_user=_fake_user())

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_invoke())

    assert excinfo.value.status_code == 413
    assert len(audit_writes) == 1
    assert audit_writes[0][1][3] == 413


# 6. Target extraction — solo str/int accettati


def test_target_extraction_int_is_stringified(audit_writes):
    """target_param='id' con valore int → audit registra str(id)."""
    from app.observability.audit_action import audit_action

    @audit_action(action="ticket.update", target_param="id")
    def update_ticket(id: int, current_user=None):
        return {"id": id}

    update_ticket(id=42, current_user=_fake_user())
    assert audit_writes[0][1][2] == "42"


def test_target_extraction_rejects_non_scalar_types(audit_writes):
    """target_param punta a un oggetto (dict/Pydantic) → target=NULL.

    Protezione contro payload abuse: il decorator non serializza oggetti
    arbitrari nella colonna target. Conservativo: se il caller vuole
    audit di una risorsa complessa, deve passare l'id come kwarg
    dedicato (es. project_id separato dal payload).
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="project.update", target_param="payload")
    def update_project(payload: dict, current_user=None):
        return payload

    update_project(payload={"id": "proj_1", "name": "x"}, current_user=_fake_user())
    assert audit_writes[0][1][2] is None, (
        "target non-scalare → NULL nella colonna target, non JSON serializzato"
    )


def test_target_param_none_writes_null_target(audit_writes):
    """Decorator senza target_param → colonna target sempre NULL,
    indipendentemente dagli altri kwargs.
    """
    from app.observability.audit_action import audit_action

    @audit_action(action="session.refresh")
    def refresh(current_user=None):
        return {"refreshed": True}

    refresh(current_user=_fake_user())
    assert audit_writes[0][1][2] is None


# Guard: validazione argomenti del decorator


def test_decorator_rejects_empty_action():
    """`action` vuota o non stringa → TypeError a decoration-time,
    cosi' l'errore si vede al boot e non in produzione."""
    from app.observability.audit_action import audit_action

    with pytest.raises(TypeError, match="action"):
        audit_action(action="")

    with pytest.raises(TypeError, match="action"):
        audit_action(action=None)  # type: ignore[arg-type]


def test_decorator_rejects_non_string_target_param():
    from app.observability.audit_action import audit_action

    with pytest.raises(TypeError, match="target_param"):
        audit_action(action="x.y", target_param=123)  # type: ignore[arg-type]
