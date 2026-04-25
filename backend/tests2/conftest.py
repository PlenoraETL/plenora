import os
from pathlib import Path
import sys

import pytest


def _ensure_backend_on_path() -> None:
    here = Path(__file__).resolve()
    backend = here.parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


_ensure_backend_on_path()

# A1: i test non usano un bucket reale. Di default la test session vive in
# STORAGE_POLICY=db (= on-prem single-node), cosi' `app.main` boota senza
# richiedere BACKUP_BUCKET. I test che esercitano il dispatch S3 impostano
# esplicitamente STORAGE_POLICY via monkeypatch.
os.environ.setdefault("STORAGE_POLICY", "db")


# ---------------------------------------------------------------------------
# DB-init isolation a session bootstrap (Phase 3 Batch B closeout, 3)
# ---------------------------------------------------------------------------
#
# Problema: `app/main.py:51` chiama `init_all_dbs()` AL TOP-LEVEL del
# modulo. Qualsiasi test (es. `test_c8_observability_contract.py`) che fa
# `import app.main` triggera la catena:
#   init_all_dbs -> pg_connection.init_db -> psycopg2.connect
#   -> idb.init_db -> psycopg2.connect
#   -> migrations_runner.apply_pending_migrations -> psycopg2.connect
# In unit-test env senza DB raggiungibile, psycopg2 puo' produrre un crash
# nativo Windows (access violation) prima di qualsiasi catch Python.
# Vedi report Batch B review: `test_c8_observability_contract.py`.
#
# Fix: stub a no-op delle 3 init function PRIMA che `app.main` venga
# importato da qualsiasi test. Necessario eseguirlo a livello modulo
# conftest (collect time), non in fixture autouse, perche' `app.main`
# fa l'init al top-level del proprio modulo: una volta importato finisce
# in `sys.modules` e non viene rieseguito.
#
# Compatibility:
#   - Test integration che richiedono init DB reale possono settare
#     `URBY_TEST_REAL_DB_INIT=1` come opt-out env (escape hatch). Nessun
#     test attualmente lo richiede (gli integration testano direttamente
#     `pg_connection.execute` senza passare da `app.main`).
#   - Runtime non e' toccato: lo stub vive nel modulo conftest, caricato
#     solo da pytest. `python -c "import app.main"` fuori dai test si
#     comporta come prima.
#   - Non interferisce con monkeypatch successivi: lo stub e' una
#     sostituzione one-shot al modulo, non un fixture; un test che vuole
#     reinstallare l'init reale puo' importare e chiamare la versione
#     originale (stashata sotto `_original_init_all_dbs`).

if os.getenv("URBY_TEST_REAL_DB_INIT") != "1":
    try:
        import app.db.db_service as _db_service
        import app.db.pg_connection as _pg_conn
        import app.core.intermediate.db as _idb
        import app.db.migrations_runner as _mig_runner

        # Stash gli originali per chi voglia reinstallarli (test integration
        # opt-in via `_db_service._original_init_all_dbs()`).
        _db_service._original_init_all_dbs = _db_service.init_all_dbs
        _pg_conn._original_init_db = _pg_conn.init_db
        _idb._original_init_db = _idb.init_db
        _mig_runner._original_apply_pending_migrations = (
            _mig_runner.apply_pending_migrations
        )

        def _noop_init_all_dbs():
            return None

        def _noop_init_db():
            return None

        def _noop_apply_pending_migrations():
            return []

        _db_service.init_all_dbs = _noop_init_all_dbs
        _pg_conn.init_db = _noop_init_db
        _idb.init_db = _noop_init_db
        _mig_runner.apply_pending_migrations = _noop_apply_pending_migrations
    except Exception:
        # Se l'import fallisce a session start, lasciamo che siano i
        # singoli test a fallire col loro errore naturale. Il bootstrap
        # non deve mascherare bug di import del codice applicativo.
        pass


def _kill_stray_test_subprocesses() -> None:
    """Kill stray celery/uvicorn spawnati da test runs precedenti.

    Quando pytest viene ucciso da --timeout o Ctrl+C a meta' test, i
    subprocess figli (celery worker, uvicorn backend) spesso sopravvivono
    perche' la teardown del fixture module-scoped non gira. Il celery stray
    si riattacca al Redis condiviso e ruba i task del nuovo worker di test
    — DuplicateNodenameWarning — causando flake indistinguibili dai bug
    applicativi.

    Heuristica conservativa: killa solo processi il cui command-line
    matcha ESATTAMENTE quello che spawniamo nei test (celery/uvicorn con
    app specifica). Non tocca dev server su porte arbitrarie."""
    import sys as _sys
    if _sys.platform != "win32":
        return
    import subprocess as _sp
    try:
        out = _sp.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return

    patterns = (
        "celery -A workers.celery_app worker",
        "uvicorn app.main:app",
        "uvicorn tests2.functional.fakes",
    )
    to_kill: list[str] = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        cmdline = parts[1] if len(parts) >= 3 else ""
        pid = parts[-1].strip()
        if not pid.isdigit():
            continue
        if any(p in cmdline for p in patterns):
            to_kill.append(pid)
    for pid in to_kill:
        try:
            _sp.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def _cleanup_stray_test_subprocesses_at_session_start():
    """Pulisce stray prima della session, non tocca al teardown (gli altri
    fixture module-scoped hanno i loro stop_process)."""
    _kill_stray_test_subprocesses()
    yield


@pytest.fixture
def backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Audit-action isolation per test unit/contract (Phase 3 Batch B closeout)
# ---------------------------------------------------------------------------
#
# Problema: dopo Batch B, ~26 endpoint hanno `@audit_action(...)`. Sui test
# che chiamano gli endpoint via FastAPI TestClient (o invocano la view
# function direttamente con kwarg `current_user` mockato), il decorator
# tenta una INSERT su `plenora.audit_actions` via `psycopg2.connect`.
# `audit_action` e' fail-open su `Exception` Python — ma una Windows
# fatal access violation in psycopg2 (DB unreachable in unit-test env)
# crasha il processo prima del catch (vedi report Batch B review:
# `test_api_connection_executable_rejected_contract.py`).
#
# Fix: fixture autouse che stub-a `_execute_write` a no-op per tutti i
# test che NON sono marcati `integration`. Gli integration hanno DB live
# disponibile (vedi `tests2/integration/conftest.py`) e devono esercitare
# la write reale per validare lo schema append-only.
#
# Compatibility:
#   - Test che richiedono fixture `audit_writes` / `audit_write_raises`
#     (definite in `tests2/security/unit/test_audit_action_decorator.py`)
#     fanno il loro `monkeypatch.setattr(_execute_write, ...)` DOPO questa
#     fixture autouse — l'ultimo monkeypatch vince, quindi gli spy/raise
#     pattern continuano a funzionare invariati.
#   - Runtime non e' toccato: la fixture vive solo nello scope test.
#   - I test `unit` / `contract` / `security` / `functional` (qualsiasi
#     marker che non sia `integration`) ottengono lo stub silenzioso.

@pytest.fixture(autouse=True)
def _stub_audit_action_write(request, monkeypatch):
    """Stub `audit_action._execute_write` a no-op nei test non integration.

    Evita crash psycopg2 / write spurie su `plenora.audit_actions` quando
    i test esercitano endpoint decorati senza un DB applicativo disponibile.
    """
    if request.node.get_closest_marker("integration") is not None:
        # Integration: lascia che la write reale colpisca il DB di test
        # (l'env integration ha PG disponibile e schema migrato).
        yield
        return

    try:
        from app.shared import audit_action as _audit_mod
    except Exception:
        # Se l'import fallisce, lasciamo che il test fallisca col suo
        # errore naturale invece di nascondere il problema con un yield muto.
        yield
        return

    def _noop_execute_write(_sql, _params):
        return None

    monkeypatch.setattr(_audit_mod, "_execute_write", _noop_execute_write)
    yield


# ---------------------------------------------------------------------------
# DB-logger isolation per test unit/contract (Phase 3 Batch B closeout, 2)
# ---------------------------------------------------------------------------
#
# Problema simmetrico ad audit_action: `PostgresLogHandler.emit` (definito
# in `app/utils/logger.py:323`) e' attaccato ai logger applicativi e fa
# `pg_connection.execute(INSERT INTO logs ...)` per ogni record di livello
# WARNING+. Quando un test unit/contract triggera un `logger.error(...)`
# (es. `[CRASH]` su un router decorato chiamato via TestClient), l'handler
# tenta `psycopg2.connect` su un DATABASE_URL non disponibile in unit-env.
# L'eccezione e' catturata da `self.handleError` (vedi logger.py:393-394)
# MA un'access violation nativa Windows in psycopg2 puo' crashare il
# processo prima di arrivare al `try`. Vedi report Batch B review:
# `test_api_connector_source_loading_core.py`.
#
# Fix: stub `app.db.pg_connection.execute` a no-op nei test non-integration
# (preferenza esplicita del reviewer Batch B). Pattern simmetrico a
# `_stub_audit_action_write`. Compatibility:
#   - Test che monkeypatchano `app.db.pg_connection.execute` con spy
#     (es. `test_bulk_log_aggregation_contract.py::pg_execute_mock`,
#     `test_log_service_db_contract.py::_install_fake_pg`,
#     `test_router_breadcrumb_*`) sovrascrivono il no-op autouse per
#     LIFO — pytest applica le fixture richieste DOPO le autouse, e
#     il monkeypatch.setattr piu' recente vince.
#   - Runtime non e' toccato: la fixture vive solo nello scope test.
#   - Test integration mantengono la write reale verso il DB di test.

# Fixture-marker che indicano "voglio DB live esplicito" anche se il test
# non porta marker `integration`. La fixture autouse sotto skippa lo stub
# quando una di queste e' richiesta dal test, cosi' i setup che fanno
# INSERT/DELETE reali su plenora.users / groups / user_mfa restano
# funzionanti (es. `mfa_user` in `test_mfa_totp_contract.py`). Estendere
# qui se in futuro nascono altre fixture analoghe.
_PG_LIVE_FIXTURES = frozenset({"mfa_user"})


@pytest.fixture(autouse=True)
def _stub_pg_connection_execute(request, monkeypatch):
    """Stub `pg_connection.execute` a no-op nei test non integration.

    Evita crash psycopg2 quando il logger applicativo (o codice raggiunto
    indirettamente) prova INSERT/UPDATE su `plenora.logs` / `log_runs` /
    `audit_actions` da un test unit senza DB disponibile.

    Opt-out per:
      - test marcati `integration`,
      - test che richiedono fixture in `_PG_LIVE_FIXTURES` (DB live esplicito).

    Test che monkeypatchano `pg_connection.execute` con spy
    (`pg_execute_mock`, `_install_fake_pg`, ecc.) sovrascrivono il no-op
    autouse per LIFO — pytest applica le fixture richieste DOPO le autouse,
    e l'ultimo monkeypatch.setattr vince.
    """
    if request.node.get_closest_marker("integration") is not None:
        yield
        return

    if _PG_LIVE_FIXTURES & set(request.fixturenames):
        yield
        return

    try:
        import app.db.pg_connection as _pg_mod
    except Exception:
        yield
        return

    def _noop_execute(*_args, **_kwargs):
        return None

    monkeypatch.setattr(_pg_mod, "execute", _noop_execute, raising=False)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


def _iter_managed_processes(obj):
    if obj is None:
        return
    if hasattr(obj, "process") and hasattr(obj, "name"):
        yield obj
        return
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_managed_processes(value)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            yield from _iter_managed_processes(value)


@pytest.fixture(autouse=True)
def _dump_managed_process_output_on_failure(request):
    yield
    rep = getattr(request.node, "rep_call", None)
    if not rep or not rep.failed:
        return

    seen = set()
    for value in getattr(request.node, "funcargs", {}).values():
        for managed in _iter_managed_processes(value):
            pid = id(managed)
            if pid in seen:
                continue
            seen.add(pid)
            captured = "".join(getattr(managed, "captured_output", [])[-400:])
            if not captured.strip():
                continue
            print(
                f"\n===== captured output: {getattr(managed, 'name', 'process')} =====\n"
                f"{captured}"
                f"\n===== end captured output: {getattr(managed, 'name', 'process')} =====\n"
            )
