"""Bootstrap idempotente di Grafana per Plenora (eseguito da install-native.ps1).

Solo libreria standard: gira nell'env conda dell'installer senza dipendenze.

Cosa fa, ad ogni esecuzione (nessun passo crea doppioni):
1. aspetta che Grafana risponda (``/api/health``);
2. rinomina l'organizzazione 1 in «Plenora Dashboard» (gli utenti Plenora
   vi entrano come Viewer: ``[users] auto_assign_org_id = 1``);
3. service account ``plenora`` con ruolo Admin; il token in ``.env``
   (``GRAFANA_SERVICE_TOKEN``) si riusa se e' ancora valido, altrimenti se ne
   crea uno nuovo e gli altri token dello stesso account vengono revocati;
4. datasource Infinity con uid FISSO ``plenora-infinity``: puo' chiamare solo
   ``<backend>/api/dash/`` e manda ``X-Grafana-Secret`` (campo cifrato).

Autenticazione verso Grafana: ``auth.proxy`` da 127.0.0.1 col login
amministratore casuale scritto nel custom.ini (``X-WEBAUTH-USER``), lo stesso
meccanismo che Caddy usa per gli utenti Plenora. Basic auth e form di login
restano spenti: la password dell'amministratore (casuale, nel custom.ini) non
serve a nessuno e Grafana rifiuta di cambiarla via API col form spento.

Ingressi (segreti SOLO da variabili d'ambiente, mai da riga di comando):
``GRAFANA_BOOTSTRAP_ADMIN_LOGIN``, ``GRAFANA_DATA_SECRET``,
``GRAFANA_SERVICE_TOKEN`` (facoltativo, quello attuale del .env).
Uscita: ultima riga su stdout ``{"service_token": "...", "token_created": bool}``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


ORG_ID = 1
ORG_NAME = "Plenora Dashboard"
SERVICE_ACCOUNT_NAME = "plenora"
SERVICE_ACCOUNT_ROLE = "Admin"
DATASOURCE_UID = "plenora-infinity"
DATASOURCE_NAME = "Plenora"
DATASOURCE_TYPE = "yesoreyeram-infinity-datasource"
# Prefisso delle rotte dati del backend (router montato su /api).
DASH_API_PREFIX = "/api/dash/"
SECRET_HEADER = "X-Grafana-Secret"
AUTH_PROXY_USER_HEADER = "X-WEBAUTH-USER"
AUTH_PROXY_ROLE_HEADER = "X-WEBAUTH-ROLE"


class BootstrapError(RuntimeError):
    pass


@dataclass
class Response:
    status: int
    body: Any


class Grafana:
    """Client HTTP minimale: ogni richiesta e' autenticata in UN modo."""

    def __init__(self, base_url: str, *, admin_login: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_login = admin_login
        self.timeout = timeout

    def _headers(self, token: Optional[str]) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers[AUTH_PROXY_USER_HEADER] = self.admin_login
            headers[AUTH_PROXY_ROLE_HEADER] = "Admin"
        return headers

    def request(self, method: str, path: str, payload: Any = None, *, token: Optional[str] = None) -> Response:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=self._headers(token)
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(resp.status, _decode(resp.read()))
        except urllib.error.HTTPError as exc:
            return Response(exc.code, _decode(exc.read()))

    def expect(self, method: str, path: str, payload: Any = None, *, ok: tuple[int, ...] = (200,)) -> Any:
        resp = self.request(method, path, payload)
        if resp.status not in ok:
            raise BootstrapError(f"{method} {path}: HTTP {resp.status} {_short(resp.body)}")
        return resp.body


def _decode(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", "replace")


def _short(body: Any) -> str:
    return str(body)[:300]


def wait_healthy(grafana: Grafana, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    last = "nessuna risposta"
    while True:
        try:
            resp = grafana.request("GET", "/api/health")
            if resp.status == 200 and isinstance(resp.body, dict) and resp.body.get("database") == "ok":
                return
            last = f"HTTP {resp.status} {_short(resp.body)}"
        except OSError as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise BootstrapError(f"Grafana non pronto: {last}")
        time.sleep(2)


def ensure_admin_session(grafana: Grafana) -> None:
    """Il login del custom.ini deve essere l'amministratore del server."""
    user = grafana.expect("GET", "/api/user")
    if not (isinstance(user, dict) and user.get("isGrafanaAdmin")):
        raise BootstrapError(
            f"il login {grafana.admin_login!r} non e' amministratore di Grafana: il database "
            "e' stato creato con un altro admin_user (vedi docs/ops/install-windows.md)"
        )


def ensure_org(grafana: Grafana) -> None:
    org = grafana.expect("GET", f"/api/orgs/{ORG_ID}")
    if org.get("name") != ORG_NAME:
        grafana.expect("PUT", f"/api/orgs/{ORG_ID}", {"name": ORG_NAME})


def ensure_service_account(grafana: Grafana) -> int:
    found = grafana.expect("GET", f"/api/serviceaccounts/search?query={SERVICE_ACCOUNT_NAME}")
    for account in found.get("serviceAccounts", []):
        if account.get("name") == SERVICE_ACCOUNT_NAME:
            if account.get("role") != SERVICE_ACCOUNT_ROLE:
                grafana.expect(
                    "PATCH", f"/api/serviceaccounts/{account['id']}", {"role": SERVICE_ACCOUNT_ROLE}
                )
            return int(account["id"])
    created = grafana.expect(
        "POST",
        "/api/serviceaccounts",
        {"name": SERVICE_ACCOUNT_NAME, "role": SERVICE_ACCOUNT_ROLE},
        ok=(200, 201),
    )
    return int(created["id"])


def token_is_valid(grafana: Grafana, token: str) -> bool:
    if not token:
        return False
    resp = grafana.request("GET", "/api/org", token=token)
    return resp.status == 200 and isinstance(resp.body, dict) and resp.body.get("id") == ORG_ID


def ensure_token(grafana: Grafana, account_id: int, current: str) -> tuple[str, bool]:
    if token_is_valid(grafana, current):
        return current, False
    # Il token del .env e' perso o revocato: i vecchi token non li conosce
    # nessuno, quindi si revocano (un token Admin dimenticato e' un rischio).
    for old in grafana.expect("GET", f"/api/serviceaccounts/{account_id}/tokens") or []:
        grafana.expect("DELETE", f"/api/serviceaccounts/{account_id}/tokens/{old['id']}")
    name = "plenora-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    created = grafana.expect(
        "POST", f"/api/serviceaccounts/{account_id}/tokens", {"name": name}, ok=(200, 201)
    )
    return str(created["key"]), True


def datasource_body(backend_url: str, data_secret: str) -> Mapping[str, Any]:
    return {
        "name": DATASOURCE_NAME,
        "uid": DATASOURCE_UID,
        "type": DATASOURCE_TYPE,
        "access": "proxy",
        "isDefault": True,
        "jsonData": {
            "allowedHosts": [backend_url.rstrip("/") + DASH_API_PREFIX],
            "httpHeaderName1": SECRET_HEADER,
            "timeoutInSeconds": 30,
        },
        "secureJsonData": {"httpHeaderValue1": data_secret},
    }


def ensure_datasource(grafana: Grafana, backend_url: str, data_secret: str) -> None:
    body = datasource_body(backend_url, data_secret)
    existing = grafana.request("GET", f"/api/datasources/uid/{DATASOURCE_UID}")
    if existing.status == 200:
        grafana.expect("PUT", f"/api/datasources/uid/{DATASOURCE_UID}", body)
    elif existing.status == 404:
        grafana.expect("POST", "/api/datasources", body)
    else:
        raise BootstrapError(f"datasource {DATASOURCE_UID}: HTTP {existing.status} {_short(existing.body)}")


def bootstrap(
    grafana: Grafana,
    *,
    backend_url: str,
    data_secret: str,
    current_token: str,
    wait_seconds: float = 120.0,
) -> dict[str, Any]:
    if not data_secret:
        raise BootstrapError("GRAFANA_DATA_SECRET mancante")
    wait_healthy(grafana, wait_seconds)
    ensure_admin_session(grafana)
    ensure_org(grafana)
    account_id = ensure_service_account(grafana)
    token, created = ensure_token(grafana, account_id, current_token)
    ensure_datasource(grafana, backend_url, data_secret)
    return {"service_token": token, "token_created": created}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3300/grafana")
    parser.add_argument("--backend-url", default="http://127.0.0.1:5004")
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    args = parser.parse_args(argv)

    admin_login = os.environ.get("GRAFANA_BOOTSTRAP_ADMIN_LOGIN", "").strip()
    if not admin_login:
        print("GRAFANA_BOOTSTRAP_ADMIN_LOGIN mancante", file=sys.stderr)
        return 2
    try:
        result = bootstrap(
            Grafana(args.grafana_url, admin_login=admin_login),
            backend_url=args.backend_url,
            data_secret=os.environ.get("GRAFANA_DATA_SECRET", "").strip(),
            current_token=os.environ.get("GRAFANA_SERVICE_TOKEN", "").strip(),
            wait_seconds=args.wait_seconds,
        )
    except BootstrapError as exc:
        print(f"bootstrap Grafana: {exc}", file=sys.stderr)
        return 1
    print("token del service account: " + ("nuovo" if result["token_created"] else "riusato"))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
