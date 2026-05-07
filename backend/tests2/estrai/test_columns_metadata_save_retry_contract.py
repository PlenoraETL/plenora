"""
Contract test — _salva_tipi_colonne_da_canvas retry selettivo + raise.

Reperto: il save di `columns_metadata` (contratto forte per lineage
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixture — executor con project_service mockato


@pytest.fixture
def executor_with_mocks(monkeypatch):
    """Crea un EstraiExecutor con le dipendenze minime mockate.
    Torna (executor, project_service_mock) per controllare il side effect
    di `update_project_columns_metadata`."""
    from app.core.estrai.executor import EstraiExecutor

    project_service = MagicMock()
    query_executor = MagicMock()
    query_generator = MagicMock()
    connection_service = MagicMock()
    features_service = MagicMock()

    executor = EstraiExecutor(
        project_service=project_service,
        executor=query_executor,
        generator=query_generator,
        connection_service=connection_service,
        features_service=features_service,
    )

    # Accelera i backoff del helper per test veloci (post-refactor la
    # logica retry vive in app.shared.columns_metadata, patchiamo lì).
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    return executor, project_service


def _make_canvas():
    """Crea un CanvasState minimale (senza nodi) — il save passa per
    _build_canvas_type_map che ritorna {} per canvas vuoti, e usa
    fallback 'str' sulle colonne di `res`."""
    from app.shared.models import CanvasState
    return CanvasState(nodes=[], edges=[])


def _make_res():
    return {
        "success": True,
        "columns": ["id", "nome"],
    }


# 1. Happy path — 1 sola chiamata, no retry


def test_save_happy_path_one_attempt(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    executor._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

    project_service.update_project_columns_metadata.assert_called_once()
    assert res["columns_metadata"] == [
        {"name": "id", "type": "str"},
        {"name": "nome", "type": "str"},
    ]


# 2-3. Retry su transient → succeed


def test_save_retries_on_transient_and_succeeds_on_2nd(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    import psycopg2
    project_service.update_project_columns_metadata.side_effect = [
        psycopg2.OperationalError("pool exhausted"),
        True,  # 2° tentativo OK (service ritorna True quando il progetto esiste)
    ]

    executor._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

    assert project_service.update_project_columns_metadata.call_count == 2
    assert "columns_metadata" in res


def test_save_retries_on_deadlock_succeeds_on_3rd(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    import psycopg2.errors as pg_err
    # DeadlockDetected ha un costruttore particolare (eredita da Error)
    # — usiamo il parent OperationalError di psycopg2 per sicurezza
    import psycopg2
    project_service.update_project_columns_metadata.side_effect = [
        psycopg2.OperationalError("deadlock 1"),
        TimeoutError("connection wait"),
        True,
    ]

    executor._salva_tipi_colonne_da_canvas("proj-1", canvas, res)
    assert project_service.update_project_columns_metadata.call_count == 3


# 4. Fail dopo 3 retry transient → raise ColumnsMetadataSaveError


def test_save_raises_after_all_retries_exhausted(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    import psycopg2
    err = psycopg2.OperationalError("pool persistently exhausted")
    # 4 tentativi → 4 eccezioni uguali
    project_service.update_project_columns_metadata.side_effect = [err, err, err, err]

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("proj-retry-fail", canvas, res)

    assert project_service.update_project_columns_metadata.call_count == 4
    caught = exc_info.value
    assert caught.project_id == "proj-retry-fail"
    assert caught.attempts == 4
    assert caught.result_already_persisted is True
    assert "OperationalError" in caught.reason
    # __cause__ preservato (chained exception → debug con traceback completo)
    assert exc_info.value.__cause__ is err


# 5-6. Eccezioni non-transient → raise immediato, 0 retry


def test_value_error_raises_immediately_no_retry(executor_with_mocks):
    """ValueError (tipicamente: canvas corrotto in
    update_project_canvas_key_atomic) → non retryare."""
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    project_service.update_project_columns_metadata.side_effect = ValueError(
        "Canvas corrotto per progetto X"
    )

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("proj-x", canvas, res)

    # UNA sola chiamata: no retry
    assert project_service.update_project_columns_metadata.call_count == 1
    assert exc_info.value.attempts == 1
    assert "ValueError" in exc_info.value.reason
    assert "Canvas corrotto" in exc_info.value.reason


def test_type_error_raises_immediately_no_retry(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    project_service.update_project_columns_metadata.side_effect = TypeError(
        "unexpected kwarg"
    )

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError):
        executor._salva_tipi_colonne_da_canvas("proj-y", canvas, res)

    assert project_service.update_project_columns_metadata.call_count == 1


def test_attribute_error_raises_immediately_no_retry(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    project_service.update_project_columns_metadata.side_effect = AttributeError(
        "missing method"
    )

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError):
        executor._salva_tipi_colonne_da_canvas("proj-z", canvas, res)

    assert project_service.update_project_columns_metadata.call_count == 1


# 7. Messaggio errore esplicito per l'utente


def test_error_message_is_explicit_about_recovery(executor_with_mocks):
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    import psycopg2
    project_service.update_project_columns_metadata.side_effect = \
        psycopg2.OperationalError("broken")

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("my-project", canvas, res)

    msg = str(exc_info.value)
    # Deve contenere i 3 fatti chiave che servono all'utente:
    assert "my-project" in msg  # quale progetto
    assert "blob" in msg.lower() or "gia' salvato" in msg.lower() or "scaricato" in msg.lower()  # risultato persistito
    assert "rilanci" in msg.lower() or "recovery" in msg.lower()  # come recuperare
    assert "downstream" in msg.lower() or "lineage" in msg.lower() or "tipi" in msg.lower()  # cosa non e' allineato


# 8. Se la 1° chiamata NON solleva, zero sleep (no backoff inutile)


def test_happy_path_does_not_sleep(executor_with_mocks, monkeypatch):
    """Il retry introdotto non deve rallentare il caso comune."""
    executor, project_service = executor_with_mocks
    canvas = _make_canvas()
    res = _make_res()

    sleep_calls = []
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper._time, "sleep", lambda s: sleep_calls.append(s))

    executor._salva_tipi_colonne_da_canvas("proj-1", canvas, res)

    assert sleep_calls == [], f"Happy path non deve chiamare sleep: {sleep_calls}"


# 9. Backoff rispettato tra retry


def test_backoff_applied_between_retries(executor_with_mocks, monkeypatch):
    executor, project_service = executor_with_mocks
    # Ripristina i backoff reali (la fixture li accelera a 0.01s)
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.2, 0.5, 1.0))
    monkeypatch.setattr(_cm_helper, "_MAX_ATTEMPTS", 4)

    canvas = _make_canvas()
    res = _make_res()

    import psycopg2
    err = psycopg2.OperationalError("x")
    project_service.update_project_columns_metadata.side_effect = [err, err, err, err]

    sleep_calls = []
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper._time, "sleep", lambda s: sleep_calls.append(s))

    from app.shared.exceptions import ColumnsMetadataSaveError
    with pytest.raises(ColumnsMetadataSaveError):
        executor._salva_tipi_colonne_da_canvas("p1", canvas, res)

    # 4 tentativi, 3 sleep tra tentativi (dopo 1°, 2°, 3°; nessun sleep dopo 4°).
    # Tutti i valori del backoff array devono essere usati — invariante
    # che bloccava il baco precedente (array len=3 ma max_attempts=3 →
    # l'ultimo valore (1.0) non veniva mai usato).
    assert sleep_calls == [0.2, 0.5, 1.0]


# FULL-STACK CONTRACT — service reale, patch sul DB layer
#
# I test precedenti mockavano direttamente `update_project_columns_metadata`:
# scavalcavano completamente il service layer. Se il service avesse
# catturato silenziosamente un'eccezione (come faceva prima del fix), i
# test NON l'avrebbero mai beccato.
#
# Questi test patchano lo strato piu' basso (`update_project_canvas_key_atomic`)
# e verificano che l'intero stack propaghi correttamente:
#     executor → project_service → config_db_service
# FIXME: scenario bloccato:
#   project_service.update_project_columns_metadata cattura tutte le
#   eccezioni → ritorna False → executor non fa retry → scrive
#   res["columns_metadata"] comunque → falso positivo.


def test_fullstack_transient_db_error_triggers_retry(monkeypatch):
    """Service reale + DB layer mockato: OperationalError al 1° tentativo,
    OK al 2°. Verifica retry + salvataggio res dopo recovery."""
    from app.core.estrai.executor import EstraiExecutor
    from app.services.project_service import ProjectService
    from app.db.config import projects as projects_leaf
    import psycopg2

    call_count = {"n": 0}

    def flaky_atomic(project_id, key, value):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise psycopg2.OperationalError("pool exhausted (1/2)")
        return True

    # BE-GO-READY-DB-ENGINE-007: project_service imports the
    # atomic helper from `app.db.config.projects` directly; patch
    # the leaf symbol to intercept the production retry path.
    monkeypatch.setattr(projects_leaf, "update_project_canvas_key_atomic", flaky_atomic)

    # Accelera i backoff
    executor = EstraiExecutor(
        project_service=ProjectService(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=MagicMock(),
    )
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    canvas = _make_canvas()
    res = _make_res()

    executor._salva_tipi_colonne_da_canvas("p1", canvas, res)

    assert call_count["n"] == 2, "Retry non partito — service swallowed exception?"
    assert "columns_metadata" in res, "Metadata non salvati dopo retry succeed"


def test_fullstack_transient_db_error_all_fail_raises_explicit(monkeypatch):
    """Service reale + DB layer mockato: tutti i 4 tentativi falliscono con
    OperationalError. Deve propagare `ColumnsMetadataSaveError` con
    attempts=4 e messaggio esplicito (blob persistito + rilanciare task).

    Questo e' il test che "prende" il bug del silenziamento: se il
    service catturasse l'eccezione e ritornasse False, il loop nell'
    executor non riceverebbe MAI un'eccezione transient e il task
    risulterebbe SUCCESS con metadata stale.
    """
    from app.core.estrai.executor import EstraiExecutor
    from app.services.project_service import ProjectService
    from app.shared.exceptions import ColumnsMetadataSaveError
    import app.db.config_db_service as cfg_db
    import psycopg2

    def always_fail(project_id, key, value):
        raise psycopg2.OperationalError("pool persistently down")

    monkeypatch.setattr(cfg_db, "update_project_canvas_key_atomic", always_fail)

    executor = EstraiExecutor(
        project_service=ProjectService(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=MagicMock(),
    )
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    canvas = _make_canvas()
    res = _make_res()

    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("p-fullstack", canvas, res)

    caught = exc_info.value
    assert caught.project_id == "p-fullstack"
    # Tutti 4 i tentativi (coerente col backoff che ora ha 3 valori)
    assert caught.attempts == 4
    assert "OperationalError" in caught.reason
    # Il messaggio DEVE dire che il blob e' gia' persistito
    assert caught.result_already_persisted is True
    # res NON deve contenere columns_metadata (niente falso positivo)
    assert "columns_metadata" not in res, (
        "res['columns_metadata'] scritto nonostante il save sia fallito — "
        "regressione al comportamento silent-fallback"
    )


def test_fullstack_project_deleted_during_run_raises(monkeypatch):
    """Edge case: progetto eliminato durante l'esecuzione →
    `update_project_canvas_key_atomic` ritorna False → l'executor deve
    sollevare `ColumnsMetadataSaveError`, NON scrivere res."""
    from app.core.estrai.executor import EstraiExecutor
    from app.services.project_service import ProjectService
    from app.shared.exceptions import ColumnsMetadataSaveError
    from app.db.config import projects as projects_leaf

    monkeypatch.setattr(
        projects_leaf, "update_project_canvas_key_atomic",
        lambda project_id, key, value: False,  # progetto inesistente
    )

    executor = EstraiExecutor(
        project_service=ProjectService(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=MagicMock(),
    )
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    canvas = _make_canvas()
    res = _make_res()

    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("p-ghost", canvas, res)

    # Nessun retry: progetto inesistente non e' un transient
    assert exc_info.value.attempts == 1
    assert "non trovato" in exc_info.value.reason.lower()
    assert "columns_metadata" not in res


def test_fullstack_non_transient_error_no_retry(monkeypatch):
    """Service reale + DB layer mockato: ValueError (canvas corrotto) →
    raise al 1° tentativo senza retry."""
    from app.core.estrai.executor import EstraiExecutor
    from app.services.project_service import ProjectService
    from app.shared.exceptions import ColumnsMetadataSaveError
    from app.db.config import projects as projects_leaf

    call_count = {"n": 0}

    def corrupted(project_id, key, value):
        call_count["n"] += 1
        raise ValueError(f"Canvas corrotto per progetto {project_id}")

    monkeypatch.setattr(projects_leaf, "update_project_canvas_key_atomic", corrupted)

    executor = EstraiExecutor(
        project_service=ProjectService(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=MagicMock(),
    )
    import app.shared.columns_metadata as _cm_helper
    monkeypatch.setattr(_cm_helper, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    canvas = _make_canvas()
    res = _make_res()

    with pytest.raises(ColumnsMetadataSaveError) as exc_info:
        executor._salva_tipi_colonne_da_canvas("p-corrupt", canvas, res)

    assert call_count["n"] == 1, "Retry partito su errore non-transient"
    assert exc_info.value.attempts == 1
    assert "ValueError" in exc_info.value.reason
