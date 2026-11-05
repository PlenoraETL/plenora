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

    @results.records_connection_test("connection", id_param="connection_id")
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
    @results.records_connection_test("api_connection", id_param="api_id")
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
    @results.records_connection_test("api_connection", id_param="api_id")
    def route(api_id: str, current_user: CurrentUser):
        return {"success": True, "status_code": 200}

    route(api_id="a-1", current_user=_user())
    assert store.records[0].message == "Connessione riuscita (HTTP 200)"


def test_refused_test_is_not_recorded(store):
    @results.records_connection_test("datasource", id_param="ds_id")
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

    @results.records_connection_test("connection", id_param="connection_id")
    def route(connection_id: str, current_user: CurrentUser):
        return {"success": True, "message": "ok"}

    @results.resets_connection_test("connection", id_param="connection_id")
    def write(connection_id: str):
        return None

    assert route(connection_id="c-1", current_user=_user()) == {"success": True, "message": "ok"}
    write(connection_id="c-1")
    assert results.last_connection_tests("connection", ["c-1"]) == {}


def test_attach_marks_stale_after_declared_age(store):
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    store.rows = [
        {"resource_id": "fresh", "success": True, "message": "ok",
         "tested_at": now - timedelta(days=1), "tested_by_name": "mario", "duration_ms": 120},
        {"resource_id": "old", "success": False, "message": "Timeout",
         "tested_at": now - results.STALE_AFTER - timedelta(minutes=1),
         "tested_by_name": None, "duration_ms": 5000},
    ]
    statuses = results.last_connection_tests("connection", ["fresh", "old", "never"], now=now)
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
    results.attach_last_tests("connection", models)
    assert models[0].last_test.success is True and models[1].last_test is None

    dicts = [{"id": "a"}, {"id": "b"}]
    results.attach_last_tests("api_connection", dicts)
    assert dicts[0]["last_test"]["success"] is True
    assert dicts[0]["last_test"]["tested_at"].endswith(("Z", "+00:00"))
    assert dicts[1]["last_test"] is None


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        results.records_connection_test("ftp_server", id_param="x")


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
