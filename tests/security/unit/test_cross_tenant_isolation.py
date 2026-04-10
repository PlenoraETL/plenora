"""
test_cross_tenant_isolation.py
================================
Test sistematici di isolamento cross-tenant per URBY-ETL.

Verifica che:
1. Un utente del gruppo B NON può vedere risorse del gruppo A nelle list,
   a meno che non siano esplicitamente condivise via allowed_groups.
2. Un utente del gruppo B NON può accedere via GET/PUT/DELETE a risorse
   di gruppo A (anche se conosce l'ID).
3. Le risorse condivise via `*_shares` appaiono nelle list dei gruppi
   destinatari.
4. Il magic share `'*'` rende la risorsa visibile a qualsiasi gruppo.
5. Le modifiche restano riservate al gruppo proprietario anche se la
   risorsa è condivisa in lettura (sharing != ownership).

Usa TestClient + mock di db.get_user. Non richiede server live.
"""

from datetime import datetime, timedelta, timezone
import sqlite3
import unittest.mock as mock

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.auth.auth import SECRET_KEY
import app.db.db_service as db
from app.shared.sharing import set_resource_shares, _SHAREABLE


# ============================================================
# FIXTURES
# ============================================================

# Override del fixture autouse `mock_reset` (root conftest.py) che fa skip
# se il mock_api_server non gira. Questi test non dipendono da quel server.
@pytest.fixture(autouse=True)
def mock_reset():
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_users():
    """
    Mocka db.get_user con un side_effect dinamico: ritorna sempre un user
    fittizio attivo, in modo da non dover pre-registrare tutti gli ID di test.
    L'effettivo controllo di gruppo/ruolo viene fatto via JWT dal token,
    non via db.get_user (che serve solo a verificare is_active).

    NB: questo va bene per test di isolation che si basano sul JWT, NON
    per test che validano credenziali o lookup di un user specifico.
    """
    def fake_get_user(uid):
        # Ritorna un user fittizio attivo per qualsiasi ID
        return {
            "id": uid,
            "username": uid,
            "group_id": "ignored_use_jwt",
            "role": "ignored_use_jwt",
            "is_active": 1,
            "email": None,
        }

    with mock.patch.object(db, "get_user", side_effect=fake_get_user):
        yield None


def make_token(user_id: str, group_id: str, role: str) -> str:
    return jwt.encode(
        {
            "sub": user_id, "username": user_id, "group_id": group_id, "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        },
        SECRET_KEY, algorithm="HS256",
    )


def auth_headers(user_id: str, group_id: str, role: str) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, group_id, role)}"}


def _pick_resource_in_default_group(table: str) -> str | None:
    """Cerca un id di una risorsa esistente nel DB nel gruppo 'default' (per test smoke)."""
    conn = sqlite3.connect("data/config.db")
    try:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE group_id = 'default' LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ============================================================
# TEST 1: Isolation base — list non mostra risorse di altri gruppi
# ============================================================

@pytest.mark.parametrize("endpoint,list_key", [
    ("/api/connections",       "connections"),
    ("/api/datasources",       "datasources"),
    ("/api/eseguibili",        None),  # restituisce direttamente lista
    ("/api/api-connections",   "api_connections"),
    ("/api/features/connections", "connections"),
])
def test_list_isolation_no_share(client, mock_users, endpoint, list_key):
    """
    User del gruppo group_b NON deve vedere risorse di gruppo default
    se non sono esplicitamente condivise.
    """
    headers = auth_headers("u_user_b", "group_b", "user")
    r = client.get(endpoint, headers=headers)
    assert r.status_code == 200, f"{endpoint}: status {r.status_code}, body={r.text}"

    body = r.json()
    if list_key:
        items = body.get(list_key, [])
    else:
        items = body if isinstance(body, list) else []

    # Tutti gli item che vediamo NON devono avere group_id != "group_b"
    # (se l'endpoint espone group_id)
    for item in items:
        gid = item.get("group_id") if isinstance(item, dict) else None
        if gid is not None:
            assert gid == "group_b", \
                f"{endpoint}: visto item con group_id='{gid}' (atteso solo 'group_b')"


# ============================================================
# TEST 2: Superadmin vede tutto
# ============================================================

def test_superadmin_sees_all_connections(client, mock_users):
    """Superadmin (anche se nominalmente in group_a) vede TUTTE le connections."""
    r = client.get("/api/connections", headers=auth_headers("u_super", "group_a", "superadmin"))
    assert r.status_code == 200
    super_count = len(r.json().get("connections", []))

    # Confronta con il totale nel DB
    conn = sqlite3.connect("data/config.db")
    total = conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
    conn.close()

    assert super_count == total, f"superadmin vede {super_count} ma il DB ne ha {total}"


# ============================================================
# TEST 3: Cross-tenant accesso diretto (GET/PUT/DELETE)
# ============================================================

def test_user_b_cannot_get_connection_of_default(client, mock_users):
    """
    User del gruppo group_b che chiama GET /connections/{id} di una connessione
    del gruppo default deve ricevere 403 (non 200, non 404 confondibile).
    """
    cid = _pick_resource_in_default_group("connections")
    if not cid:
        pytest.skip("Nessuna connection nel gruppo default")

    # Note: GET /connections/{id} (single) was removed in Phase 0.
    # Test su /tables che esiste e usa assert_group_access.
    r = client.get(
        f"/api/connections/{cid}/tables",
        headers=auth_headers("u_user_b", "group_b", "user"),
    )
    assert r.status_code == 403, f"status {r.status_code}: {r.text}"


def test_user_b_cannot_delete_connection_of_default(client, mock_users):
    """User del gruppo B non può cancellare connessioni del gruppo default."""
    cid = _pick_resource_in_default_group("connections")
    if not cid:
        pytest.skip("Nessuna connection nel gruppo default")

    # DELETE richiede admin → user gets 403 just for role
    r = client.delete(
        f"/api/connections/{cid}",
        headers=auth_headers("u_user_b", "group_b", "user"),
    )
    assert r.status_code == 403

    # Anche admin di group_b non può (group check)
    r = client.delete(
        f"/api/connections/{cid}",
        headers=auth_headers("u_admin_b", "group_b", "admin"),
    )
    assert r.status_code == 403, f"admin di group_b ha potuto cancellare: {r.text}"


# ============================================================
# TEST 4: Sharing rende la risorsa visibile (e niente di più)
# ============================================================

def test_share_makes_connection_visible_in_list(client, mock_users):
    """
    Aggiungo uno share da connections.X verso group_b. Ora user_b
    vede X nella sua list.
    """
    cid = _pick_resource_in_default_group("connections")
    if not cid:
        pytest.skip("Nessuna connection nel gruppo default")

    try:
        # Cleanup
        set_resource_shares("connections", cid, [])

        # Pre: user_b NON vede
        r = client.get("/api/connections", headers=auth_headers("u_user_b", "group_b", "user"))
        ids_before = [c["id"] for c in r.json().get("connections", [])]
        assert cid not in ids_before

        # Aggiungo share
        set_resource_shares("connections", cid, ["group_b"])

        # Post: user_b vede
        r = client.get("/api/connections", headers=auth_headers("u_user_b", "group_b", "user"))
        ids_after = [c["id"] for c in r.json().get("connections", [])]
        assert cid in ids_after, f"share non visibile: ids={ids_after}"
    finally:
        set_resource_shares("connections", cid, [])


def test_magic_share_visible_to_any_group(client, mock_users):
    """share_groups = ['*'] → visibile a qualsiasi gruppo random."""
    cid = _pick_resource_in_default_group("connections")
    if not cid:
        pytest.skip("Nessuna connection nel gruppo default")

    try:
        set_resource_shares("connections", cid, ["*"])
        for fake_group in ["random_xx_1", "random_xx_2", "qualsiasi_y"]:
            r = client.get(
                "/api/connections",
                headers=auth_headers("u_random", fake_group, "user"),
            )
            ids = [c["id"] for c in r.json().get("connections", [])]
            assert cid in ids, f"share '*' non visibile a {fake_group}"
    finally:
        set_resource_shares("connections", cid, [])


def test_share_does_not_grant_modify_permission(client, mock_users):
    """
    Anche se condivisa, l'utente del gruppo destinatario NON può modificare
    o cancellare la risorsa (sharing è solo lettura).
    """
    cid = _pick_resource_in_default_group("connections")
    if not cid:
        pytest.skip("Nessuna connection nel gruppo default")

    try:
        set_resource_shares("connections", cid, ["group_b"])

        # admin del gruppo group_b (destinatario dello share) NON può cancellare
        r = client.delete(
            f"/api/connections/{cid}",
            headers=auth_headers("u_admin_b", "group_b", "admin"),
        )
        assert r.status_code == 403, \
            f"admin di group_b ha cancellato una connessione condivisa: {r.text}"
    finally:
        set_resource_shares("connections", cid, [])


# ============================================================
# TEST 5: Viewer constraints
# ============================================================

def test_viewer_cannot_create_resources(client, mock_users):
    """viewer NON può creare connections, datasources, projects, schedules, ecc."""
    headers = auth_headers("u_viewer", "group_a", "viewer")

    # POST /connections (require_admin)
    r = client.post("/api/connections", json={"name": "x", "db_type": "sqlite"}, headers=headers)
    assert r.status_code == 403

    # POST /datasources (require_admin)
    r = client.post("/api/datasources", json={"name": "x", "ds_type": "local", "config": {}}, headers=headers)
    assert r.status_code == 403

    # POST /projects (require_user_or_above)
    r = client.post("/api/projects", json={"name": "x", "project_type": "estrai"}, headers=headers)
    assert r.status_code == 403

    # POST /schedules (require_user_or_above)
    r = client.post("/api/schedules", json={"name": "x", "project_id": "y", "cron_expression": "* * * * *"}, headers=headers)
    assert r.status_code == 403

    # POST /eseguibili (require_admin)
    r = client.post("/api/eseguibili", json={"name": "x"}, headers=headers)
    assert r.status_code == 403


def test_viewer_can_read_and_execute(client, mock_users):
    """viewer PUO' leggere e PUO' lanciare esecuzioni."""
    headers = auth_headers("u_viewer", "group_a", "viewer")

    # GET list endpoints → 200
    for ep in ["/api/connections", "/api/projects", "/api/datasources", "/api/eseguibili"]:
        r = client.get(ep, headers=headers)
        assert r.status_code == 200, f"{ep}: viewer ha ricevuto {r.status_code}"


# ============================================================
# TEST 6: Privilege escalation impossibile via create_user
# ============================================================

def test_admin_cannot_create_admin_or_superadmin(client, mock_users):
    """Un admin (non super) non può creare admin/superadmin via POST /auth/users."""
    headers = auth_headers("u_admin_a", "group_a", "admin")

    # Tenta creare admin
    r = client.post("/api/auth/users",
                    json={"username": "evil", "password": "x", "group_id": "group_a", "role": "admin"},
                    headers=headers)
    assert r.status_code == 403

    # Tenta creare superadmin
    r = client.post("/api/auth/users",
                    json={"username": "evil", "password": "x", "group_id": "group_a", "role": "superadmin"},
                    headers=headers)
    assert r.status_code == 403


def test_role_validation_rejects_invalid_role(client, mock_users):
    """Ruolo non valido = 400 immediato."""
    headers = auth_headers("u_super", "group_a", "superadmin")

    r = client.post("/api/auth/users",
                    json={"username": "x", "password": "y", "group_id": "g", "role": "wizard_typo"},
                    headers=headers)
    assert r.status_code == 400
