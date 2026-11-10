"""Dashboard Grafana degli Eseguibili: regole, endpoint, sicurezza (11/11/2026).

Raccolta: ``docs/audits/grafana-dashboard-raccolta-2026-11-11.md``; correzione
del giro di conferma nel piano (sezione «Dashboard Grafana: correzione del
giro di conferma»). Grafana e' SEMPRE finto (``client.set_client_factory``):
nessuna rete. Il finto risolve anche i permessi come Grafana (ruolo, team,
cartella), cosi' i test verificano CHI VEDE e CHI MODIFICA, non le chiamate.

Copre: lasciapassare != login != sessione e ogni gettone non valido = 401
(enumerazione); forward_auth (identita' legata all'ID, ruolo sempre, team
riallineati alla richiesta, cache, Grafana giu' = 503); visibilita' in
Grafana = visibilita' in Plenora (matrice utenti x Eseguibili x
condivisioni); endpoint dati (lasciapassare in intestazione, limite contato
dopo la verifica); gestione (crea, elimina, link, avvisi con timeout e
cache); con ``GRAFANA_ENABLED=false`` nessuna chiamata a Grafana.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

PASS_SECRET = "segreto-lasciapassare-di-prova-abcdefghijklmnop"
DATA_SECRET = "segreto-grafana-di-prova"
PASS_HEADER = "X-Dashboard-Pass"


# ── Grafana finto (con la risoluzione dei permessi) ─────────────────────────

class FakeGrafana:
    def __init__(self):
        self.calls: list[tuple] = []
        self.folders: dict[str, dict] = {}
        self.permissions: dict[str, list] = {}
        self.teams: dict[str, int] = {}
        self.members: dict[int, dict[str, int]] = {}
        self.users: dict[str, dict] = {}
        self.dashboards: dict[str, dict] = {}
        self.fail = False

    def _log(self, *call):
        if self.fail:
            from app.core.grafana.client import GrafanaUnavailable

            raise GrafanaUnavailable("finto giu'")
        self.calls.append(call)

    # Auth proxy come Grafana 13: crea l'utente, aggiorna nome e ruolo dalle
    # intestazioni (senza intestazione di ruolo il ruolo resta quello vecchio).
    def _proxy_user(self, headers):
        login = headers["X-WEBAUTH-USER"]
        user = self.users.get(login)
        if user is None:
            user = self.users[login] = {"id": 100 + len(self.users), "login": login, "name": "", "role": "Viewer"}
        if "X-WEBAUTH-NAME" in headers:
            user["name"] = headers["X-WEBAUTH-NAME"]
        if "X-WEBAUTH-ROLE" in headers:
            user["role"] = headers["X-WEBAUTH-ROLE"]
        return user

    def proxy_get(self, headers, path):
        self._log("proxy_get", headers.get("X-WEBAUTH-USER"), path)
        user = self._proxy_user(headers)
        if path == "/api/user":
            return {"id": user["id"], "login": user["login"], "name": user["name"]}
        if path == "/api/user/teams":
            return [{"id": tid, "name": name} for name, tid in self.teams.items()
                    if user["login"] in self.members.get(tid, {})]
        raise AssertionError(path)

    def get_folder(self, uid):
        self._log("get_folder", uid)
        return self.folders.get(uid)

    def create_folder(self, uid, title):
        self._log("create_folder", uid)
        self.folders[uid] = {"uid": uid, "title": title}
        return self.folders[uid]

    def delete_folder(self, uid):
        self._log("delete_folder", uid)
        self.permissions.pop(uid, None)
        return self.folders.pop(uid, None) is not None

    def set_folder_permissions(self, uid, items):
        self._log("set_folder_permissions", uid)
        self.permissions[uid] = list(items)

    def find_team(self, name):
        self._log("find_team", name)
        return self.teams.get(name)

    def create_team(self, name):
        self._log("create_team", name)
        self.teams[name] = len(self.teams) + 10
        self.members[self.teams[name]] = {}
        return self.teams[name]

    def team_member_logins(self, team_id):
        self._log("team_member_logins", team_id)
        return dict(self.members.get(team_id, {}))

    def add_team_member(self, team_id, user_id):
        self._log("add_team_member", team_id, user_id)
        login = next(login for login, u in self.users.items() if u["id"] == user_id)
        self.members.setdefault(team_id, {})[login] = user_id

    def remove_team_member(self, team_id, user_id):
        self._log("remove_team_member", team_id, user_id)
        self.members[team_id] = {k: v for k, v in self.members[team_id].items() if v != user_id}

    def get_dashboard(self, uid):
        self._log("get_dashboard", uid)
        entry = self.dashboards.get(uid)
        return None if entry is None else {"dashboard": entry["dashboard"], "folder_uid": entry["folder"]}

    def upsert_dashboard(self, dashboard, *, folder_uid, message):
        self._log("upsert_dashboard", dashboard["uid"], folder_uid)
        self.dashboards[dashboard["uid"]] = {"dashboard": dict(dashboard), "folder": folder_uid}
        return {"status": "success"}

    def delete_dashboard(self, uid):
        self._log("delete_dashboard", uid)
        return self.dashboards.pop(uid, None) is not None

    # Risoluzione dei permessi come Grafana: Admin vede e modifica tutto;
    # altrimenti la cartella (ruolo Viewer o team di cui e' membro).
    def permission(self, login, dashboard_uid):
        user = self.users.get(login)
        if user is None or dashboard_uid not in self.dashboards:
            return 0
        if user["role"] == "Admin":
            return 2
        granted = 0
        for item in self.permissions.get(self.dashboards[dashboard_uid]["folder"], []):
            if item.get("role") == "Viewer" or ("teamId" in item and login in self.members.get(item["teamId"], {})):
                granted = max(granted, int(item["permission"]))
        return granted

    def visible_dashboards(self, login):
        return {uid for uid in self.dashboards if self.permission(login, uid) >= 1}


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, data, key):
        self.objects[key] = bytes(data)

    def set_object_tags(self, key, tags):
        pass

    def list_objects(self, prefix):
        return [{"key": k, "size": len(v), "last_modified": None} for k, v in self.objects.items()
                if k.startswith(prefix)]

    def get_object_stream(self, key):
        return {"body": BytesIO(self.objects[key]), "size": len(self.objects[key])}

    def delete(self, key):
        self.objects.pop(key, None)


def _user(user_id, username, role, group_id="g1", active=True):
    from app.core.auth.db import UserRow

    return UserRow(id=user_id, username=username, role=role, group_id=group_id, is_active=active)


USERS = {
    "u-exec": _user("u-exec", "esecutore1", "esecutore"),
    "u-other": _user("u-other", "esecutore2", "esecutore"),
    "u-admin": _user("u-admin", "admin1", "admin"),
    "u-admin2": _user("u-admin2", "admin2", "admin"),
    "u-super": _user("u-super", "capo", "superadmin", group_id="g0"),
    "u-off": _user("u-off", "spento", "esecutore", active=False),
    "u-g2": _user("u-g2", "esecutore-g2", "esecutore", group_id="g2"),
    "u-admin-g2": _user("u-admin-g2", "admin-g2", "admin", group_id="g2"),
    "u-g3": _user("u-g3", "esecutore-g3", "esecutore", group_id="g3"),
}


def _login(user_id):
    return f"plenora-u-{user_id}"


@pytest.fixture
def env(monkeypatch):
    from app.core.auth import db as users_db
    from app.core.eseguibili import dashboard as rules
    from app.core.eseguibili import dashboard_router, storage
    from app.core.eseguibili import db as eseguibili_db
    from app.core.eseguibili.db import EseguibileRow
    from app.core.grafana import access as grafana_access
    from app.core.grafana import client as grafana_client

    monkeypatch.setenv("GRAFANA_ENABLED", "true")
    monkeypatch.setenv("GRAFANA_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("DASHBOARD_PASS_SECRET", PASS_SECRET)
    monkeypatch.setenv("GRAFANA_DATA_SECRET", DATA_SECRET)

    fake_storage = _FakeStorage()
    monkeypatch.setattr(storage, "_storage", lambda: fake_storage)
    grafana = FakeGrafana()
    grafana_client.set_client_factory(lambda: grafana)
    grafana_access.forget()
    rules.dashboard_read_cache.forget()

    records = {
        "exec_1": EseguibileRow(
            id="exec_1", name="Report istituti", description="", canvas={}, expected_columns=[],
            default_output_format="csv", group_id="g1", created_by="u-admin", created_at="t", updated_at="t",
            source_canvas_id="prj_1",
        ),
        "exec_super": EseguibileRow(
            id="exec_super", name="Del capo", description="", canvas={}, expected_columns=[],
            default_output_format="csv", group_id="g1", created_by="u-super", created_at="t", updated_at="t",
            source_canvas_id="prj_1",
        ),
        "exec_g2": EseguibileRow(
            id="exec_g2", name="Del gruppo 2", description="", canvas={}, expected_columns=[],
            default_output_format="csv", group_id="g2", created_by="u-admin-g2", created_at="t", updated_at="t",
            source_canvas_id="prj_1",
        ),
    }
    users = dict(USERS)
    shares: dict[str, list[str]] = {}
    monkeypatch.setattr(eseguibili_db, "get_eseguibile", lambda eid, group_id=None: records.get(eid))

    def set_uid(eid, uid):
        records[eid] = dataclasses.replace(records[eid], dashboard_uid=uid)
        return records[eid]

    monkeypatch.setattr(eseguibili_db, "set_dashboard_uid", set_uid)
    monkeypatch.setattr(users_db, "get_user", lambda uid: (u if (u := users.get(uid)) else None))
    monkeypatch.setattr(users_db, "get_user_including_deleted", lambda uid: users.get(uid))
    monkeypatch.setattr(users_db, "list_users", lambda gid, limit=200, offset=0: [
        u for u in users.values() if u.group_id == gid])
    monkeypatch.setattr(users_db, "get_group", lambda gid: SimpleNamespace(id=gid, name=f"Gruppo {gid}"))
    monkeypatch.setattr(rules, "list_resource_shares", lambda table, rid: list(shares.get(rid, [])))
    contract = {"columns": [
        {"physical_name": "istituto", "type": {"logical": "string"}, "ordinal": 0},
        {"physical_name": "importo", "type": {"logical": "number"}, "ordinal": 1},
        {"physical_name": "geom", "type": {"logical": "geometry"}, "ordinal": 2},
    ]}
    monkeypatch.setattr(rules.ProjectOutputContractService, "get_project_output_contract",
                        lambda self, pid: contract)
    monkeypatch.setattr(dashboard_router, "assert_resource_visible", lambda *a, **k: None)
    dashboard_router.rate_limit.reset()
    yield SimpleNamespace(grafana=grafana, storage=fake_storage, records=records, users=users, contract=contract,
                          shares=shares)
    grafana_client.set_client_factory(None)
    grafana_access.forget()
    rules.dashboard_read_cache.forget()


def _current(user_id, users=None):
    from app.shared.contracts.auth import CurrentUser

    row = (users or USERS)[user_id]
    return CurrentUser(user_id=row.id, username=row.username, group_id=row.group_id, role=row.role)


def _app(user_id=None):
    from app.core.auth import dependencies
    from app.core.auth.grafana_router import grafana_router
    from app.core.auth.shares_router import router as shares_router
    from app.core.eseguibili import dashboard_router

    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api")
    app.include_router(dashboard_router.data_router, prefix="/api")
    app.include_router(grafana_router, prefix="/api")
    app.include_router(shares_router, prefix="/api")
    if user_id:
        current = _current(user_id)
        app.dependency_overrides[dependencies.get_current_user] = lambda: current
        app.dependency_overrides[dependencies.require_admin] = lambda: dependencies.require_admin(current)
        app.dependency_overrides[dependencies.require_superadmin] = lambda: dependencies.require_superadmin(current)
    return TestClient(app, raise_server_exceptions=False)


def _run(env, task_id="task-1", user_id="u-exec", eseguibile_id="exec_1", rows=3, now=None):
    """Un'esecuzione riuscita con copia per la dashboard."""
    from app.core.eseguibili import dashboard_data, output_store

    group_id = env.records[eseguibile_id].group_id
    output_store.save_task_owner(task_id, eseguibile_id, user_id, group_id=group_id)
    env.storage.objects[f"tenants/{group_id}/eseguibili-outputs/{task_id}/report.csv"] = b"x"
    collector = dashboard_data.DashboardRowsCollector()
    collector.add_rows(["istituto", "importo"], [(f"IST{i}", i * 1.5) for i in range(rows)])
    dashboard_data.write(task_id, group_id=group_id, collector=collector, now=now)


def _with_dashboard(env, eseguibile_id="exec_1", requester="u-admin"):
    from app.core.eseguibili import dashboard as rules

    env.records[eseguibile_id] = rules.create_dashboard(env.records[eseguibile_id], _current(requester, env.users))
    return env.records[eseguibile_id]


def _enter_grafana(user_id):
    """Una richiesta del browser a /grafana: Caddy chiama il forward_auth."""
    client = _app()
    client.cookies.set("plenora_grafana_session", _session_cookie(user_id))
    return client.get("/api/grafana/auth")


# ── Firme: lasciapassare, sessione, login ───────────────────────────────────

def test_pass_is_not_a_login_token_and_login_is_not_a_pass(env):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.auth import dependencies, jwt_token
    from app.core.grafana import signing

    token = signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u-exec", expires_at=int(time.time()) + 60)
    assert jwt_token.decode_token(token) is None
    with pytest.raises(HTTPException) as refused:
        dependencies.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert refused.value.status_code == 401

    login = jwt_token.create_access_token("u-exec", "esecutore1", "g1", "esecutore")
    with pytest.raises(signing.InvalidToken):
        signing.verify_pass(login)
    with pytest.raises(signing.InvalidToken):
        signing.verify_session(login)


def test_pass_and_session_are_not_interchangeable(env):
    from app.core.grafana import signing

    future = int(time.time()) + 60
    session = signing.issue_session(user_id="u-exec", expires_at=future)
    dashboard_pass = signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u-exec", expires_at=future)
    with pytest.raises(signing.InvalidToken):
        signing.verify_pass(session)
    with pytest.raises(signing.InvalidToken):
        signing.verify_session(dashboard_pass)
    assert signing.verify_pass(dashboard_pass).task_id == "t"
    assert signing.verify_session(session).user_id == "u-exec"


def test_tampered_expired_and_unconfigured_tokens_are_rejected(env, monkeypatch):
    from app.core.grafana import signing

    token = signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u", expires_at=int(time.time()) + 60)
    body, signature = token.split(".")
    other = signing.issue_pass(task_id="t2", eseguibile_id="e", user_id="u", expires_at=int(time.time()) + 60)
    for forged in (f"{other.split('.')[0]}.{signature}", body, "", "a.b.c"):
        with pytest.raises(signing.InvalidToken):
            signing.verify_pass(forged)
    expired = signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u", expires_at=int(time.time()) - 1)
    with pytest.raises(signing.InvalidToken, match="scaduto"):
        signing.verify_pass(expired)
    monkeypatch.setenv("JWT_SECRET_KEY", PASS_SECRET)
    monkeypatch.setenv("DASHBOARD_PASS_SECRET", "")
    with pytest.raises(signing.SigningUnavailable):
        signing.verify_pass(token)


# Enumerazione dei gettoni non validi (punto 8 della conferma): OGNI errore di
# forma o di decodifica e' InvalidToken (401), mai un'altra eccezione (500).

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed(token_type: str, body: bytes) -> str:
    """Firma VALIDA su un corpo arbitrario (chi conosce la chiave: il caso
    peggiore per il decodificatore)."""
    return _signed_text(token_type, _b64(body))


def _signed_text(token_type: str, encoded: str) -> str:
    from app.core.grafana import signing

    return f"{encoded}.{_b64(hmac.new(signing._key(token_type), encoded.encode('ascii'), hashlib.sha256).digest())}"


def _bad_tokens(token_type: str) -> dict[str, str]:
    from app.core.grafana import signing

    future = int(time.time()) + 600
    valid = (signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u-exec", expires_at=future)
             if token_type == signing.PASS_TYPE else signing.issue_session(user_id="u-exec", expires_at=future))
    fields = {"t": "t", "e": "e", "u": "u-exec"} if token_type == signing.PASS_TYPE else {"u": "u-exec"}

    def body(**overrides):
        payload = {"typ": token_type, "exp": future, **fields, **overrides}
        return json.dumps({k: v for k, v in payload.items() if v is not ...}).encode("utf-8")

    cases = {
        "vuoto": "", "una parte": "abc", "tre parti": "a.b.c", "solo punto": ".", "parte vuota": "abc.",
        "non ascii corpo": "è.é", "non ascii firma": valid.split(".")[0] + ".dé", "non ascii in coda": valid + "é",
        "troppo lungo": "a" * 5000 + "." + "b" * 10, "a capo in coda": valid + "\n", "spazio in testa": " " + valid,
        "padding": valid + "=", "base64 standard": valid.replace("-", "+").replace("_", "/") + "+/",
        "nul": valid.replace(".", "\x00."), "firma altrui": valid.split(".")[0] + "." + _b64(b"x" * 32),
        "base64 rotto firmato": _signed_text(token_type, "abcde"),
        "utf8 rotto firmato": _signed(token_type, b"\xff\xfe\xfd"),
        "non json": _signed(token_type, b"non json"),
        "json lista": _signed(token_type, b"[]"),
        "json annidato": _signed(token_type, b"[" * 1200 + b"]" * 1200),
        "senza typ": _signed(token_type, body(typ=...)),
        "typ altrui": _signed(token_type, body(typ="plenora-altro")),
        "senza exp": _signed(token_type, body(exp=...)),
        "exp testo": _signed(token_type, body(exp=str(future))),
        "exp booleano": _signed(token_type, body(exp=True)),
        "exp decimale": _signed(token_type, body(exp=future + 0.5)),
        "u mancante": _signed(token_type, body(u=...)),
        "u vuoto": _signed(token_type, body(u="")),
        "u numero": _signed(token_type, body(u=7)),
    }
    if token_type == signing.PASS_TYPE:
        cases.update({
            "t mancante": _signed(token_type, body(t=...)),
            "e lista": _signed(token_type, body(e=["x"])),
        })
    return cases


@pytest.mark.parametrize("kind", ["pass", "session"])
def test_every_malformed_token_is_invalid_token_never_another_error(env, kind):
    from app.core.grafana import signing

    token_type, verify = ((signing.PASS_TYPE, signing.verify_pass) if kind == "pass"
                          else (signing.SESSION_TYPE, signing.verify_session))
    for name, token in _bad_tokens(token_type).items():
        try:
            verify(token)
        except signing.InvalidToken:
            continue
        except Exception as exc:  # pragma: no cover - e' il difetto che il test cerca
            pytest.fail(f"{kind} {name!r}: {type(exc).__name__} invece di InvalidToken")
        pytest.fail(f"{kind} {name!r}: accettato")


def test_malformed_cookie_and_pass_are_401_over_http(env):
    from app.core.grafana import signing

    _with_dashboard(env)
    _run(env)
    for name, token in _bad_tokens(signing.SESSION_TYPE).items():
        if "\n" in token or "\x00" in token or token != token.strip():
            continue  # non rappresentabili in un'intestazione HTTP (o ripuliti dal protocollo)
        response = _app().get("/api/grafana/auth", headers={"cookie": f"plenora_grafana_session={token}".encode("utf-8")})
        assert response.status_code == 401, name
    for name, token in _bad_tokens(signing.PASS_TYPE).items():
        if "\n" in token or "\x00" in token or token != token.strip() or not token:
            continue
        response = _app().get("/api/dash/exec/task-1/rows",
                              headers={"X-Grafana-Secret": DATA_SECRET, PASS_HEADER: token.encode("utf-8")})
        assert response.status_code == 401, (name, response.status_code)


# ── forward_auth: identita', ruolo, team ────────────────────────────────────

def _session_cookie(user_id, ttl=60):
    from app.core.grafana import signing

    return signing.issue_session(user_id=user_id, expires_at=int(time.time()) + ttl)


def test_session_endpoint_sets_dedicated_cookie(env):
    response = _app("u-exec").post("/api/grafana/session")
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("plenora_grafana_session=")
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered and "path=/grafana" in lowered
    assert "max-age=1800" in lowered


@pytest.mark.parametrize("cookie_user,expected_status,name_header,role_header", [
    (None, 401, None, None),              # cookie assente
    ("u-exec", 200, "esecutore1", "Viewer"),  # ruolo SEMPRE presente
    ("u-admin", 200, "admin1", "Viewer"),     # admin di gruppo: Edit dal team, non dal ruolo
    ("u-super", 200, "capo", "Admin"),
    ("u-off", 401, None, None),             # disattivato
    ("u-gone", 401, None, None),            # eliminato (lapide: get_user non la vede)
])
def test_forward_auth(env, cookie_user, expected_status, name_header, role_header):
    client = _app()
    if cookie_user:
        client.cookies.set("plenora_grafana_session", _session_cookie(cookie_user))
    response = client.get("/api/grafana/auth")
    assert response.status_code == expected_status
    # Identita' legata all'ID (mai lo username), nome visualizzato a parte.
    assert response.headers.get("x-webauth-user") == (_login(cookie_user) if expected_status == 200 else None)
    assert response.headers.get("x-webauth-name") == name_header
    assert response.headers.get("x-webauth-role") == role_header


def test_forward_auth_rejects_expired_session_pass_and_login(env):
    from app.core.auth import jwt_token
    from app.core.grafana import signing

    for value in (
        _session_cookie("u-exec", ttl=-5),
        signing.issue_pass(task_id="t", eseguibile_id="e", user_id="u-exec", expires_at=int(time.time()) + 60),
        jwt_token.create_access_token("u-exec", "esecutore1", "g1", "esecutore"),
        "spazzatura",
    ):
        client = _app()
        client.cookies.set("plenora_grafana_session", value)
        assert client.get("/api/grafana/auth").status_code == 401


def _teams_of(env, user_id):
    login = _login(user_id)
    return {name for name, tid in env.grafana.teams.items() if login in env.grafana.members.get(tid, {})}


def test_forward_auth_applies_role_and_teams_before_letting_in(env):
    for user_id, teams, role in (
        ("u-exec", {"utenti-g1"}, "Viewer"),
        ("u-admin", {"utenti-g1", "admin-g1"}, "Viewer"),
        ("u-super", {"utenti-g0"}, "Admin"),
    ):
        assert _enter_grafana(user_id).status_code == 200
        assert _teams_of(env, user_id) == teams
        assert env.grafana.users[_login(user_id)]["role"] == role
    assert env.grafana.users[_login("u-admin")]["name"] == "admin1"


def test_moved_or_demoted_admin_is_realigned_at_the_next_request(env):
    _with_dashboard(env)
    assert _enter_grafana("u-admin2").status_code == 200
    assert _teams_of(env, "u-admin2") == {"utenti-g1", "admin-g1"}
    assert env.grafana.permission(_login("u-admin2"), "plenora-exec_1") == 2
    # Spostato nel gruppo g2 e retrocesso: alla richiesta successiva (nessuna
    # sincronizzazione da «Modifica in Grafana» nel frattempo).
    env.users["u-admin2"] = dataclasses.replace(env.users["u-admin2"], role="esecutore", group_id="g2")
    assert _enter_grafana("u-admin2").status_code == 200
    assert _teams_of(env, "u-admin2") == {"utenti-g2"}
    assert env.grafana.permission(_login("u-admin2"), "plenora-exec_1") == 0


def test_demoted_superadmin_loses_grafana_admin_at_the_next_request(env):
    assert _enter_grafana("u-super").status_code == 200
    assert env.grafana.users[_login("u-super")]["role"] == "Admin"
    env.users["u-super"] = dataclasses.replace(env.users["u-super"], role="admin")
    response = _enter_grafana("u-super")
    assert response.headers["x-webauth-role"] == "Viewer"
    assert env.grafana.users[_login("u-super")]["role"] == "Viewer"


def test_reused_username_does_not_inherit_the_old_grafana_user(env):
    from app.core.eseguibili import dashboard as rules

    _with_dashboard(env)
    env.users["u-old"] = _user("u-old", "mario", "admin")
    assert _enter_grafana("u-old").status_code == 200
    assert _teams_of(env, "u-old") == {"utenti-g1", "admin-g1"}
    # Lapide (lo username torna libero) e nuovo utente con lo stesso nome in g2.
    del env.users["u-old"]
    env.users["u-new"] = _user("u-new", "mario", "esecutore", group_id="g2")
    assert _enter_grafana("u-old").status_code == 401
    response = _enter_grafana("u-new")
    assert response.headers["x-webauth-user"] == _login("u-new") != _login("u-old")
    assert _teams_of(env, "u-new") == {"utenti-g2"}
    assert env.grafana.permission(_login("u-new"), "plenora-exec_1") == 0
    # La sincronizzazione successiva toglie la lapide dai team del gruppo.
    rules.edit_link(env.records["exec_1"], _current("u-admin"))
    assert _teams_of(env, "u-old") == set()


def test_forward_auth_syncs_once_then_uses_the_cache(env):
    assert _enter_grafana("u-exec").status_code == 200
    env.grafana.calls.clear()
    for _ in range(5):
        assert _enter_grafana("u-exec").status_code == 200
    assert env.grafana.calls == []
    # Voce scaduta (dopo GRAFANA_ACCESS_SYNC_TTL_SECONDS): si risincronizza.
    from app.core.grafana import access, sync

    user = sync.PlenoraUser("u-exec", "esecutore1", "esecutore", "g1")
    access.forget()
    assert access.ensure_access(user, now=1_000.0) is True
    assert access.ensure_access(user, now=1_299.0) is False
    assert access.ensure_access(user, now=1_301.0) is True
    # Impronta cambiata (ruolo, gruppo, nome): subito, anche dentro il TTL.
    assert access.ensure_access(dataclasses.replace(user, group_id="g2"), now=1_302.0) is True


def test_forward_auth_is_503_when_grafana_cannot_align_the_user(env):
    env.grafana.fail = True
    response = _enter_grafana("u-exec")
    assert response.status_code == 503
    assert "x-webauth-user" not in response.headers
    env.grafana.fail = False
    assert _enter_grafana("u-exec").status_code == 200


def test_grafana_login_is_always_a_safe_header_value():
    from app.core.grafana import sync

    for user_id in ("usr_abc", "a" * 200, "con spazio", "àccènto", "x\ny", ""):
        login = sync.grafana_login(user_id)
        assert login.startswith(sync.LOGIN_PREFIX) and login.isascii() and len(login) <= 80
        assert all(ch.isalnum() or ch in "_.-" for ch in login)
    assert sync.grafana_login("a b") != sync.grafana_login("a_b")
    assert sync.display_name("Łukasz Ñandú") == "?ukasz ?and?"
    assert sync.display_name("x" * 500) == "x" * 100


# ── Visibilita' in Grafana = visibilita' in Plenora ─────────────────────────

def _plenora_can_view(user, record, shares):
    """``auth.visibility.is_resource_visible``: superadmin, gruppo proprietario,
    gruppi condivisi, ``*`` = tutti."""
    return (user.role == "superadmin" or user.group_id == record.group_id
            or user.group_id in shares or "*" in shares)


@pytest.mark.parametrize("shares", [[], ["g2"], ["*"], ["g2", "g3"]])
def test_grafana_visibility_matches_plenora_for_every_user_and_eseguibile(env, shares):
    from app.core.auth.ownership import compute_can_edit

    for eseguibile_id, creator in (("exec_1", "u-admin"), ("exec_super", "u-super"), ("exec_g2", "u-admin-g2")):
        env.shares[eseguibile_id] = list(shares) if eseguibile_id != "exec_g2" else []
        _with_dashboard(env, eseguibile_id, requester=creator)
    for user_id, user in env.users.items():
        if not user.is_active:
            assert _enter_grafana(user_id).status_code == 401
            continue
        assert _enter_grafana(user_id).status_code == 200
        for eseguibile_id, record in env.records.items():
            uid = record.dashboard_uid
            view = env.grafana.permission(_login(user_id), uid) >= 1
            edit = env.grafana.permission(_login(user_id), uid) >= 2
            assert view == _plenora_can_view(user, record, env.shares.get(eseguibile_id, [])), (user_id, eseguibile_id)
            assert edit == compute_can_edit(record, _current(user_id, env.users)), (user_id, eseguibile_id)


def test_no_folder_grants_view_to_every_viewer_unless_shared_with_all(env):
    _with_dashboard(env)
    items = env.grafana.permissions["plenora-e-exec_1"]
    assert {"role": "Viewer", "permission": 1} not in items
    assert env.grafana.dashboards["plenora-exec_1"]["folder"] == "plenora-e-exec_1"
    assert _enter_grafana("u-g2").status_code == 200
    assert env.grafana.visible_dashboards(_login("u-g2")) == set()


def test_share_changes_realign_the_folder_through_the_shares_router(env):
    _with_dashboard(env)
    assert _enter_grafana("u-g2").status_code == 200
    assert env.grafana.permission(_login("u-g2"), "plenora-exec_1") == 0

    import app.core.auth.shares_router as shares_router_module

    def owner(*_a, **_k):
        return {"id": "exec_1", "group_id": "g1"}

    def put(rtype, rid, groups):
        env.shares[rid] = list(groups)

    def add(rtype, rid, group):
        env.shares.setdefault(rid, []).append(group)

    def remove(rtype, rid, group):
        env.shares[rid] = [g for g in env.shares.get(rid, []) if g != group]

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(shares_router_module, "_check_owner_access", owner)
        mp.setattr(shares_router_module, "set_resource_shares", put)
        mp.setattr(shares_router_module, "add_resource_share", add)
        mp.setattr(shares_router_module, "remove_resource_share", remove)
        mp.setattr(shares_router_module, "list_resource_shares", lambda rtype, rid: list(env.shares.get(rid, [])))
        superadmin = _app("u-super")
        assert superadmin.post("/api/shares/eseguibili/exec_1/g2").status_code == 200
        assert env.grafana.permission(_login("u-g2"), "plenora-exec_1") == 1
        assert env.grafana.permission(_login("u-g3"), "plenora-exec_1") == 0
        assert superadmin.put("/api/shares/eseguibili/exec_1", json={"group_ids": ["*"]}).status_code == 200
        assert _enter_grafana("u-g3").status_code == 200
        assert env.grafana.permission(_login("u-g3"), "plenora-exec_1") == 1
        assert superadmin.delete("/api/shares/eseguibili/exec_1/*").status_code == 200
        assert env.grafana.permission(_login("u-g2"), "plenora-exec_1") == 0
        assert env.grafana.permission(_login("u-g3"), "plenora-exec_1") == 0
        # Grafana giu': la condivisione resta scritta (best effort dichiarato).
        env.grafana.fail = True
        assert superadmin.post("/api/shares/eseguibili/exec_1/g2").status_code == 200
        assert env.shares["exec_1"] == ["g2"]
    finally:
        mp.undo()


def test_only_the_eseguibili_share_hook_is_registered():
    import app.core.eseguibili.dashboard as rules
    from app.core.auth import share_hooks

    assert share_hooks.registered("eseguibili") == (rules.on_shares_changed,)
    for table in ("connections", "datasources", "api_connections", "arcgis_connections", "file_sources"):
        assert share_hooks.registered(table) == ()


def test_every_share_write_of_the_router_notifies():
    """Enumerazione: ogni endpoint che scrive condivisioni chiama notify."""
    import ast
    import inspect

    import app.core.auth.shares_router as shares_router_module

    tree = ast.parse(inspect.getsource(shares_router_module))
    writers = {"set_resource_shares", "add_resource_share", "remove_resource_share"}
    for function in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        calls = [c.func for c in ast.walk(function) if isinstance(c, ast.Call)]
        names = {getattr(f, "id", None) or getattr(f, "attr", None) for f in calls}
        if names & writers:
            assert "notify" in names, function.name


# ── Endpoint dati ───────────────────────────────────────────────────────────

def _pass_for(env, task_id="task-1", user_id="u-exec", eseguibile_id="exec_1"):
    from app.core.eseguibili import dashboard as rules

    return rules.issue_pass(env.records[eseguibile_id], task_id, _current(user_id))["pass_token"]


def _rows(client, task_id, token, secret=DATA_SECRET):
    headers = {"X-Grafana-Secret": secret} if secret else {}
    if token is not None:
        headers[PASS_HEADER] = token
    return client.get(f"/api/dash/exec/{task_id}/rows", headers=headers)


def test_data_endpoint_serves_rows_non_destructively(env):
    _with_dashboard(env)
    _run(env)
    token = _pass_for(env)
    client = _app()
    for _ in range(2):
        response = _rows(client, "task-1", token)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == [{"istituto": f"IST{i}", "importo": i * 1.5} for i in range(3)]


def test_pass_travels_in_a_header_never_in_the_url(env):
    """Il log di accesso del backend registra path e query string: il
    lasciapassare non deve mai starci (punto 4 della conferma)."""
    _with_dashboard(env)
    _run(env)
    token = _pass_for(env)
    query = env.grafana.dashboards["plenora-exec_1"]["dashboard"]["panels"][0]["targets"][0]
    assert query["url_options"]["headers"] == [{"key": PASS_HEADER, "value": "${pass}"}]
    assert "params" not in query["url_options"] and "pass" not in query["url"]
    # In query string non vale: solo l'intestazione apre i dati.
    client = _app()
    assert client.get("/api/dash/exec/task-1/rows", params={"pass": token},
                      headers={"X-Grafana-Secret": DATA_SECRET}).status_code == 401
    assert _rows(client, "task-1", token).status_code == 200


def test_data_endpoint_requires_grafana_secret_but_access_is_the_pass(env):
    _with_dashboard(env)
    _run(env)
    token = _pass_for(env)
    client = _app()
    assert _rows(client, "task-1", token, secret=None).status_code == 401
    assert _rows(client, "task-1", token, secret="sbagliato").status_code == 401
    # Il segreto da solo non basta.
    assert _rows(client, "task-1", None).status_code == 401
    assert _rows(client, "task-1", "x.y").status_code == 401


def test_data_endpoint_rejects_other_task_expired_and_traversal(env):
    from app.core.grafana import signing

    _with_dashboard(env)
    _run(env, "task-1")
    _run(env, "task-2")
    client = _app()
    token_1 = _pass_for(env, "task-1")
    assert _rows(client, "task-2", token_1).status_code == 401  # lasciapassare di altro task
    expired = signing.issue_pass(task_id="task-1", eseguibile_id="exec_1", user_id="u-exec",
                                 expires_at=int(time.time()) - 1)
    assert _rows(client, "task-1", expired).status_code == 401
    for bad in ("a..b", "..", "x%2F..%2Fy", "t%00", "task-1%0A"):
        assert _rows(client, bad, token_1).status_code in {400, 404}
    # Lasciapassare di un altro utente forgiato per il task di esecutore1.
    forged = signing.issue_pass(task_id="task-1", eseguibile_id="exec_1", user_id="u-other",
                                expires_at=int(time.time()) + 60)
    assert _rows(client, "task-1", forged).status_code == 401


def test_data_endpoint_disabled_user_loses_access(env):
    _with_dashboard(env)
    _run(env)
    token = _pass_for(env)
    env.users["u-exec"] = dataclasses.replace(env.users["u-exec"], is_active=False)
    assert _rows(_app(), "task-1", token).status_code == 401


def test_data_is_kept_after_download_and_gone_after_full_cleanup(env):
    from app.core.eseguibili import output_store

    _with_dashboard(env)
    _run(env)
    token = _pass_for(env)
    client = _app()
    output_store.delete_task_outputs("task-1", group_id="g1", retain_owner=True)  # «Scarica»
    assert _rows(client, "task-1", token).status_code == 200
    output_store.delete_task_outputs("task-1", group_id="g1")  # chiusura finestra
    response = _rows(client, "task-1", token)
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "dashboard_data_unavailable"


def test_data_endpoint_row_limit_and_rate_limit_counted_after_the_pass(env, monkeypatch):
    from app.core.eseguibili import dashboard_router

    monkeypatch.setenv("DASHBOARD_DATA_MAX_ROWS", "5")
    monkeypatch.setenv("DASHBOARD_DATA_RATE_PER_MINUTE", "3")
    _with_dashboard(env)
    _run(env, rows=12)
    token = _pass_for(env)
    client = _app()
    # Chi non ha un lasciapassare valido non consuma il limite (punto 9).
    for _ in range(10):
        assert _rows(client, "task-1", "x.y").status_code == 401
    assert len(_rows(client, "task-1", token).json()) == 5
    assert _rows(client, "task-1", token).status_code == 200
    assert _rows(client, "task-1", token).status_code == 200
    assert _rows(client, "task-1", token).status_code == 429
    # Il limite e' per esecuzione E utente: il superadmin ha il suo.
    super_token = _pass_for(env, user_id="u-super")
    assert _rows(client, "task-1", super_token).status_code == 200
    dashboard_router.rate_limit.reset()


def test_data_expires_after_thirty_minutes(env):
    from app.core.eseguibili import dashboard as rules

    _with_dashboard(env)
    start = time.time() - 1_790
    _run(env, now=start)
    token = _pass_for(env)
    assert _rows(_app(), "task-1", token).status_code == 200
    with pytest.raises(Exception) as gone:
        rules.read_rows("task-1", token, now=start + 1_801)
    assert type(gone.value).__name__ in {"DashboardDataGone", "PassRejected"}


# ── Lasciapassare: chi lo riceve ────────────────────────────────────────────

def test_pass_only_for_who_launched_the_run_or_superadmin(env):
    _with_dashboard(env)
    _run(env, user_id="u-exec")
    body = {"task_id": "task-1"}
    mine = _app("u-exec").post("/api/eseguibili/exec_1/dashboard/pass", json=body)
    assert mine.status_code == 200
    data = mine.json()
    assert data["iframe_path"].startswith("/grafana/d/plenora-exec_1?orgId=1&kiosk&var-exec=task-1&var-pass=")
    assert data["expires_at"] <= data["data_expires_at"] <= int(time.time()) + 1_800
    assert _app("u-other").post("/api/eseguibili/exec_1/dashboard/pass", json=body).status_code == 403
    assert _app("u-super").post("/api/eseguibili/exec_1/dashboard/pass", json=body).status_code == 200
    # Superadmin: il suo lasciapassare legge i dati.
    token = _app("u-super").post("/api/eseguibili/exec_1/dashboard/pass", json=body).json()["pass_token"]
    assert _rows(_app(), "task-1", token).status_code == 200


def test_pass_unavailable_without_copy_or_dashboard(env):
    from app.core.eseguibili import output_store

    client = _app("u-exec")
    body = {"task_id": "task-1"}
    output_store.save_task_owner("task-1", "exec_1", "u-exec", group_id="g1")
    assert client.post("/api/eseguibili/exec_1/dashboard/pass", json=body).status_code == 404  # senza dashboard
    _with_dashboard(env)
    response = client.post("/api/eseguibili/exec_1/dashboard/pass", json=body)  # rest_file: nessuna copia
    assert response.status_code == 410
    assert client.post("/api/eseguibili/exec_1/dashboard/pass", json={"task_id": "../x"}).status_code == 400


# ── Gestione ────────────────────────────────────────────────────────────────

def test_create_builds_dashboard_in_its_own_folder_with_the_rule(env):
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard")
    assert response.status_code == 201
    info = response.json()
    assert info["dashboard_uid"] == "plenora-exec_1"
    assert env.records["exec_1"].dashboard_uid == "plenora-exec_1"
    saved = env.grafana.dashboards["plenora-exec_1"]
    assert saved["folder"] == "plenora-e-exec_1"
    dashboard = saved["dashboard"]
    names = [v["name"] for v in dashboard["templating"]["list"]]
    assert set(names) == {"exec", "pass"} and all(v["hide"] == 2 for v in dashboard["templating"]["list"])
    query = dashboard["panels"][0]["targets"][0]
    assert "pass" not in query["url"] and "${exec}" in query["url"]
    assert query["url"].startswith("http://127.0.0.1:5004/api/dash/exec/")
    assert [c["selector"] for c in query["columns"]] == ["istituto", "importo"]  # geometria esclusa
    shared = [p for p in dashboard["panels"][1:]]
    assert {p["type"] for p in shared} == {"stat", "barchart"}
    assert all(p["datasource"]["uid"] == "-- Dashboard --" for p in shared)
    teams = env.grafana.teams
    assert env.grafana.permissions["plenora-e-exec_1"] == [
        {"teamId": teams["utenti-g1"], "permission": 1},
        {"teamId": teams["admin-g1"], "permission": 2},
    ]
    # Chi ha chiesto la creazione e' gia' nel team (il link funziona subito).
    assert _teams_of(env, "u-admin") == {"utenti-g1", "admin-g1"}
    assert _app("u-admin").post("/api/eseguibili/exec_1/dashboard").status_code == 409


def test_superadmin_created_eseguibile_has_no_editor_team_and_admin_cannot_manage(env):
    assert _app("u-admin").post("/api/eseguibili/exec_super/dashboard").status_code == 403
    assert _app("u-super").post("/api/eseguibili/exec_super/dashboard").status_code == 201
    assert env.grafana.dashboards["plenora-exec_super"]["folder"] == "plenora-e-exec_super"
    assert all(item["permission"] == 1 for item in env.grafana.permissions["plenora-e-exec_super"])
    assert _app("u-admin").post("/api/eseguibili/exec_super/dashboard/edit-link").status_code == 403
    assert _app("u-exec").post("/api/eseguibili/exec_1/dashboard").status_code == 403


def test_team_sync_removes_who_is_no_longer_admin(env):
    _with_dashboard(env)
    assert _enter_grafana("u-admin2").status_code == 200
    team = env.grafana.teams["admin-g1"]
    assert _login("u-admin2") in env.grafana.members[team]
    env.users["u-admin2"] = dataclasses.replace(env.users["u-admin2"], role="esecutore")  # cambia ruolo
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard/edit-link")
    assert response.status_code == 200
    assert set(env.grafana.members[team]) == {_login("u-admin")}
    assert response.json()["edit_path"] == "/grafana/d/plenora-exec_1?orgId=1"


def test_edit_link_moves_a_dashboard_from_an_old_group_folder(env):
    _with_dashboard(env)
    env.grafana.dashboards["plenora-exec_1"]["folder"] = "plenora-g-g1"  # versione precedente
    assert _app("u-admin").post("/api/eseguibili/exec_1/dashboard/edit-link").status_code == 200
    assert env.grafana.dashboards["plenora-exec_1"]["folder"] == "plenora-e-exec_1"


def test_edit_link_with_own_run_carries_the_data(env):
    _with_dashboard(env)
    _run(env, user_id="u-admin")
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard/edit-link", json={"task_id": "task-1"})
    assert response.status_code == 200
    assert "var-exec=task-1&var-pass=" in response.json()["edit_path"]


def test_warnings_for_columns_no_longer_expected(env):
    from app.core.eseguibili import dashboard as rules

    record = _with_dashboard(env)
    assert rules.dashboard_warnings(record) == []
    env.contract["columns"] = [c for c in env.contract["columns"] if c["physical_name"] != "importo"]
    warnings = rules.dashboard_warnings(record)
    assert [w["column"] for w in warnings] == ["importo"]


def test_warnings_read_grafana_with_short_timeout_and_cache(env, monkeypatch):
    """Il dettaglio di un Eseguibile non aspetta Grafana (punto 6): timeout
    dedicato di 1,5 s e risultato in cache, anche «non verificabile»."""
    from app.core.eseguibili import dashboard as rules
    from app.core.grafana import client as grafana_client

    record = _with_dashboard(env)
    rules.dashboard_read_cache.forget()
    timeouts = []

    def default_client(timeout=None):
        timeouts.append(timeout)
        return env.grafana

    grafana_client.set_client_factory(None)
    monkeypatch.setattr(grafana_client, "_default_client", default_client)
    env.grafana.calls.clear()
    for _ in range(3):
        assert rules.dashboard_warnings(record) == []
    assert timeouts == [1.5] and [c[0] for c in env.grafana.calls] == ["get_dashboard"]

    rules.dashboard_read_cache.forget()
    env.grafana.fail = True
    for _ in range(3):
        assert rules.dashboard_warnings(record)[0]["column"] is None  # non verificabile
    assert len(timeouts) == 2  # una sola prova per intervallo
    env.grafana.fail = False
    assert rules.dashboard_warnings(record)[0]["column"] is None  # ancora in cache
    rules.dashboard_read_cache.forget()
    assert rules.dashboard_warnings(record) == []
    grafana_client.set_client_factory(lambda: env.grafana)


def test_delete_dashboard_and_best_effort_on_eseguibile_deletion(env):
    from app.core.eseguibili import dashboard as rules

    _with_dashboard(env)
    assert _app("u-admin").delete("/api/eseguibili/exec_1/dashboard").status_code == 204
    assert env.records["exec_1"].dashboard_uid is None
    assert "plenora-exec_1" not in env.grafana.dashboards
    assert "plenora-e-exec_1" not in env.grafana.folders
    assert _app("u-admin").delete("/api/eseguibili/exec_1/dashboard").status_code == 404
    record = _with_dashboard(env)
    env.grafana.fail = True
    rules.delete_dashboard_best_effort(record)  # Grafana giu': nessuna eccezione
    env.grafana.fail = False
    rules.delete_dashboard_best_effort(record)
    assert "plenora-e-exec_1" not in env.grafana.folders


def test_grafana_down_is_503_and_nothing_is_bound(env):
    env.grafana.fail = True
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "grafana_unavailable"
    assert env.records["exec_1"].dashboard_uid is None


def test_referenced_columns_reads_every_place_a_dashboard_names_a_column():
    from app.core.grafana import dashboard_builder

    dashboard = {"panels": [
        {"targets": [{"columns": [{"selector": "a"}]}],
         "transformations": [{"id": "groupBy", "options": {"fields": {"b": {}}}}],
         "options": {"xField": "c", "reduceOptions": {"fields": "/^d\\.x$/"}},
         "fieldConfig": {"overrides": [{"matcher": {"id": "byName", "options": "e"}}]}},
        {"type": "row", "panels": [{"options": {"xField": "f"}}]},
    ]}
    assert dashboard_builder.referenced_columns(dashboard) == ["a", "b", "c", "d.x", "e", "f"]
    assert dashboard_builder.missing_columns(dashboard, ["a", "b", "c"]) == ["d.x", "e", "f"]
    assert len(dashboard_builder.folder_uid("exec_" + "g" * 80)) <= 40
    assert len(dashboard_builder.dashboard_uid("exec_" + "x" * 80)) <= 40


# ── GRAFANA_ENABLED=false: nessuna chiamata ─────────────────────────────────

def test_disabled_makes_no_grafana_call_anywhere(env, monkeypatch):
    import requests

    from app.core.eseguibili import dashboard as rules
    from app.core.grafana import client as grafana_client
    from app.core.grafana import settings as grafana_settings

    _with_dashboard(env)  # creata quando era attiva
    _run(env)
    env.grafana.calls.clear()
    monkeypatch.setenv("GRAFANA_ENABLED", "false")
    grafana_client.set_client_factory(None)  # client reale: nessuna factory finta

    def no_network(*_a, **_k):
        raise AssertionError("chiamata HTTP con GRAFANA_ENABLED=false")

    monkeypatch.setattr(requests.Session, "request", no_network)
    assert not grafana_settings.enabled()
    with pytest.raises(grafana_client.GrafanaDisabled):
        grafana_client.get_client()

    admin, executor = _app("u-admin"), _app("u-exec")
    assert admin.post("/api/eseguibili/exec_1/dashboard").json()["detail"]["error"] == "grafana_disabled"
    assert admin.post("/api/eseguibili/exec_1/dashboard/edit-link").status_code == 400
    assert admin.delete("/api/eseguibili/exec_1/dashboard").status_code == 400
    assert executor.post("/api/eseguibili/exec_1/dashboard/pass", json={"task_id": "task-1"}).status_code == 400
    assert executor.post("/api/grafana/session").status_code == 400
    assert _enter_grafana("u-exec").status_code == 401
    client = _app()
    assert client.get("/api/dash/exec/task-1/rows", headers={"X-Grafana-Secret": DATA_SECRET}).status_code == 404
    rules.delete_dashboard_best_effort(env.records["exec_1"])
    rules.on_shares_changed("exec_1")
    rules.dashboard_read_cache.forget()
    assert rules.dashboard_warnings(env.records["exec_1"]) == []
    assert env.grafana.calls == []


def test_disabled_with_flag_but_no_token(env, monkeypatch):
    from app.core.grafana import settings as grafana_settings

    monkeypatch.setenv("GRAFANA_SERVICE_TOKEN", "")
    assert grafana_settings.flag_enabled() and not grafana_settings.enabled()


def test_secrets_are_env_only_and_rest_in_defaults():
    from app.config import Settings

    for secret in ("DASHBOARD_PASS_SECRET", "GRAFANA_DATA_SECRET", "GRAFANA_SERVICE_TOKEN"):
        assert secret not in Settings.DEFAULTS
    # Acceso di default: senza GRAFANA_SERVICE_TOKEN (scritto solo
    # dall'installer con -InstallGrafana) la funzione resta spenta.
    assert Settings.DEFAULTS["GRAFANA_ENABLED"] is True
    assert Settings.DEFAULTS["GRAFANA_URL"] == "http://127.0.0.1:3300/grafana"
    assert Settings.DEFAULTS["DASHBOARD_DATA_MAX_ROWS"] == 50_000
    assert Settings.DEFAULTS["DASHBOARD_DATA_TTL_SECONDS"] == 1800
    assert Settings.DEFAULTS["DASHBOARD_DATA_PURGE_INTERVAL_SECONDS"] == 300
    assert Settings.DEFAULTS["GRAFANA_WARNINGS_TIMEOUT_SECONDS"] == 1.5
    assert Settings.DEFAULTS["GRAFANA_ACCESS_SYNC_TTL_SECONDS"] == 300


def test_env_example_describes_the_real_default():
    """Punto 10: ``.env.example`` dice il default vero (true, attiva solo col
    token) e mostra come spegnerla."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    block = text[text.index("DASHBOARD GRAFANA DEGLI ESEGUIBILI"):text.index("RATE LIMIT LOGIN")]
    assert "DEFAULTS: false" not in block
    assert "true nei DEFAULTS" in block and "# GRAFANA_ENABLED=false" in block
    assert "GRAFANA_ENABLED=true" not in block


def test_migration_is_additive():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[3] / "migrations" / "2026-11-11_eseguibili_dashboard.sql").read_text(
        encoding="utf-8")
    statements = [line for line in sql.splitlines() if line.strip() and not line.strip().startswith("--")]
    assert " ".join(statements).split() == [
        "ALTER", "TABLE", "eseguibili", "ADD", "COLUMN", "IF", "NOT", "EXISTS", "dashboard_uid", "TEXT;"]


def test_routes_status_codes():
    from app.core.eseguibili import dashboard_router
    from tests2.shared._route_status_helpers import (
        _assert_delete_route_status_204,
        _assert_post_route_status_201,
    )

    _assert_post_route_status_201(dashboard_router.router, "/eseguibili/{eseguibile_id}/dashboard", "dashboard")
    _assert_delete_route_status_204(dashboard_router.router, "/eseguibili/{eseguibile_id}/dashboard", "dashboard")


def test_summary_hides_uid_from_who_cannot_edit(env):
    from app.core.eseguibili import router as eseguibili_router

    record = _with_dashboard(env)
    assert eseguibili_router._dashboard_fields(record, False) == {
        "has_dashboard": True, "dashboard_uid": None, "dashboard_feature_enabled": True}
    assert eseguibili_router._dashboard_fields(record, True)["dashboard_uid"] == "plenora-exec_1"


def test_json_rows_are_array_of_objects(env):
    _with_dashboard(env)
    _run(env, rows=1)
    body = _rows(_app(), "task-1", _pass_for(env)).content
    assert isinstance(json.loads(body), list) and isinstance(json.loads(body)[0], dict)


def test_default_without_token_is_off(monkeypatch):
    """Default acceso + nessun token (server senza Grafana) = spenta."""
    from app.core.grafana import settings as grafana_settings

    monkeypatch.delenv("GRAFANA_ENABLED", raising=False)
    monkeypatch.setenv("GRAFANA_SERVICE_TOKEN", "")
    assert grafana_settings.flag_enabled() and not grafana_settings.enabled()


# ── Log di accesso: il lasciapassare non ci finisce (punto 4) ───────────────

def test_access_log_masks_the_pass_in_forwarded_grafana_queries():
    """Il forward_auth di Caddy inoltra a /api/grafana/auth la query della
    pagina Grafana (``var-pass=...``, provato dal vivo): il log di accesso di
    uvicorn la scrive oscurata. Il formatter vero di uvicorn spacchetta
    ``record.args``: il filtro riscrive solo il path."""
    import inspect
    import logging

    from uvicorn.logging import AccessFormatter

    from app import app_factory
    from app.utils import logger as app_logger

    token = "eyJhbGciOi.firma_segreta-123"
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/api/grafana/auth?orgId=1&kiosk&var-exec=t1&var-pass={token}&pass={token}",
         "1.1", 200), None,
    )
    assert app_logger.AccessLogRedactionFilter().filter(record) is True
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert token not in line
    assert "var-exec=t1" in line and "var-pass=***REDACTED***" in line and "&pass=***REDACTED***" in line
    # Non tocca path senza query sensibili e lascia passare gli altri record.
    assert app_logger.redact_access_path("/api/eseguibili/x?passaggio=1") == "/api/eseguibili/x?passaggio=1"
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "testo", None, None)
    assert app_logger.AccessLogRedactionFilter().filter(other) is True
    # Installato da create_app, idempotente.
    app_logger.install_access_log_redaction()
    app_logger.install_access_log_redaction()
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, app_logger.AccessLogRedactionFilter) for f in access.filters) == 1
    assert "install_access_log_redaction()" in inspect.getsource(app_factory.create_app)
