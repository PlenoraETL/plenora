"""Dashboard Grafana degli Eseguibili: regole, endpoint, sicurezza (11/11/2026).

Raccolta: ``docs/audits/grafana-dashboard-raccolta-2026-11-11.md``. Grafana e'
SEMPRE finto (``client.set_client_factory``): nessuna rete. Copre:
lasciapassare != login != sessione; forward_auth (cookie assente, scaduto,
utente disattivato o eliminato, niente ruolo ai non superadmin); endpoint
dati (segreto, path, lasciapassare di altro task, scaduto, 410 dopo la
pulizia, limite righe, frequenza); gestione (crea, elimina, link, avvisi,
cartella riservata, sincronizzazione del team); con ``GRAFANA_ENABLED=false``
nessuna chiamata a Grafana in nessun percorso.
"""

from __future__ import annotations

import dataclasses
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


# ── Grafana finto ───────────────────────────────────────────────────────────

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

    def get_folder(self, uid):
        self._log("get_folder", uid)
        return self.folders.get(uid)

    def create_folder(self, uid, title):
        self._log("create_folder", uid)
        self.folders[uid] = {"uid": uid, "title": title}
        return self.folders[uid]

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
        login = next(login for login, u in self.users.items() if u["userId"] == user_id)
        self.members.setdefault(team_id, {})[login] = user_id

    def remove_team_member(self, team_id, user_id):
        self._log("remove_team_member", team_id, user_id)
        self.members[team_id] = {k: v for k, v in self.members[team_id].items() if v != user_id}

    def org_users(self):
        self._log("org_users")
        return [dict(u) for u in self.users.values()]

    def set_org_role(self, user_id, role):
        self._log("set_org_role", user_id, role)
        for user in self.users.values():
            if user["userId"] == user_id:
                user["role"] = role

    def ensure_proxy_user(self, login, *, admin):
        self._log("ensure_proxy_user", login, admin)
        self.users.setdefault(login, {"login": login, "userId": len(self.users) + 100,
                                      "role": "Admin" if admin else "Viewer"})

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
}


@pytest.fixture
def env(monkeypatch):
    from app.core.auth import db as users_db
    from app.core.eseguibili import dashboard as rules
    from app.core.eseguibili import dashboard_router, storage
    from app.core.eseguibili import db as eseguibili_db
    from app.core.eseguibili.db import EseguibileRow
    from app.core.grafana import client as grafana_client

    monkeypatch.setenv("GRAFANA_ENABLED", "true")
    monkeypatch.setenv("GRAFANA_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("DASHBOARD_PASS_SECRET", PASS_SECRET)
    monkeypatch.setenv("GRAFANA_DATA_SECRET", DATA_SECRET)

    fake_storage = _FakeStorage()
    monkeypatch.setattr(storage, "_storage", lambda: fake_storage)
    grafana = FakeGrafana()
    grafana_client.set_client_factory(lambda: grafana)

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
    }
    users = dict(USERS)
    monkeypatch.setattr(eseguibili_db, "get_eseguibile", lambda eid, group_id=None: records.get(eid))

    def set_uid(eid, uid):
        records[eid] = dataclasses.replace(records[eid], dashboard_uid=uid)
        return records[eid]

    monkeypatch.setattr(eseguibili_db, "set_dashboard_uid", set_uid)
    monkeypatch.setattr(users_db, "get_user", lambda uid: (u if (u := users.get(uid)) else None))
    monkeypatch.setattr(users_db, "get_user_including_deleted", lambda uid: users.get(uid))
    monkeypatch.setattr(users_db, "list_users", lambda gid, limit=200, offset=0: [
        u for u in users.values() if u.group_id == gid])
    monkeypatch.setattr(users_db, "list_all_users", lambda limit=200, offset=0: list(users.values())[offset:offset + limit])
    monkeypatch.setattr(users_db, "get_group", lambda gid: SimpleNamespace(id=gid, name=f"Gruppo {gid}"))
    contract = {"columns": [
        {"physical_name": "istituto", "type": {"logical": "string"}, "ordinal": 0},
        {"physical_name": "importo", "type": {"logical": "number"}, "ordinal": 1},
        {"physical_name": "geom", "type": {"logical": "geometry"}, "ordinal": 2},
    ]}
    monkeypatch.setattr(rules.ProjectOutputContractService, "get_project_output_contract",
                        lambda self, pid: contract)
    monkeypatch.setattr(dashboard_router, "assert_resource_visible", lambda *a, **k: None)
    dashboard_router.rate_limit.reset()
    yield SimpleNamespace(grafana=grafana, storage=fake_storage, records=records, users=users, contract=contract)
    grafana_client.set_client_factory(None)


def _app(user_id=None):
    from app.core.auth import dependencies
    from app.core.auth.grafana_router import grafana_router
    from app.core.eseguibili import dashboard_router
    from app.shared.contracts.auth import CurrentUser

    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api")
    app.include_router(dashboard_router.data_router, prefix="/api")
    app.include_router(grafana_router, prefix="/api")
    if user_id:
        row = USERS[user_id]
        current = CurrentUser(user_id=row.id, username=row.username, group_id=row.group_id, role=row.role)
        app.dependency_overrides[dependencies.get_current_user] = lambda: current
        app.dependency_overrides[dependencies.require_admin] = lambda: dependencies.require_admin(current)
    return TestClient(app, raise_server_exceptions=False)


def _run(env, task_id="task-1", user_id="u-exec", eseguibile_id="exec_1", rows=3, now=None):
    """Un'esecuzione riuscita con copia per la dashboard."""
    from app.core.eseguibili import dashboard_data, output_store

    output_store.save_task_owner(task_id, eseguibile_id, user_id, group_id="g1")
    env.storage.objects[f"tenants/g1/eseguibili-outputs/{task_id}/report.csv"] = b"x"
    collector = dashboard_data.DashboardRowsCollector()
    collector.add_rows(["istituto", "importo"], [(f"IST{i}", i * 1.5) for i in range(rows)])
    dashboard_data.write(task_id, group_id="g1", collector=collector, now=now)


def _with_dashboard(env, eseguibile_id="exec_1"):
    from app.core.eseguibili import dashboard as rules

    env.records[eseguibile_id] = rules.create_dashboard(
        env.records[eseguibile_id], SimpleNamespace(user_id="u-admin", username="admin1", group_id="g1", role="admin"),
    )
    return env.records[eseguibile_id]


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


# ── forward_auth ────────────────────────────────────────────────────────────

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


@pytest.mark.parametrize("cookie_user,expected_status,user_header,role_header", [
    (None, 401, None, None),            # cookie assente
    ("u-exec", 200, "esecutore1", None),  # nessun ruolo: Viewer in Grafana
    ("u-admin", 200, "admin1", None),     # admin di gruppo: nessun ruolo (Edit dal team)
    ("u-super", 200, "capo", "Admin"),
    ("u-off", 401, None, None),           # disattivato
    ("u-gone", 401, None, None),          # eliminato (lapide: get_user non la vede)
])
def test_forward_auth(env, cookie_user, expected_status, user_header, role_header):
    client = _app()
    if cookie_user:
        client.cookies.set("plenora_grafana_session", _session_cookie(cookie_user))
    response = client.get("/api/grafana/auth")
    assert response.status_code == expected_status
    assert response.headers.get("x-webauth-user") == user_header
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


# ── Endpoint dati ───────────────────────────────────────────────────────────

def _pass_for(env, task_id="task-1", user_id="u-exec", eseguibile_id="exec_1"):
    from app.core.eseguibili import dashboard as rules
    from app.shared.contracts.auth import CurrentUser

    row = USERS[user_id]
    user = CurrentUser(user_id=row.id, username=row.username, group_id=row.group_id, role=row.role)
    return rules.issue_pass(env.records[eseguibile_id], task_id, user)["pass_token"]


def _rows(client, task_id, token, secret=DATA_SECRET):
    headers = {"X-Grafana-Secret": secret} if secret else {}
    return client.get(f"/api/dash/exec/{task_id}/rows", params={"pass": token}, headers=headers)


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
    for bad in ("a..b", "..", "x%2F..%2Fy", "t%00"):
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


def test_data_endpoint_row_limit_and_rate_limit(env, monkeypatch):
    from app.core.eseguibili import dashboard_router

    monkeypatch.setenv("DASHBOARD_DATA_MAX_ROWS", "5")
    monkeypatch.setenv("DASHBOARD_DATA_RATE_PER_MINUTE", "3")
    _with_dashboard(env)
    _run(env, rows=12)
    token = _pass_for(env)
    client = _app()
    assert len(_rows(client, "task-1", token).json()) == 5
    assert _rows(client, "task-1", token).status_code == 200
    assert _rows(client, "task-1", token).status_code == 200
    assert _rows(client, "task-1", token).status_code == 429
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

def test_create_builds_dashboard_in_group_folder_and_syncs_team(env):
    env.grafana.users["admin2"] = {"login": "admin2", "userId": 7, "role": "Viewer"}
    env.grafana.users["ex-admin"] = {"login": "ex-admin", "userId": 8, "role": "Viewer"}
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard")
    assert response.status_code == 201
    info = response.json()
    assert info["dashboard_uid"] == "plenora-exec_1"
    assert env.records["exec_1"].dashboard_uid == "plenora-exec_1"
    saved = env.grafana.dashboards["plenora-exec_1"]
    assert saved["folder"] == "plenora-g-g1"
    dashboard = saved["dashboard"]
    names = [v["name"] for v in dashboard["templating"]["list"]]
    assert set(names) == {"exec", "pass"} and all(v["hide"] == 2 for v in dashboard["templating"]["list"])
    query = dashboard["panels"][0]["targets"][0]
    assert "pass" not in query["url"] and "${exec}" in query["url"]
    assert query["url_options"]["params"] == [{"key": "pass", "value": "${pass}"}]
    assert query["url"].startswith("http://127.0.0.1:5004/api/dash/exec/")
    assert [c["selector"] for c in query["columns"]] == ["istituto", "importo"]  # geometria esclusa
    shared = [p for p in dashboard["panels"][1:]]
    assert {p["type"] for p in shared} == {"stat", "barchart"}
    assert all(p["datasource"]["uid"] == "-- Dashboard --" for p in shared)
    team = env.grafana.teams["admin-g1"]
    assert set(env.grafana.members[team]) == {"admin1", "admin2"}
    assert {"teamId": team, "permission": 2} in env.grafana.permissions["plenora-g-g1"]
    assert {"role": "Viewer", "permission": 1} in env.grafana.permissions["plenora-g-g1"]
    assert all("teamId" not in item for item in env.grafana.permissions["plenora-g-g1-riservata"])
    assert _app("u-admin").post("/api/eseguibili/exec_1/dashboard").status_code == 409


def test_superadmin_created_eseguibile_goes_to_reserved_folder_and_admin_cannot_manage(env):
    assert _app("u-admin").post("/api/eseguibili/exec_super/dashboard").status_code == 403
    assert _app("u-super").post("/api/eseguibili/exec_super/dashboard").status_code == 201
    assert env.grafana.dashboards["plenora-exec_super"]["folder"] == "plenora-g-g1-riservata"
    assert _app("u-admin").post("/api/eseguibili/exec_super/dashboard/edit-link").status_code == 403
    assert _app("u-exec").post("/api/eseguibili/exec_1/dashboard").status_code == 403


def test_team_sync_removes_who_is_no_longer_admin(env):
    _with_dashboard(env)
    team = env.grafana.teams["admin-g1"]
    assert "admin2" in env.grafana.members[team]
    env.users["u-admin2"] = dataclasses.replace(env.users["u-admin2"], role="user")  # cambia ruolo
    response = _app("u-admin").post("/api/eseguibili/exec_1/dashboard/edit-link")
    assert response.status_code == 200
    assert set(env.grafana.members[team]) == {"admin1"}
    assert response.json()["edit_path"] == "/grafana/d/plenora-exec_1?orgId=1"


def test_demoted_superadmin_loses_grafana_admin(env):
    env.grafana.users["capo"] = {"login": "capo", "userId": 9, "role": "Admin"}
    env.users["u-super"] = dataclasses.replace(env.users["u-super"], role="admin")
    _with_dashboard(env)
    assert env.grafana.users["capo"]["role"] == "Viewer"


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
    env.grafana.fail = True
    assert rules.dashboard_warnings(record)[0]["column"] is None


def test_delete_dashboard_and_best_effort_on_eseguibile_deletion(env):
    from app.core.eseguibili import dashboard as rules

    _with_dashboard(env)
    assert _app("u-admin").delete("/api/eseguibili/exec_1/dashboard").status_code == 204
    assert env.records["exec_1"].dashboard_uid is None
    assert "plenora-exec_1" not in env.grafana.dashboards
    assert _app("u-admin").delete("/api/eseguibili/exec_1/dashboard").status_code == 404
    env.grafana.fail = True
    rules.delete_dashboard_best_effort("plenora-exec_1")  # Grafana giu': nessuna eccezione


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
    assert len(dashboard_builder.folder_uid("g" * 80, reserved=True)) <= 40
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
    client = _app()
    client.cookies.set("plenora_grafana_session", _session_cookie("u-exec"))
    assert client.get("/api/grafana/auth").status_code == 401
    assert client.get("/api/dash/exec/task-1/rows", headers={"X-Grafana-Secret": DATA_SECRET}).status_code == 404
    rules.delete_dashboard_best_effort("plenora-exec_1")
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
    assert Settings.DEFAULTS["GRAFANA_ENABLED"] is False
    assert Settings.DEFAULTS["GRAFANA_URL"] == "http://127.0.0.1:3300/grafana"
    assert Settings.DEFAULTS["DASHBOARD_DATA_MAX_ROWS"] == 50_000
    assert Settings.DEFAULTS["DASHBOARD_DATA_TTL_SECONDS"] == 1800


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
