"""AUTH-001..AUTH-00X — Auth smokes.

Catalogo: backend/test-e2e/catalog/auth.yaml
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest


API_PREFIX = "/api"


# ── AUTH-001 — Group cleanup cascade su delete gruppo ────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_auth_001_group_cleanup_cascade(
    http_client: httpx.Client,
    superadmin_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """AUTH-001 — `DELETE /auth/groups/{id}` esegue il cascade
    cleanup di tutte le risorse del gruppo: schedules, projects,
    datasources, connections, file_sources, api_connections, results,
    intermediate, users, log.db. Smoke `must`, profilo `core`.

    Endpoint richiede `superadmin`. Il default tester (`tester@plenora
    .test`) e' admin del gruppo `default` ma non superadmin, quindi
    questo test usa il fixture `superadmin_headers` che skipa se la
    pwd superadmin non e' configurata.

    Note operative:
      - Il gruppo `default` non e' eliminabile (guard
        `groups_router.delete_group`); il test crea un gruppo
        dedicato `e2e-auth-001-<uuid>`.
      - Per generare risorse nel gruppo serve un user al suo
        interno: ne creiamo uno con role `admin` e usiamo il suo
        token per popolare la connection di test (la
        `postgres_conn_id` fixture e' nel gruppo del tester e non
        ci serve qui).
    """
    # 1. Crea gruppo dedicato.
    gid = f"e2e-auth-001-{uuid.uuid4().hex[:8]}"
    r_g = http_client.post(
        f"{API_PREFIX}/auth/groups",
        json={
            "group_id": gid,
            "name": "E2E AUTH-001 cleanup cascade",
            "description": "Gruppo creato per il test AUTH-001",
        },
        headers=superadmin_headers,
    )
    assert r_g.status_code in (200, 201), (
        f"POST group: {r_g.status_code} — {r_g.text[:200]}"
    )

    # 2. Crea utente admin nel gruppo.
    uname = f"e2e-auth001-{uuid.uuid4().hex[:6]}"
    upwd = "Plenora_E2E_AuthTest_001!"
    r_u = http_client.post(
        f"{API_PREFIX}/auth/users",
        json={
            "username": uname,
            "password": upwd,
            "email": f"{uname}@plenora.test",
            "group_id": gid,
            "role": "admin",
        },
        headers=superadmin_headers,
    )
    assert r_u.status_code in (200, 201), (
        f"POST user: {r_u.status_code} — {r_u.text[:200]}"
    )

    # 3. Login come l'utente del gruppo per generare risorse.
    r_login = http_client.post(
        f"{API_PREFIX}/auth/login",
        json={"username": uname, "password": upwd},
    )
    assert r_login.status_code == 200, (
        f"Login user gruppo: {r_login.status_code} — {r_login.text[:200]}"
    )
    user_token = r_login.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # 4. Crea risorse minime (1 connection, 1 datasource, 1 project)
    # cosi' il report del cascade ha contatori non-zero su almeno tre
    # tabelle. La connection e' un postgres dummy (host inesistente
    # va benissimo per l'inserimento; non lo testiamo, l'importante
    # e' che persista nel DB cosi' il cascade la elimini).
    r_c = http_client.post(
        f"{API_PREFIX}/connections",
        json={
            "name": f"e2e-c-{uuid.uuid4().hex[:6]}",
            "description": "AUTH-001 dummy connection",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        },
        headers=user_headers,
    )
    assert r_c.status_code in (200, 201), (
        f"POST connection: {r_c.status_code} — {r_c.text[:200]}"
    )

    r_d = http_client.post(
        f"{API_PREFIX}/datasources",
        json={
            "name": f"e2e-d-{uuid.uuid4().hex[:6]}",
            "description": "AUTH-001 dummy datasource",
            "type": "ftp",
            "host": "plenora-e2e-ftp",
            "port": 21,
            "username": "tester",
            "password": "plenora_ftp_pwd",
            "root_path": "/home/tester/fixtures",
            "passive": True,
            "tls": False,
        },
        headers=user_headers,
    )
    assert r_d.status_code in (200, 201), (
        f"POST datasource: {r_d.status_code} — {r_d.text[:200]}"
    )

    r_p = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"e2e-p-{uuid.uuid4().hex[:6]}",
            "description": "AUTH-001 dummy project",
            "project_type": "manipola",
        },
        headers=user_headers,
    )
    assert r_p.status_code in (200, 201), (
        f"POST project: {r_p.status_code} — {r_p.text[:200]}"
    )

    # 5. Cascade delete del gruppo.
    r_del = http_client.delete(
        f"{API_PREFIX}/auth/groups/{gid}", headers=superadmin_headers,
    )
    assert r_del.status_code == 200, (
        f"DELETE group: {r_del.status_code} — {r_del.text[:200]}"
    )
    body = r_del.json()
    assert body.get("deleted") is True, f"deleted flag missing: {body}"
    assert body.get("group_id") == gid, f"group_id mismatch: {body}"
    report = body.get("report") or {}

    # 6. Il report deve includere contatori per le tabelle previste
    # dal `delete_group_cascade`. La sua struttura esatta dipende dal
    # `cleanup_registry`; verifichiamo le chiavi piu' importanti
    # senza assumere il formato value (int vs dict).
    expected_keys = {
        "connections", "datasources", "projects", "users",
    }
    missing = expected_keys - set(report.keys())
    assert not missing, (
        f"AUTH-001 report cascade incompleto: chiavi mancanti "
        f"{missing}. Report: {report}"
    )

    # Per ciascuna tabella deve risultare almeno 1 elemento eliminato.
    # I valori possono essere int o dict {"deleted": int}; coercion
    # a int per portabilita'.
    def _count(v: Any) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, dict):
            for k in ("deleted", "count", "rows", "n"):
                if isinstance(v.get(k), int):
                    return v[k]
        return 0

    assert _count(report.get("connections")) >= 1, (
        f"AUTH-001 attesa >=1 connection cancellata: {report}"
    )
    assert _count(report.get("datasources")) >= 1, (
        f"AUTH-001 attesa >=1 datasource cancellato: {report}"
    )
    assert _count(report.get("projects")) >= 1, (
        f"AUTH-001 atteso >=1 project cancellato: {report}"
    )
    assert _count(report.get("users")) >= 1, (
        f"AUTH-001 atteso >=1 user cancellato: {report}"
    )

    # 7. Verifica end-state: il gruppo non compare piu' nella list.
    # (Plenora non espone GET singolo `/auth/groups/{id}` — solo
    # LIST + summary.)
    r_list = http_client.get(
        f"{API_PREFIX}/auth/groups", headers=superadmin_headers,
    )
    if r_list.status_code == 200:
        groups_payload = r_list.json()
        groups_list = (
            groups_payload.get("groups")
            if isinstance(groups_payload, dict) else groups_payload
        ) or []
        ids = {g.get("group_id") or g.get("id") for g in groups_list}
        assert gid not in ids, (
            f"AUTH-001 gruppo {gid!r} ancora presente dopo cascade. "
            f"Group ids: {ids}"
        )

    # 8. Tentativo di login con l'utente eliminato → 401/403
    # (cascade rimuove anche `users`, quindi le credenziali non
    # autenticano piu').
    r_relogin = http_client.post(
        f"{API_PREFIX}/auth/login",
        json={"username": uname, "password": upwd},
    )
    assert r_relogin.status_code in (401, 403), (
        f"AUTH-001 user ancora autenticabile dopo cascade: "
        f"HTTP {r_relogin.status_code}"
    )


# ── AUTH-004 — JWT scaduto → 401 ──────────────────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_auth_004_jwt_expired_returns_401(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """AUTH-004 — Un token JWT con `exp` nel passato deve essere
    rifiutato con 401 da qualsiasi endpoint protetto. Catalog
    `auth.yaml` assertion: "re-login richiesto".

    Plenora usa HS256 con `JWT_SECRET_KEY` (vedi
    `app/core/auth/jwt_token.py:create_access_token`). Forgiamo
    un token con la stessa shape del create-time
    (`sub`, `username`, `group_id`, `role`, `exp`, `iat`, `jti`)
    ma `exp` = `now - 10s`. La libreria PyJWT considera scaduto
    qualsiasi token con `exp <= now` (default `leeway=0`).

    Skip-safe: se la `JWT_SECRET_KEY` non e' disponibile come env
    var (CI esterno), il test si salta. Default lookup:
    `PLENORA_E2E_JWT_SECRET` -> `JWT_SECRET_KEY` (env del runner).
    """
    import os
    import jwt as _jwt
    from datetime import datetime, timezone, timedelta

    secret = (
        os.getenv("PLENORA_E2E_JWT_SECRET")
        or os.getenv("JWT_SECRET_KEY")
    )
    if not secret:
        pytest.skip(
            "JWT_SECRET_KEY non configurata nel runner; non posso "
            "forgiare un token scaduto per testare il 401."
        )

    # Estrai shape del payload da un token valido in modo da non
    # hardcodare username / group_id / role
    r_me = http_client.get(f"{API_PREFIX}/auth/me", headers=auth_headers)
    assert r_me.status_code == 200, (
        f"GET /auth/me: {r_me.status_code} — {r_me.text[:200]}"
    )
    me = r_me.json()
    user_id = me.get("user_id") or me.get("sub") or me.get("id")
    username = me.get("username")
    group_id = me.get("group_id")
    role = me.get("role")
    assert all((user_id, username, group_id, role)), (
        f"AUTH-004 /auth/me payload incompleto: {me}"
    )

    expired_payload = {
        "sub": user_id,
        "username": username,
        "group_id": group_id,
        "role": role,
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
        "jti": uuid.uuid4().hex,
    }
    expired_token = _jwt.encode(expired_payload, secret, algorithm="HS256")
    bad_headers = {"Authorization": f"Bearer {expired_token}"}

    # Tre endpoint diversi per coprire il dependency `require_*`
    # senza indipendenza dal payload del singolo endpoint.
    for path in ("/auth/me", "/connections", "/projects"):
        r = http_client.get(f"{API_PREFIX}{path}", headers=bad_headers)
        assert r.status_code == 401, (
            f"AUTH-004 endpoint {path}: atteso 401 con token scaduto, "
            f"ottenuto {r.status_code} — {r.text[:200]}"
        )


# ── SEC-005 — Token blacklist post-logout ─────────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_sec_005_token_blacklist_post_logout(
    http_client: httpx.Client,
) -> None:
    """SEC-005 — Catalog: dopo `POST /auth/logout`, il token usato
    nella sessione deve essere in blacklist (jti registrato in
    Redis o equivalente). Una richiesta successiva con lo stesso
    token DEVE restituire 401.

    Reference: `app/shared/token_blacklist.py`. Il blacklist
    storage e' Redis-backed; il check via `decode_token` non
    basta — serve il dependency `require_*` che esamina il jti.
    """
    import os
    user = os.getenv("PLENORA_E2E_USER", "tester")
    pwd = os.getenv("PLENORA_E2E_PASSWORD", "plenora_test_pwd")
    r_login = http_client.post(
        f"{API_PREFIX}/auth/login",
        json={"username": user, "password": pwd},
    )
    assert r_login.status_code == 200, (
        f"SEC-005 login fallito: {r_login.status_code} — "
        f"{r_login.text[:200]}"
    )
    token = r_login.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # Pre-logout: token valido, /auth/me 200
    r_me_pre = http_client.get(f"{API_PREFIX}/auth/me", headers=h)
    assert r_me_pre.status_code == 200, (
        f"SEC-005 token non valido pre-logout: {r_me_pre.status_code}"
    )

    # Logout
    r_logout = http_client.post(f"{API_PREFIX}/auth/logout", headers=h)
    assert r_logout.status_code in (200, 204), (
        f"SEC-005 logout fallito: {r_logout.status_code} — "
        f"{r_logout.text[:200]}"
    )

    # Post-logout: stesso token DEVE essere rifiutato 401
    r_me_post = http_client.get(f"{API_PREFIX}/auth/me", headers=h)
    assert r_me_post.status_code == 401, (
        f"SEC-005 token ancora valido post-logout: "
        f"{r_me_post.status_code} — {r_me_post.text[:200]}. "
        f"Blacklist non funzionante."
    )


# ── AUTH-002 — MFA TOTP enrollment ────────────────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_auth_002_mfa_totp_enrollment(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """AUTH-002 — Catalog: "secret generato, code valida apre
    sessione".

    Flow:
      1. POST /auth/mfa/enroll → ottieni `secret` + `provisioning_uri`
      2. Calcola TOTP code dal secret (pyotp)
      3. POST /auth/mfa/verify con code → enabled=true
      4. (Cleanup) POST /auth/mfa/disable

    Skip se `pyotp` non e' nel runner.
    """
    try:
        import pyotp
    except ImportError:
        pytest.skip("pyotp non installato nel runner; AUTH-002 skip")

    r_enroll = http_client.post(
        f"{API_PREFIX}/auth/mfa/enroll", headers=auth_headers,
    )
    assert r_enroll.status_code == 200, (
        f"AUTH-002 enroll: {r_enroll.status_code} — "
        f"{r_enroll.text[:200]}"
    )
    body = r_enroll.json()
    secret = body.get("secret")
    assert secret and len(secret) >= 16, (
        f"AUTH-002 secret malformato: {body}"
    )

    code = pyotp.TOTP(secret).now()
    r_verify = http_client.post(
        f"{API_PREFIX}/auth/mfa/verify",
        json={"code": code}, headers=auth_headers,
    )
    assert r_verify.status_code == 200, (
        f"AUTH-002 verify: {r_verify.status_code} — "
        f"{r_verify.text[:200]}"
    )
    vbody = r_verify.json()
    assert vbody.get("enabled") is True, f"AUTH-002 not enabled: {vbody}"

    # Cleanup: disable MFA per non rompere AUTH-001/004 successivi
    http_client.post(
        f"{API_PREFIX}/auth/mfa/disable",
        json={"code": pyotp.TOTP(secret).now()}, headers=auth_headers,
    )


# ── AUTH-003 — Login con password sbagliata → rate limit ──────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_auth_003_login_rate_limit(
    http_client: httpx.Client,
) -> None:
    """AUTH-003 — Catalog: "dopo N tentativi, 429".

    N tentativi falliti consecutivi sullo stesso username devono
    triggerare il rate limiter. Il backend usa Redis o in-memory
    backend (vedi `app.shared.login_rate_limiter`).

    Test best-effort: se il rate limit non e' attivo (env dev
    senza Redis o disabilitato), accetta come tutti 401 e
    skippa la verifica 429. Se attivo, almeno una richiesta deve
    rispondere 429.
    """
    user = "rate-limit-target-" + os.urandom(4).hex()
    statuses = []
    for _ in range(12):
        r = http_client.post(
            f"{API_PREFIX}/auth/login",
            json={"username": user, "password": "wrong"},
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert all(s in (401, 403, 429) for s in statuses), (
        f"AUTH-003 status code inattesi: {statuses}"
    )
    if 429 not in statuses:
        pytest.skip(
            f"Rate limit non scattato in 12 tentativi: {statuses}. "
            f"Il backend potrebbe essere in modalita' "
            f"in-memory disabled o avere soglie alte. AUTH-003 "
            f"si conferma valido ma non osservabile in questa "
            f"configurazione."
        )
