"""
Esito del «Test connessione» (Fase 5, voce S3): enumerazione delle route e
regola unica di registrazione (app/shared/connection_tests/results.py).

Regola: ogni POST ``.../test`` dell'app registra l'esito con la stessa
funzione (decoratore ``records_connection_test``) o e' un'eccezione
dichiarata qui sotto col motivo; per ogni tipo, ogni PUT/PATCH/DELETE della
stessa risorsa azzera l'esito e la lista lo allega a ogni elemento.
"""

from __future__ import annotations

import inspect
import typing
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel

from app.shared.connection_tests import results
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.connection_test_status import ConnectionTestStatus

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# POST .../test che NON registrano un esito, col motivo.
DECLARED_UNRECORDED = {
    "/api-connections/test": (
        "testa una configurazione non salvata (wizard di creazione): non c'e' "
        "una connessione a cui legare l'esito"
    ),
}

_WRITE_METHODS = {"PUT", "PATCH", "DELETE"}


class _FakeStore:
    def __init__(self):
        self.records: list[results.ConnectionTestRecord] = []
        self.resets: list[tuple[str, str]] = []
        self.rows: list[dict] = []
        self.fail_with: Exception | None = None

    def record(self, entry):
        if self.fail_with:
            raise self.fail_with
        self.records.append(entry)

    def reset(self, kind, resource_id, at):
        if self.fail_with:
            raise self.fail_with
        self.resets.append((kind, resource_id))

    def load(self, kind, resource_ids):
        if self.fail_with:
            raise self.fail_with
        return [r for r in self.rows if r["resource_id"] in resource_ids]


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(results, "_store", fake)
    return fake


class _Route(typing.NamedTuple):
    path: str
    methods: frozenset
    endpoint: typing.Any
    response_model: typing.Any


@pytest.fixture(scope="module")
def routes() -> list[_Route]:
    """Tutte le route HTTP sotto /api (il router che l'app monta con prefisso
    /api, app_factory), anche quelle dei router inclusi. Si legge il router
    e non app.main: niente avvio dell'app (test_architecture_legacy_patterns)."""
    from app.core.api import api_router

    out = []
    for ctx in iter_route_contexts(api_router.routes):
        if not ctx.path or not ctx.methods or ctx.endpoint is None:
            continue
        out.append(_Route(ctx.path, frozenset(ctx.methods), ctx.endpoint, getattr(ctx, "response_model", None)))
    return out


def _test_routes(routes):
    return [r for r in routes if "POST" in r.methods and r.path.rsplit("/", 1)[-1] == "test"]


def _path_params(path: str) -> set[str]:
    return {seg[1:-1] for seg in path.split("/") if seg.startswith("{") and seg.endswith("}")}


def _user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="mario", group_id="g-1", role="user")


def _no_secrets(resource_id: str) -> dict:
    return {}


# Enumerazione delle route


def test_every_test_endpoint_records_or_is_declared(routes):
    found = _test_routes(routes)
    assert len(routes) > 100, "enumerazione delle route vuota: il test non vede i router inclusi"
    assert found, "nessun endpoint di test trovato: il test non enumera niente"
    missing = [
        r.path
        for r in found
        if not hasattr(r.endpoint, results.RECORDS_ATTR) and r.path not in DECLARED_UNRECORDED
    ]
    assert missing == [], (
        "Endpoint di test che non registrano l'esito: "
        f"{missing}. Decorare con records_connection_test o dichiarare il motivo."
    )
    paths = {r.path for r in found}
    stale = sorted(set(DECLARED_UNRECORDED) - paths)
    assert stale == [], f"Eccezioni dichiarate senza piu' la route: {stale}"
    declared_but_recorded = [
        r.path for r in found if r.path in DECLARED_UNRECORDED and hasattr(r.endpoint, results.RECORDS_ATTR)
    ]
    assert declared_but_recorded == []


def test_every_kind_has_exactly_its_test_endpoint(routes):
    kinds = sorted(
        getattr(r.endpoint, results.RECORDS_ATTR)[0]
        for r in _test_routes(routes)
        if hasattr(r.endpoint, results.RECORDS_ATTR)
    )
    assert kinds == sorted(results.CONNECTION_TEST_KINDS)


def test_id_param_is_a_path_param(routes):
    for r in routes:
        for attr in (results.RECORDS_ATTR, results.RESETS_ATTR):
            marker = getattr(r.endpoint, attr, None)
            if marker:
                kind, id_param = marker
                assert id_param in _path_params(r.path), (r.path, attr, id_param)


def test_writes_of_the_resource_reset_and_list_attaches(routes):
    for test_route in _test_routes(routes):
        marker = getattr(test_route.endpoint, results.RECORDS_ATTR, None)
        if not marker:
            continue
        kind, _ = marker
        resource_path = test_route.path[: -len("/test")]
        writes = [r for r in routes if r.path == resource_path and r.methods & _WRITE_METHODS]
        assert {m for r in writes for m in r.methods} >= {"PUT", "DELETE"}, (kind, resource_path)
        for r in writes:
            reset = getattr(r.endpoint, results.RESETS_ATTR, None)
            assert reset and reset[0] == kind, (
                f"{sorted(r.methods)} {r.path} non azzera l'esito del test ({kind})"
            )
        collection_path = resource_path.rsplit("/", 1)[0]
        lists = [r for r in routes if r.path == collection_path and "GET" in r.methods]
        assert len(lists) == 1, (kind, collection_path)
        assert getattr(lists[0].endpoint, results.ATTACHES_ATTR, None) == kind, (
            f"GET {collection_path} non allega last_test ({kind})"
        )
        model = lists[0].response_model
        if model is not None:
            item_models = [
                arg
                for field in model.model_fields.values()
                for arg in typing.get_args(field.annotation)
                if inspect.isclass(arg) and issubclass(arg, BaseModel)
            ]
            assert item_models, (kind, model)
            for item in item_models:
                assert "last_test" in item.model_fields, (
                    f"{item.__name__} non dichiara last_test: la risposta lo scarterebbe"
                )


def test_resets_only_on_resource_writes(routes):
    """Il marcatore di azzeramento sta solo su PUT/PATCH/DELETE."""
    for r in routes:
        if hasattr(r.endpoint, results.RESETS_ATTR):
            assert r.methods <= _WRITE_METHODS, (r.path, r.methods)


# Regola di registrazione


def test_record_from_model_result(store):
    class Result(BaseModel):
        success: bool
        message: str

    @results.records_connection_test("connection", id_param="connection_id", secrets_of=_no_secrets)
    def route(connection_id: str, current_user: CurrentUser):
        return Result(success=True, message="Connessione PostgreSQL riuscita")

    out = route(connection_id="c-1", current_user=_user())
    assert out.success is True
    (entry,) = store.records
    assert (entry.kind, entry.resource_id, entry.success) == ("connection", "c-1", True)
    assert entry.message == "Connessione PostgreSQL riuscita"
    assert (entry.tested_by, entry.tested_by_name) == ("u-1", "mario")
    assert entry.tested_at.tzinfo is not None and entry.tested_at.utcoffset() == timedelta(0)
    assert entry.duration_ms >= 0


def test_record_from_dict_result_without_secrets(store):
    @results.records_connection_test("api_connection", id_param="api_id", secrets_of=_no_secrets)
    def route(api_id: str, current_user: CurrentUser):
        return {
            "success": False,
            "error": "Errore di connessione: https://api.x.it/v1?token=SEGRETO password=abc " + "x" * 600,
        }

    route(api_id="a-1", current_user=_user())
    (entry,) = store.records
    assert entry.success is False
    assert "SEGRETO" not in entry.message and "abc" not in entry.message
    assert len(entry.message) <= results.MESSAGE_MAX_LEN


def test_record_without_message_uses_fallback(store):
    @results.records_connection_test("api_connection", id_param="api_id", secrets_of=_no_secrets)
    def route(api_id: str, current_user: CurrentUser):
        return {"success": True, "status_code": 200}

    route(api_id="a-1", current_user=_user())
    assert store.records[0].message == "Connessione riuscita (HTTP 200)"


def test_refused_test_is_not_recorded(store):
    @results.records_connection_test("datasource", id_param="ds_id", secrets_of=_no_secrets)
    def route(ds_id: str, current_user: CurrentUser):
        raise HTTPException(status_code=404, detail="Data Source non trovato")

    with pytest.raises(HTTPException):
        route(ds_id="d-1", current_user=_user())
    assert store.records == []


def test_api_overrides_are_not_recorded(store):
    from app.core.arricchisci import router as arricchisci_router

    when = arricchisci_router._tests_saved_api_configuration
    assert when({"body": None}) is True
    assert when({"body": {"test_params": {}}}) is True
    assert when({"body": {"config_overrides": {"url": "https://x"}}}) is False


def test_reset_after_successful_write_only(store):
    @results.resets_connection_test("arcgis_connection", id_param="connection_id")
    def ok(connection_id: str):
        return "ok"

    @results.resets_connection_test("arcgis_connection", id_param="connection_id")
    def refused(connection_id: str):
        raise HTTPException(status_code=403, detail="no")

    ok(connection_id="g-1")
    with pytest.raises(HTTPException):
        refused(connection_id="g-2")
    assert store.resets == [("arcgis_connection", "g-1")]


def test_store_errors_never_break_the_route(store):
    store.fail_with = psycopg.OperationalError("pool")

    @results.records_connection_test("connection", id_param="connection_id", secrets_of=_no_secrets)
    def route(connection_id: str, current_user: CurrentUser):
        return {"success": True, "message": "ok"}

    @results.resets_connection_test("connection", id_param="connection_id")
    def write(connection_id: str):
        return None

    assert route(connection_id="c-1", current_user=_user()) == {"success": True, "message": "ok"}
    write(connection_id="c-1")
    assert results.last_connection_tests("connection", ["c-1"], viewer=_user()) == {}


def test_attach_marks_stale_after_declared_age(store):
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    store.rows = [
        {"resource_id": "fresh", "success": True, "message": "ok",
         "tested_at": now - timedelta(days=1), "tested_by_name": "mario",
         "tested_by_group": "g-1", "duration_ms": 120},
        {"resource_id": "old", "success": False, "message": "Timeout",
         "tested_at": now - results.STALE_AFTER - timedelta(minutes=1),
         "tested_by_name": None, "duration_ms": 5000},
    ]
    statuses = results.last_connection_tests(
        "connection", ["fresh", "old", "never"], viewer=_user(), now=now,
    )
    assert statuses["fresh"].stale is False and statuses["fresh"].tested_by == "mario"
    assert statuses["old"].stale is True and statuses["old"].success is False
    assert "never" not in statuses


def test_attach_writes_models_and_dicts(store):
    store.rows = [
        {"resource_id": "a", "success": True, "message": "ok",
         "tested_at": datetime.now(timezone.utc), "tested_by_name": "mario", "duration_ms": 3},
    ]

    class Item(BaseModel):
        id: str
        last_test: ConnectionTestStatus | None = None

    models = [Item(id="a"), Item(id="b")]
    results.attach_last_tests("connection", models, viewer=_user())
    assert models[0].last_test.success is True and models[1].last_test is None

    dicts = [{"id": "a"}, {"id": "b"}]
    results.attach_last_tests("api_connection", dicts, viewer=_user())
    assert dicts[0]["last_test"]["success"] is True
    assert dicts[0]["last_test"]["tested_at"].endswith(("Z", "+00:00"))
    assert dicts[1]["last_test"] is None


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        results.records_connection_test("ftp_server", id_param="x", secrets_of=_no_secrets)


def test_real_route_records_with_user(store, monkeypatch):
    """La route del test delle connessioni database registra con la funzione unica."""
    from app.core.connections import router as connections_router
    from app.core.connections.models import ConnectionTestResult

    class _Service:
        def get_connection(self, connection_id):
            return object()

        def test_connection(self, connection_id):
            return ConnectionTestResult(success=False, message="Errore connessione PostgreSQL")

    monkeypatch.setattr(connections_router, "_connection_service", _Service())
    monkeypatch.setattr(connections_router, "assert_resource_visible", lambda *a, **k: None)
    connections_router.test_connection(connection_id="c-9", current_user=_user())
    (entry,) = store.records
    assert (entry.kind, entry.resource_id, entry.success, entry.tested_by_name) == (
        "connection", "c-9", False, "mario",
    )


# Chi vede il nome di chi ha testato (regola di users_router.list_users)


def _tested_row(group):
    return {"resource_id": "c-1", "success": True, "message": "ok",
            "tested_at": datetime.now(timezone.utc), "tested_by_name": "mario",
            "tested_by_group": group, "duration_ms": 3}


@pytest.mark.parametrize(
    ("viewer", "tester_group", "expected"),
    [
        (CurrentUser(user_id="u-2", username="anna", group_id="g-1", role="user"), "g-1", "mario"),
        (CurrentUser(user_id="u-3", username="admin", group_id="g-1", role="admin"), "g-1", "mario"),
        (CurrentUser(user_id="u-4", username="luca", group_id="g-2", role="user"), "g-1", None),
        (CurrentUser(user_id="u-5", username="adm2", group_id="g-2", role="admin"), "g-1", None),
        (CurrentUser(user_id="u-6", username="root", group_id="g-9", role="superadmin"), "g-1", "mario"),
        # Utente che ha testato cancellato: gruppo sconosciuto.
        (CurrentUser(user_id="u-2", username="anna", group_id="g-1", role="user"), None, None),
        (CurrentUser(user_id="u-6", username="root", group_id="g-9", role="superadmin"), None, "mario"),
        (None, "g-1", None),
    ],
)
def test_tester_name_only_for_same_group_or_superadmin(store, viewer, tester_group, expected):
    store.rows = [_tested_row(tester_group)]
    statuses = results.last_connection_tests("connection", ["c-1"], viewer=viewer)
    assert statuses["c-1"].tested_by == expected
    items = [{"id": "c-1"}]
    results.attach_last_tests("connection", items, viewer=viewer)
    assert items[0]["last_test"]["tested_by"] == expected


def test_list_route_passes_the_requesting_user(store):
    """Il decoratore della lista usa ``current_user`` della richiesta."""
    store.rows = [_tested_row("g-1")]

    @results.attaches_connection_tests("connection", items_field="connections")
    def route(current_user: CurrentUser):
        return {"connections": [{"id": "c-1"}]}

    same = route(current_user=_user())
    other = route(current_user=CurrentUser(user_id="x", username="x", group_id="g-2", role="admin"))
    assert same["connections"][0]["last_test"]["tested_by"] == "mario"
    assert other["connections"][0]["last_test"]["tested_by"] is None


# Errori dello store: fail-open solo per la disponibilita'


_UNAVAILABLE = [
    psycopg.OperationalError("pool timeout"),
    psycopg.InterfaceError("connection closed"),
    psycopg.errors.UndefinedTable("relation connection_test_results does not exist"),
    RuntimeError("DATABASE_URL non configurato. Imposta la variabile d'ambiente."),
]
_PROGRAMMING = [
    psycopg.errors.SyntaxError("syntax error at or near"),
    psycopg.errors.UndefinedColumn("column tested_by_grup does not exist"),
    psycopg.ProgrammingError("can't adapt type"),
    psycopg.errors.DatatypeMismatch("argument of WHERE must be type boolean"),
    RuntimeError("UnitOfWork is already active"),
]


def _store_calls():
    @results.records_connection_test("connection", id_param="connection_id", secrets_of=_no_secrets)
    def test_route(connection_id: str, current_user: CurrentUser):
        return {"success": True, "message": "ok"}

    @results.resets_connection_test("connection", id_param="connection_id")
    def write(connection_id: str):
        return None

    return {
        "record": lambda: test_route(connection_id="c-1", current_user=_user()),
        "reset": lambda: write(connection_id="c-1"),
        "load": lambda: results.last_connection_tests("connection", ["c-1"], viewer=_user()),
    }


def _capture_errors(monkeypatch) -> list[tuple[str, object]]:
    logged: list[tuple[str, object]] = []
    monkeypatch.setattr(
        results.logger, "error",
        lambda msg, *args, **kwargs: logged.append((msg % args, kwargs.get("exc_info"))),
    )
    return logged


# Qualunque errore, non solo quelli del DB: anche il sanitizer o un bug.
_ANY_ERROR = _UNAVAILABLE + _PROGRAMMING + [ValueError("bad"), TypeError("bad"), KeyError("bad")]


def test_after_effect_ops_are_the_writes():
    assert set(results.AFTER_EFFECT_OPS) == {"record", "reset"}
    assert set(_store_calls()) == set(results.AFTER_EFFECT_OPS) | {"load"}


@pytest.mark.parametrize("error", _ANY_ERROR, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("op", results.AFTER_EFFECT_OPS)
def test_writes_after_the_effect_never_reach_the_user(store, error, op, monkeypatch):
    """Registrazione e azzeramento seguono un effetto compiuto (test eseguito,
    connessione modificata/eliminata): qualunque errore e' loggato ERROR con
    il tipo e lo stack e la route risponde come senza registro."""
    logged = _capture_errors(monkeypatch)
    store.fail_with = error
    _store_calls()[op]()
    ((line, exc_info),) = [entry for entry in logged if f"connection_test_{op}_error" in entry[0]]
    assert type(error).__name__ in line
    assert exc_info is error  # stack nel log


def test_any_error_after_the_test_is_not_propagated(store, monkeypatch):
    """Anche ``when``, la lettura dei segreti e il calcolo del messaggio
    stanno dopo il test: la risposta della route arriva comunque."""
    logged = _capture_errors(monkeypatch)

    def broken_secrets(resource_id):
        raise ZeroDivisionError("bug")

    @results.records_connection_test("connection", id_param="connection_id", secrets_of=broken_secrets)
    def route(connection_id: str, current_user: CurrentUser):
        return {"success": True, "message": "ok"}

    @results.records_connection_test(
        "connection", id_param="connection_id", secrets_of=_no_secrets, when=lambda kw: 1 / 0,
    )
    def route_when(connection_id: str, current_user: CurrentUser):
        return {"success": True, "message": "ok"}

    assert route(connection_id="c-1", current_user=_user()) == {"success": True, "message": "ok"}
    assert route_when(connection_id="c-1", current_user=_user()) == {"success": True, "message": "ok"}
    assert len([line for line, _ in logged if "connection_test_record_error" in line]) == 2
    assert store.records == []


@pytest.mark.parametrize("error", _UNAVAILABLE, ids=lambda e: type(e).__name__)
def test_load_availability_errors_are_fail_open(store, error):
    store.fail_with = error
    assert _store_calls()["load"]() == {}


@pytest.mark.parametrize("error", _PROGRAMMING, ids=lambda e: type(e).__name__)
def test_load_programming_errors_are_not_silenced(store, error, monkeypatch):
    logged = _capture_errors(monkeypatch)
    store.fail_with = error
    with pytest.raises(type(error)):
        _store_calls()["load"]()
    assert any("connection_test_load_error" in line for line, _ in logged), logged


# Segreti della risorsa non leggibili: messaggio generico (fail-closed)


def _failing_loader(resource_id):
    raise RuntimeError("pool")


@pytest.mark.parametrize("loader", [lambda rid: None, _failing_loader], ids=["resource-none", "loader-error"])
def test_unreadable_secrets_save_the_generic_message(store, loader):
    @results.records_connection_test("datasource", id_param="ds_id", secrets_of=loader)
    def route(ds_id: str, current_user: CurrentUser):
        return {"success": False, "message": "Login fallito per sftp con password Zq8!x"}

    route(ds_id="d-1", current_user=_user())
    assert store.records[0].message == "Test non riuscito"


def test_secrets_of_is_mandatory():
    with pytest.raises(TypeError):
        results.records_connection_test("connection", id_param="connection_id")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        results.records_connection_test("connection", id_param="connection_id", secrets_of=None)


# Gli SQL dello store sono PostgreSQL valido


_STORE_SQL = {
    "record": results._RECORD_SQL,
    "reset": results._RESET_SQL,
    "load": results._LOAD_SQL,
}


@pytest.mark.parametrize("name", sorted(_STORE_SQL))
def test_store_sql_parses_as_postgresql(name):
    """Parser di PostgreSQL (libpg_query via pglast). pglast non e' nelle
    dipendenze del progetto: senza, il test si salta e resta il test
    integration ``test_connection_test_results_pg_integration.py`` (PG reale)."""
    pglast = pytest.importorskip("pglast")
    sql = _STORE_SQL[name].replace("%s", "NULL")
    (stmt,) = pglast.parse_sql(sql)
    assert stmt.stmt is not None
