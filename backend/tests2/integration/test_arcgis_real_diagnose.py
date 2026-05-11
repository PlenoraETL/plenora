"""
ArcGIS Online — diagnostic probe sul token client_credentials.

Scopo: rispondere a 3 domande quando le scritture danno 403:
  1. Chi e' l'utente che il token rappresenta? (community/self)
  2. Su quale org? (portals/self.id)
  3. Quali privileges ha quel ruolo?

Non e' un test di regressione: e' uno **strumento di diagnostica**
che gira solo on-demand. Per questo skip-by-default a meno
dell'opt-in env, come la suite real-smoke.
"""

from __future__ import annotations

import json
import os

import pytest


_ENV_PORTAL = "ARCGIS_TEST_PORTAL_URL"
_ENV_OWNER = "ARCGIS_TEST_OWNER"
_ENV_USERNAME = "ARCGIS_TEST_USERNAME"
_ENV_PASSWORD = "ARCGIS_TEST_PASSWORD"
_ENV_CLIENT_ID = "ARCGIS_TEST_CLIENT_ID"
_ENV_CLIENT_SECRET = "ARCGIS_TEST_CLIENT_SECRET"
_ENV_ENABLE = "ARCGIS_TESTS_ENABLED"


def _build_conn_or_skip():
    """Costruisce ArcGISConnectionInfo, preferendo user/password (che
    sblocca i path content/users/<X>) e fallback su oauth2 client_creds.
    Skippa se nessuno dei due gruppi e' configurato."""
    if not os.getenv(_ENV_PORTAL) or not os.getenv(_ENV_OWNER):
        pytest.skip("ARCGIS_TEST_PORTAL_URL / ARCGIS_TEST_OWNER missing")
    if os.getenv(_ENV_ENABLE, "").lower() not in {"1", "true", "yes"}:
        pytest.skip("ARCGIS_TESTS_ENABLED!=1")

    from app.core.features.arcgis_client import ArcGISConnectionInfo

    portal = os.environ[_ENV_PORTAL].rstrip("/")
    owner = os.environ[_ENV_OWNER]
    username = os.getenv(_ENV_USERNAME) or owner
    password = os.getenv(_ENV_PASSWORD)

    if password:
        return ArcGISConnectionInfo(
            server_url=portal,
            server_type="arcgis_online",
            auth_type="token",
            username=username,
            password=password,
        ), "token (user/password)", owner
    cid = os.getenv(_ENV_CLIENT_ID)
    csec = os.getenv(_ENV_CLIENT_SECRET)
    if cid and csec:
        return ArcGISConnectionInfo(
            server_url=portal,
            server_type="arcgis_online",
            auth_type="oauth2",
            username=owner,
            client_id=cid,
            client_secret=csec,
        ), "oauth2 (client_credentials)", owner
    pytest.skip("Ne user/password ne client_credentials configurati")


def test_diagnose_token_identity_and_privileges(capsys):
    """Probe i 3 endpoint introspettivi e stampa il riassunto a stdout.

    Non e' un assert classico di pass/fail: l'output capsys e' poi
    visibile col flag pytest ``-s``. Il test e' "PASS" solo se i 3
    endpoint introspettivi rispondono — se danno 403, fail con report
    dettagliato che indica precisamente cosa manca.
    """
    conn, auth_label, owner = _build_conn_or_skip()
    print(f"\n[diagnose] auth mode = {auth_label}")

    from app.core.features.arcgis_client import ArcGISClient
    portal = conn.server_url.rstrip("/")

    client = ArcGISClient()
    token = client.authenticate(conn)
    assert token

    import requests

    base = f"{portal}/sharing/rest"
    headers = {"Authorization": f"Bearer {token}"}

    report = {}

    # 1. community/self -> chi e' il token holder
    r = requests.get(
        f"{base}/community/self",
        params={"f": "json"},
        headers=headers,
        timeout=15,
    )
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    report["community_self"] = {
        "http_status": r.status_code,
        "username": j.get("username"),
        "fullName": j.get("fullName"),
        "role": j.get("role"),
        "roleId": j.get("roleId"),
        "userLicenseTypeId": j.get("userLicenseTypeId"),
        "userType": j.get("userType"),
        "privileges": j.get("privileges"),
        "error": j.get("error"),
    }

    # 2. portals/self -> org/portal context
    r = requests.get(
        f"{base}/portals/self",
        params={"f": "json"},
        headers=headers,
        timeout=15,
    )
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    report["portals_self"] = {
        "http_status": r.status_code,
        "id": j.get("id"),
        "name": j.get("name"),
        "urlKey": j.get("urlKey"),
        "isPortal": j.get("isPortal"),
        "user_username": (j.get("user") or {}).get("username") if isinstance(j.get("user"), dict) else None,
        "subscriptionInfo_type": (j.get("subscriptionInfo") or {}).get("type") if isinstance(j.get("subscriptionInfo"), dict) else None,
        "error": j.get("error"),
    }

    # 3. content/users/<owner>  -> il path che da' 403 sui write
    r = requests.get(
        f"{base}/content/users/{owner}",
        params={"f": "json"},
        headers=headers,
        timeout=15,
    )
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    report["content_users_owner"] = {
        "http_status": r.status_code,
        "username": j.get("username"),
        "total": j.get("total"),
        "num_items": len(j.get("items") or []),
        "num_folders": len(j.get("folders") or []),
        "error": j.get("error"),
    }

    print("\n=== ArcGIS diagnose report ===")
    print(json.dumps(report, indent=2, default=str))

    # Verdict
    cs = report["community_self"]
    cu = report["content_users_owner"]

    diagnose_msg_parts = []
    if cs.get("error"):
        diagnose_msg_parts.append(
            f"community/self error: {cs['error']}"
        )
    elif cs.get("username") and cs["username"] != owner:
        diagnose_msg_parts.append(
            f"Token holder username e' '{cs['username']}', ma "
            f"ARCGIS_TEST_OWNER='{owner}'. Imposta "
            f"ARCGIS_TEST_OWNER='{cs['username']}'."
        )
    if cu.get("error"):
        diagnose_msg_parts.append(
            f"content/users/{owner} 403: {cu['error']}"
        )

    privileges = cs.get("privileges") or []
    publish_privs = [
        p for p in privileges
        if isinstance(p, str) and (
            "publish" in p.lower()
            or "createItem" in p
            or "portal:publisher" in p
        )
    ]
    if isinstance(privileges, list) and not publish_privs:
        diagnose_msg_parts.append(
            f"L'utente NON ha privilegi 'publish*' / 'createItem'. "
            f"Lista privileges trovata: {privileges}. "
            f"User type='{cs.get('userType')}', role='{cs.get('role')}'."
        )

    if diagnose_msg_parts:
        pytest.fail(
            "Diagnose ha rilevato problemi che spiegano i 403 sulle scritture:\n  - "
            + "\n  - ".join(diagnose_msg_parts)
            + "\n(Vedi report JSON sopra per dettaglio completo.)"
        )
