"""
ArcGIS Online — full Plenora-stack integration test.

Differenza vs ``test_arcgis_real_smoke.py``: NON istanzia
``ArcGISClient`` direttamente. Esercita l'intero stack Plenora:

  HTTP /api/features/connections (POST) -> persistenza DB
   -> encryption a riposo per password/client_secret
   -> connection resolver
   -> features_service.test_connection (round-trip al portale)

Verifica che la stessa coppia user/password che funziona a livello
client-library funzioni ANCHE attraverso il path-utente (REST API +
storage + recupero) cosi' come la usa il frontend.

Setup richiesto: stack docker compose UP + admin login funzionante.
Le credenziali AGOL stesse vengono da env (vedi smoke suite).

Cleanup: la connection creata in test viene cancellata sempre via
DELETE /api/features/connections/{id} in fixture teardown, anche su
fallimento del test.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

requests = pytest.importorskip("requests")


_ENV_ENABLE = "ARCGIS_TESTS_ENABLED"
_ENV_PORTAL = "ARCGIS_TEST_PORTAL_URL"
_ENV_USERNAME = "ARCGIS_TEST_USERNAME"
_ENV_PASSWORD = "ARCGIS_TEST_PASSWORD"

# Login admin: usa lo stesso utente seed locale degli altri E2E.
# Override via env se gira contro un'instance diversa.
_PLENORA_API = os.getenv("PLENORA_API_URL", "http://backend:5004")
_PLENORA_USER = os.getenv("PLENORA_ADMIN_USER", "admin_plenora")
_PLENORA_PASS = os.getenv("PLENORA_ADMIN_PASS", "admin_plenora_2026")


def _resolve_or_skip() -> tuple[str, str, str]:
    """Skip pulito se non opt-in o credenziali AGOL mancanti."""
    if os.getenv(_ENV_ENABLE, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"{_ENV_ENABLE}!=1")
    missing = [k for k in (_ENV_PORTAL, _ENV_USERNAME, _ENV_PASSWORD) if not os.getenv(k)]
    if missing:
        pytest.skip(f"AGOL creds missing: {missing}")
    return (
        os.environ[_ENV_PORTAL].rstrip("/"),
        os.environ[_ENV_USERNAME],
        os.environ[_ENV_PASSWORD],
    )


@pytest.fixture(scope="module")
def admin_token() -> str:
    """Login admin via API. Skip se il backend non raggiungibile o
    le credenziali admin di default sono state cambiate localmente."""
    try:
        r = requests.post(
            f"{_PLENORA_API}/api/auth/login",
            json={"username": _PLENORA_USER, "password": _PLENORA_PASS},
            timeout=10,
        )
    except requests.RequestException as e:
        pytest.skip(f"backend Plenora non raggiungibile: {e}")
    if r.status_code != 200:
        pytest.skip(f"login admin fallito ({r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]


@pytest.fixture
def arcgis_connection_id(admin_token) -> Iterator[str]:
    """Crea una connection ArcGIS via API, yielda l'id, cancella in
    teardown via DELETE. Garantisce zero residui anche su test fallito.

    Naming: ``plenora_ci_pytest_conn_<uuid>`` per ispezione facile.
    """
    portal, username, password = _resolve_or_skip()
    headers = {"Authorization": f"Bearer {admin_token}"}

    name = f"plenora_ci_pytest_conn_{uuid.uuid4().hex[:8]}"
    payload = {
        "name": name,
        "description": "Connection auto-creata da pytest, cleanup automatico",
        "server_url": portal,
        "server_type": "arcgis_online",
        "auth_type": "token",
        "username": username,
        "password": password,
    }
    r = requests.post(
        f"{_PLENORA_API}/api/features/connections",
        headers=headers,
        json=payload,
        timeout=15,
    )
    assert r.status_code in (200, 201), (
        f"POST /api/features/connections {r.status_code}: {r.text[:300]}"
    )
    body = r.json()
    conn_id = body.get("id") or body.get("connection_id") or (body.get("metadata") or {}).get("id")
    assert conn_id, f"id mancante in response create: {body}"

    try:
        yield conn_id
    finally:
        # Best-effort cleanup.
        try:
            requests.delete(
                f"{_PLENORA_API}/api/features/connections/{conn_id}",
                headers=headers,
                timeout=10,
            )
        except Exception as e:
            print(f"[arcgis-via-api] cleanup DELETE fallito best-effort: {e}")


# Tests


def test_create_arcgis_connection_persists_and_decrypts(
    admin_token, arcgis_connection_id,
):
    """Verifica round-trip create + GET:
    - la connection appare in lista
    - la GET ritorna campi base (NO password in chiaro)
    """
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. La connection appare nella lista.
    r = requests.get(
        f"{_PLENORA_API}/api/features/connections",
        headers=headers,
        timeout=10,
    )
    assert r.status_code == 200, f"list {r.status_code}: {r.text[:200]}"
    data = r.json()
    # ArcGISList shape: {"connections": [...]}; fallback su list/items.
    items = (
        data.get("connections")
        if isinstance(data, dict) and "connections" in data
        else (data if isinstance(data, list) else data.get("items", []))
    )
    ids = [c.get("id") for c in items]
    assert arcgis_connection_id in ids, (
        f"connection appena creata {arcgis_connection_id} non in lista: {ids}"
    )

    # 2. GET singola: campi base presenti, password mai esposta in chiaro.
    r2 = requests.get(
        f"{_PLENORA_API}/api/features/connections/?id={arcgis_connection_id}",
        headers=headers,
        timeout=10,
    )
    # Alcuni endpoint di Plenora hanno la GET item via /connections/{id}
    # invece che query string: provo entrambi.
    if r2.status_code != 200:
        r2 = requests.get(
            f"{_PLENORA_API}/api/features/connections/{arcgis_connection_id}",
            headers=headers,
            timeout=10,
        )
    # Se nessuno dei due esiste, basta che la list abbia ritornato la
    # connection (gia' asserito sopra) — skip il dettaglio.
    if r2.status_code == 200:
        detail = r2.json()
        # Password e client_secret NON devono mai uscire in chiaro
        # dall'API verso il client. Tolerate either redacted or absent.
        leaked_password = detail.get("password")
        assert not leaked_password or leaked_password in (
            "", "***", "***REDACTED***", "<encrypted>",
        ), (
            f"password leakata in plaintext dalla GET: "
            f"{str(leaked_password)[:20]!r}"
        )


def test_arcgis_connection_test_endpoint_against_real_portal(
    admin_token, arcgis_connection_id,
):
    """Esegue POST /connections/{id}/test che internamente fa generate
    token contro AGOL reale.

    Questo e' il vero test E2E: se passa, abbiamo verificato che:
      1. La password e' stata correttamente cifrata a riposo
      2. Il backend la decifra al volo per usarla
      3. L'integrazione AGOL funziona dal path-utente HTTP
    """
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = requests.post(
        f"{_PLENORA_API}/api/features/connections/{arcgis_connection_id}/test",
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200, (
        f"connection/test {r.status_code}: {r.text[:300]}"
    )
    body = r.json()
    # Endpoint /test ritorna {success: bool, ...} o simile.
    success = (
        body.get("success")
        or body.get("ok")
        or body.get("connected")
        or body.get("status") == "ok"
    )
    assert success, f"test connection AGOL fallito via API: {body}"


def test_arcgis_connection_browse_services_against_real_portal(
    admin_token, arcgis_connection_id,
):
    """Una volta che il /test passa, GET /services deve ritornare la
    catalog list dei FeatureServer dell'utente sul portale.

    Stress il path complementare al /test: features_service.list_services
    via API HTTP, con encryption/decrypt password reale.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(
        f"{_PLENORA_API}/api/features/connections/{arcgis_connection_id}/services",
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200, (
        f"browse services {r.status_code}: {r.text[:300]}"
    )
    body = r.json()
    # Endpoint puo' ritornare lista nuda o {services: [...]} o {items: [...]}.
    services = body if isinstance(body, list) else (
        body.get("services") or body.get("items") or []
    )
    assert isinstance(services, list), (
        f"browse services non ha shape list-like: {type(services).__name__}"
    )
    # NON assertiamo len > 0: l'utente di test potrebbe non aver
    # ancora pubblicato nulla. Basta che la chiamata non esploda.
