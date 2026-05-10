"""Fixture condivise del runner E2E plenora.

Hanno tutte session-scope dove possibile, in modo che lo stack docker e
il login plenora vengano fatti una volta sola per l'intera run pytest.
Il teardown delle risorse create (gruppo, connection, ...) avviene a
fine sessione.

Convenzioni:
- Ogni fixture che dipende dal docker compose stack chiama
  `_require_compose_ready()` per fallire fast con messaggio chiaro se
  lo stack non è up.
- Ogni fixture che dipende dall'app plenora chiama
  `_require_plenora_app()` con stesso pattern.
- Niente magia: se manca un prereq il test viene skippato, non
  silenziosamente passato.

Endpoint plenora — note importanti scoperte leggendo i router:
- L'app monta `api_router` con prefix `/api`, quindi tutti gli URL
  di plenora iniziano con `/api/...` (vedi `app/main.py:293`).
- Login: `POST /api/auth/login` body `{username,password,mfa_code?}`,
  risposta `TokenResponse{access_token,token_type,user_id,username,
  group_id,role,expires_in}`.
- Groups: `POST /api/auth/groups` richiede ruolo `superadmin`.
  L'utente test smoke è `user`, quindi NON possiamo creare un nuovo
  gruppo via API → il fixture `group_id` riusa il gruppo dell'utente
  che torna dal login.
- Connections: `POST /api/connections` body `ConnectionCreate{name,
  description,type ∈ {postgres,mssql,mysql,oracle,sqlite},host,port,
  database,db_schema?,username,password}`. Niente type `s3` né `cloud`.
- Test connection: `POST /api/connections/{id}/test` ritorna
  `ConnectionTestResult{success:bool,message:str,details?:dict}`.
  NB: il catalogo dice `{ok:true, latency_ms}` ma il contratto reale
  è `success/message`. Asseriamo `success is True`.
- Tabelle: `GET /api/connections/{id}/tables` (NON `/schemas`).
- Datasources: `POST /api/datasources` body `DataSourceCreate{name,
  type ∈ {local,ftp,sftp,sharepoint},host?,port?,...}`. Per FTP/SFTP
  il path radice è in `root_path` (NON `base_path`).
- API connections: `POST /api/api-connections` accetta dict free-form,
  payload normalizzato dal service (vedi `arricchisci/router.py:472`).
- Projects: `POST /api/projects` body `ProjectCreate{name,description,
  project_type,default_connection_id?}`. NB: `group_id` NON è in
  payload, è derivato da `current_user.group_id`.
- Canvas: `PUT /api/projects/{id}/canvas` con `CanvasWithPlugins{nodes,
  edges,plugins?}`. Non esistono "canvases" come risorse separate —
  ogni progetto ha un canvas inline.
- Run on-demand: `POST /api/projects/{id}/execute` ritorna `task_info`
  con `task_id`. Poll: `GET /api/tasks/{task_id}` → status terminale
  in `result.status`.
- Result preview: il backend non ha un endpoint `/results/{id}/preview`
  esposto nel router. Il flusso reale è preview via task:
  `GET /api/projects/{id}/preview` ritorna un task asincrono che,
  al completamento, ha `result.preview` con righe + schema.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest


# ── Paths ───────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE_DEFAULT = REPO_ROOT / "backend" / "test-e2e" / "docker" / "docker-compose.yml"

# Tutti gli endpoint plenora hanno prefisso /api (vedi app/main.py:293).
API_PREFIX = "/api"


# ── Config dataclass ────────────────────────────────────────────────────

@dataclass(frozen=True)
class E2EConfig:
    plenora_api_url: str
    profile: str
    user: str
    password: str
    compose_file: Path
    timeout: float
    verify_docker: bool

    @classmethod
    def from_env(cls) -> "E2EConfig":
        return cls(
            plenora_api_url=os.getenv("PLENORA_API_URL", "http://localhost:5004").rstrip("/"),
            profile=os.getenv("PLENORA_E2E_PROFILE", "core"),
            user=os.getenv("PLENORA_E2E_USER", "tester@plenora.test"),
            password=os.getenv("PLENORA_E2E_PASSWORD", "plenora_test_pwd"),
            compose_file=Path(os.getenv("PLENORA_E2E_COMPOSE_FILE", str(COMPOSE_FILE_DEFAULT))),
            timeout=float(os.getenv("PLENORA_E2E_TIMEOUT", "30")),
            verify_docker=os.getenv("PLENORA_E2E_VERIFY_DOCKER", "1") == "1",
        )


# ── Helpers ─────────────────────────────────────────────────────────────

def _profile_is(active: str, required: str) -> bool:
    """Profilo attivo include `required`?

    Ordine canonico: core ⊂ db ⊂ db-heavy, e (cloud, files) opzionali.
    `full` include tutto.
    """
    active = active.lower()
    required = required.lower()
    if active == required:
        return True
    if active == "full":
        return True
    if required == "core":
        return active in {"core", "db", "db-heavy", "cloud", "files", "full"}
    if required == "db":
        return active in {"db", "db-heavy", "full"}
    if required == "db-heavy":
        return active in {"db-heavy", "full"}
    if required == "cloud":
        return active in {"cloud", "full"}
    if required == "files":
        return active in {"files", "full"}
    return False


def _maybe_id(body: Any) -> str | None:
    """Estrae un id da risposte plenora con shape eterogenea."""
    if not isinstance(body, dict):
        return None
    for key in ("id", "connection_id", "datasource_id", "api_id",
                "api_connection_id", "project_id", "group_id"):
        value = body.get(key)
        if value:
            return str(value)
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        return _maybe_id(metadata)
    return None


# ── Session-scope fixtures ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    """Configurazione globale del runner, derivata da env vars."""
    return E2EConfig.from_env()


@pytest.fixture(scope="session")
def stack_healthy(e2e_config: E2EConfig) -> dict[str, Any]:
    """Verifica che i container del profilo richiesto siano (healthy).

    Skippa il test se il compose non è raggiungibile o se uno dei
    container del profilo non è in stato healthy. Non avvia nulla:
    presupposto è che lo stack sia già up.
    """
    if not e2e_config.verify_docker:
        return {"status": "skipped", "services": {}}

    if not e2e_config.compose_file.exists():
        pytest.skip(f"compose file non trovato: {e2e_config.compose_file}")

    cmd = [
        "docker", "compose",
        "-f", str(e2e_config.compose_file),
        "--profile", e2e_config.profile,
        "ps", "--format", "json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        pytest.skip("docker non installato o non in PATH")
    except subprocess.TimeoutExpired:
        pytest.skip("`docker compose ps` ha superato il timeout")

    if proc.returncode != 0:
        pytest.skip(f"`docker compose ps` ha fallito: {proc.stderr.strip()}")

    services: dict[str, dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = entry.get("Service") or entry.get("Name", "")
        health = (entry.get("Health") or "").lower()
        state = (entry.get("State") or "").lower()
        services[name] = {
            "state": state,
            "health": health,
            "healthy": health == "healthy" or (health == "" and state == "running"),
        }

    if not services:
        pytest.skip(
            f"Nessun container attivo per profilo {e2e_config.profile!r}. "
            f"Avvia con: docker compose -f {e2e_config.compose_file} "
            f"--profile {e2e_config.profile} up -d"
        )

    not_healthy = [s for s, info in services.items() if not info["healthy"]]
    if not_healthy:
        pytest.skip(f"Container non healthy: {not_healthy}. Stato: {services}")

    return {"status": "ok", "services": services, "profile": e2e_config.profile}


@pytest.fixture(scope="session")
def http_client(e2e_config: E2EConfig) -> Iterator[httpx.Client]:
    """Client HTTP base verso l'app plenora. Niente auth applicata."""
    with httpx.Client(
        base_url=e2e_config.plenora_api_url,
        timeout=e2e_config.timeout,
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def plenora_app_alive(e2e_config: E2EConfig, http_client: httpx.Client) -> dict[str, Any]:
    """Verifica che l'app plenora risponda a un endpoint di health.

    Tenta `/health`, poi `/api/health`, poi `/`. Se nessuno risponde
    con 2xx entro il timeout, skippa il test che richiede l'app.
    """
    candidates = ["/health", f"{API_PREFIX}/health", "/"]
    last_error: str | None = None

    for path in candidates:
        try:
            r = http_client.get(path)
        except httpx.HTTPError as exc:
            last_error = f"{path}: {exc.__class__.__name__}: {exc}"
            continue
        if r.status_code < 500:
            return {"status": "ok", "endpoint": path, "code": r.status_code}
        last_error = f"{path}: HTTP {r.status_code}"

    pytest.skip(
        f"App plenora non raggiungibile su {e2e_config.plenora_api_url}. "
        f"Ultimo errore: {last_error}"
    )


@pytest.fixture(scope="session")
def login_payload(
    e2e_config: E2EConfig,
    plenora_app_alive: dict[str, Any],
    http_client: httpx.Client,
) -> dict[str, Any]:
    """Login utente test. Ritorna il body completo della risposta.

    Contiene `access_token`, `group_id`, `user_id`, `role` — tutti
    necessari per le fixture che dipendono dal login.
    """
    payload = {"username": e2e_config.user, "password": e2e_config.password}
    r = http_client.post(f"{API_PREFIX}/auth/login", json=payload)
    if r.status_code >= 400:
        pytest.skip(f"Login utente test fallito: HTTP {r.status_code} — {r.text[:200]}")

    body = r.json()
    if not body.get("access_token"):
        pytest.skip(f"Risposta login senza access_token: {body}")
    return body


@pytest.fixture(scope="session")
def auth_token(login_payload: dict[str, Any]) -> str:
    """JWT access_token dell'utente test."""
    return str(login_payload["access_token"])


@pytest.fixture(scope="session")
def auth_headers(auth_token: str) -> dict[str, str]:
    """Header `Authorization: Bearer <jwt>` da iniettare nelle richieste."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="session")
def group_id(login_payload: dict[str, Any]) -> str:
    """ID del gruppo dell'utente test.

    Il catalogo PREP-003 richiederebbe `POST /groups` per creare un
    gruppo dedicato all'esecuzione, ma quell'endpoint richiede ruolo
    `superadmin` (vedi `auth/groups_router.py:101`). L'utente smoke ha
    ruolo `user`, quindi riusiamo il suo gruppo come sandbox isolata.

    Le risorse create dai test (connection, project, datasource) hanno
    teardown puntuale via DELETE.
    """
    gid = login_payload.get("group_id")
    if not gid:
        pytest.skip(f"Login senza group_id: {login_payload}")
    return str(gid)


# ── DB connections ──────────────────────────────────────────────────────

def _create_db_connection(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    *,
    name: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> str:
    """POST /api/connections + return id. Skippa se non riesce."""
    payload = {
        "name": name,
        "description": f"e2e {db_type} fixture",
        "type": db_type,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password,
    }
    r = http_client.post(
        f"{API_PREFIX}/connections", json=payload, headers=auth_headers
    )
    if r.status_code >= 400:
        pytest.skip(
            f"Creazione connection {db_type} fallita: "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )
    cid = _maybe_id(r.json())
    if not cid:
        pytest.skip(f"Risposta connection {db_type} senza id: {r.json()}")
    return cid


def _delete_silent(http_client: httpx.Client, path: str, headers: dict[str, str]) -> None:
    """DELETE idempotente: ignora errori HTTP, soprattutto 404."""
    try:
        http_client.delete(path, headers=headers)
    except httpx.HTTPError:
        pass


@pytest.fixture(scope="session")
def postgres_conn_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Connection PostgreSQL → `plenora-e2e-postgres`."""
    cid = _create_db_connection(
        http_client, auth_headers,
        name=f"e2e-pg-{uuid.uuid4().hex[:6]}",
        db_type="postgres",
        host=os.getenv("PLENORA_E2E_PG_HOST", "plenora-e2e-postgres"),
        port=int(os.getenv("PLENORA_E2E_PG_PORT", "5432")),
        database=os.getenv("PLENORA_E2E_PG_DB", "plenora_test"),
        username=os.getenv("PLENORA_E2E_PG_USER", "plenora"),
        password=os.getenv("PLENORA_E2E_PG_PWD", "plenora_test_pwd"),
    )
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/connections/{cid}", auth_headers)


@pytest.fixture(scope="session")
def mysql_conn_id(
    e2e_config: E2EConfig,
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Connection MySQL → `plenora-e2e-mysql`. Profilo `db`."""
    if not _profile_is(e2e_config.profile, "db"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `db`")
    cid = _create_db_connection(
        http_client, auth_headers,
        name=f"e2e-mysql-{uuid.uuid4().hex[:6]}",
        db_type="mysql",
        host=os.getenv("PLENORA_E2E_MYSQL_HOST", "plenora-e2e-mysql"),
        port=int(os.getenv("PLENORA_E2E_MYSQL_PORT", "3306")),
        database=os.getenv("PLENORA_E2E_MYSQL_DB", "plenora_test"),
        username=os.getenv("PLENORA_E2E_MYSQL_USER", "plenora"),
        password=os.getenv("PLENORA_E2E_MYSQL_PWD", "plenora_test_pwd"),
    )
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/connections/{cid}", auth_headers)


@pytest.fixture(scope="session")
def sqlserver_conn_id(
    e2e_config: E2EConfig,
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Connection SQL Server → `plenora-e2e-sqlserver`. Profilo `db`.

    Type plenora: `mssql` (NON `sqlserver`).
    """
    if not _profile_is(e2e_config.profile, "db"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `db`")
    cid = _create_db_connection(
        http_client, auth_headers,
        name=f"e2e-mssql-{uuid.uuid4().hex[:6]}",
        db_type="mssql",
        host=os.getenv("PLENORA_E2E_MSSQL_HOST", "plenora-e2e-sqlserver"),
        port=int(os.getenv("PLENORA_E2E_MSSQL_PORT", "1433")),
        database=os.getenv("PLENORA_E2E_MSSQL_DB", "plenora_test"),
        username=os.getenv("PLENORA_E2E_MSSQL_USER", "sa"),
        password=os.getenv("PLENORA_E2E_MSSQL_PWD", "PlenoraTest!2026"),
    )
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/connections/{cid}", auth_headers)


@pytest.fixture(scope="session")
def oracle_conn_id(
    e2e_config: E2EConfig,
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Connection Oracle → `plenora-e2e-oracle`. Profilo `db-heavy`."""
    if not _profile_is(e2e_config.profile, "db-heavy"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `db-heavy`")
    cid = _create_db_connection(
        http_client, auth_headers,
        name=f"e2e-oracle-{uuid.uuid4().hex[:6]}",
        db_type="oracle",
        host=os.getenv("PLENORA_E2E_ORACLE_HOST", "plenora-e2e-oracle"),
        port=int(os.getenv("PLENORA_E2E_ORACLE_PORT", "1521")),
        database=os.getenv("PLENORA_E2E_ORACLE_DB", "plenora_test"),
        username=os.getenv("PLENORA_E2E_ORACLE_USER", "plenora"),
        password=os.getenv("PLENORA_E2E_ORACLE_PWD", "plenora_test_pwd"),
    )
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/connections/{cid}", auth_headers)


# ── API connections (REST/ArcGIS) ───────────────────────────────────────

def _create_api_connection(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    payload: dict[str, Any],
) -> str:
    r = http_client.post(
        f"{API_PREFIX}/api-connections", json=payload, headers=auth_headers
    )
    if r.status_code >= 400:
        pytest.skip(
            f"Creazione api_connection fallita: "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )
    cid = _maybe_id(r.json())
    if not cid:
        pytest.skip(f"Risposta api_connection senza id: {r.json()}")
    return cid


@pytest.fixture(scope="session")
def api_conn_apikey_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """api_connection REST con autenticazione api_key in header.

    Schema payload backend (vedi `api_connection_management.create_api_connection`):
    - `url` top-level (mappato in `endpoint.url`); NON `base_url`.
    - `auth` annidato `{type, location, key_name, key_value}` (vedi
      `app.shared.connections.oauth.prepare_headers`).
    """
    base_url = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )
    payload = {
        "name": f"e2e-rest-apikey-{uuid.uuid4().hex[:6]}",
        "description": "e2e REST api_key fixture",
        "connection_type": "rest",
        "url": f"{base_url}/health",
        "method": "GET",
        "auth": {
            "type": "api_key",
            "location": "header",
            "key_name": "X-API-Key",
            "key_value": os.getenv(
                "PLENORA_E2E_FAKE_REST_KEY", "PLENORA_TEST_KEY"
            ),
        },
    }
    cid = _create_api_connection(http_client, auth_headers, payload)
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/api-connections/{cid}", auth_headers)


@pytest.fixture(scope="session")
def api_conn_oauth2_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """api_connection REST con OAuth2 client_credentials."""
    base_url = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )
    payload = {
        "name": f"e2e-rest-oauth2-{uuid.uuid4().hex[:6]}",
        "description": "e2e REST OAuth2 client_credentials fixture",
        "connection_type": "rest",
        "base_url": base_url,
        "auth_type": "oauth2_client_credentials",
        "token_url": f"{base_url}/auth/oauth2/token",
        "client_id": os.getenv("PLENORA_E2E_OAUTH_CLIENT_ID", "plenora"),
        "client_secret": os.getenv("PLENORA_E2E_OAUTH_CLIENT_SECRET", "plenora_secret"),
    }
    cid = _create_api_connection(http_client, auth_headers, payload)
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/api-connections/{cid}", auth_headers)


@pytest.fixture(scope="session")
def api_conn_arcgis_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """api_connection ArcGIS con generateToken."""
    base_url = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )
    payload = {
        "name": f"e2e-arcgis-{uuid.uuid4().hex[:6]}",
        "description": "e2e ArcGIS token fixture",
        "connection_type": "arcgis",
        "base_url": base_url,
        "auth_type": "arcgis_token",
        "token_url": f"{base_url}/auth/arcgis/generateToken",
        "username": os.getenv("PLENORA_E2E_ARCGIS_USER", "tester"),
        "password": os.getenv("PLENORA_E2E_ARCGIS_PWD", "tester_pass"),
    }
    cid = _create_api_connection(http_client, auth_headers, payload)
    yield cid
    _delete_silent(http_client, f"{API_PREFIX}/api-connections/{cid}", auth_headers)


# ── Datasources (FTP / SFTP) ────────────────────────────────────────────

def _create_datasource(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    payload: dict[str, Any],
) -> str:
    r = http_client.post(
        f"{API_PREFIX}/datasources", json=payload, headers=auth_headers
    )
    if r.status_code >= 400:
        pytest.skip(
            f"Creazione datasource fallita: "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )
    did = _maybe_id(r.json())
    if not did:
        pytest.skip(f"Risposta datasource senza id: {r.json()}")
    return did


@pytest.fixture(scope="session")
def ftp_datasource_id(
    e2e_config: E2EConfig,
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Datasource FTP → `plenora-e2e-ftp`. Profilo `files`."""
    if not _profile_is(e2e_config.profile, "files"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `files`")
    payload = {
        "name": f"e2e-ftp-{uuid.uuid4().hex[:6]}",
        "description": "e2e FTP fixture",
        "type": "ftp",
        "host": os.getenv("PLENORA_E2E_FTP_HOST", "plenora-e2e-ftp"),
        "port": int(os.getenv("PLENORA_E2E_FTP_PORT", "21")),
        "username": os.getenv("PLENORA_E2E_FTP_USER", "tester"),
        "password": os.getenv("PLENORA_E2E_FTP_PWD", "plenora_ftp_pwd"),
        "root_path": os.getenv("PLENORA_E2E_FTP_ROOT", "/fixtures"),
        "passive": True,
        "tls": False,
    }
    did = _create_datasource(http_client, auth_headers, payload)
    yield did
    _delete_silent(http_client, f"{API_PREFIX}/datasources/{did}", auth_headers)


@pytest.fixture(scope="session")
def sftp_datasource_id(
    e2e_config: E2EConfig,
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> Iterator[str]:
    """Datasource SFTP → `plenora-e2e-sftp`. Profilo `files`."""
    if not _profile_is(e2e_config.profile, "files"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `files`")
    payload = {
        "name": f"e2e-sftp-{uuid.uuid4().hex[:6]}",
        "description": "e2e SFTP fixture",
        "type": "sftp",
        "host": os.getenv("PLENORA_E2E_SFTP_HOST", "plenora-e2e-sftp"),
        "port": int(os.getenv("PLENORA_E2E_SFTP_PORT", "22")),
        "username": os.getenv("PLENORA_E2E_SFTP_USER", "tester"),
        "password": os.getenv("PLENORA_E2E_SFTP_PWD", "plenora_sftp_pwd"),
        "root_path": os.getenv("PLENORA_E2E_SFTP_ROOT", "/fixtures"),
        "host_key_policy": "auto_add",
    }
    did = _create_datasource(http_client, auth_headers, payload)
    yield did
    _delete_silent(http_client, f"{API_PREFIX}/datasources/{did}", auth_headers)


# ── S3 / MinIO ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def s3_conn_id(
    e2e_config: E2EConfig,
) -> Iterator[str]:
    """Stub fixture per la connection S3/MinIO.

    Il backend plenora **non espone** un type `s3` né nelle connections
    (`Literal["postgres","mssql","mysql","oracle","sqlite"]`) né nei
    datasources (`Literal["local","ftp","sftp","sharepoint"]`). La
    connection S3 vive solo dentro template/feature_source ArcGIS-like
    o in pipeline carica, e non ha CRUD pubblico dedicato. Skippiamo
    la fixture finché il contratto API non sarà chiarito.
    """
    if not _profile_is(e2e_config.profile, "cloud"):
        pytest.skip(f"Profilo {e2e_config.profile!r} non include `cloud`")
    pytest.skip(
        "Connection S3/MinIO non ha endpoint CRUD pubblico in plenora "
        "(no type 's3' in ConnectionCreate.type né in DataSourceCreate.type)."
    )
    yield ""  # pragma: no cover


# ── Project / canvas / run / result ─────────────────────────────────────

@pytest.fixture(scope="session")
def project_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
    postgres_conn_id: str,
) -> Iterator[str]:
    """Crea un progetto vuoto nel gruppo dell'utente.

    NB: `ProjectCreate` non accetta `group_id` in payload — il gruppo
    è derivato lato server da `current_user.group_id`. Il default
    connection è il postgres fixture (utile per PREP-020).
    """
    payload = {
        "name": f"e2e-project-{uuid.uuid4().hex[:6]}",
        "description": "e2e fixture project",
        "project_type": "estrai",
        "default_connection_id": postgres_conn_id,
    }
    r = http_client.post(
        f"{API_PREFIX}/projects", json=payload, headers=auth_headers
    )
    if r.status_code >= 400:
        pytest.skip(f"Creazione progetto fallita: HTTP {r.status_code} — {r.text[:200]}")
    pid = _maybe_id(r.json())
    if not pid:
        pytest.skip(f"Risposta progetto senza id: {r.json()}")
    yield pid
    _delete_silent(http_client, f"{API_PREFIX}/projects/{pid}", auth_headers)


@pytest.fixture(scope="session")
def canvas_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    project_id: str,
) -> str:
    """Inizializza il canvas del progetto con definition vuota.

    In plenora il canvas non è una risorsa separata: vive dentro al
    progetto e si aggiorna via `PUT /projects/{id}/canvas`. Il
    "canvas_id" coincide con il project_id per uniformità con il
    catalogo PREP-019, e il PUT vuoto è idempotente.
    """
    payload = {"nodes": [], "edges": []}
    r = http_client.put(
        f"{API_PREFIX}/projects/{project_id}/canvas",
        json=payload, headers=auth_headers,
    )
    if r.status_code >= 400:
        pytest.skip(
            f"Init canvas fallita: HTTP {r.status_code} — {r.text[:200]}"
        )
    return project_id


def _poll_task(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    task_id: str,
    *,
    timeout: float = 60.0,
    interval: float = 1.0,
) -> dict[str, Any]:
    """Poll `/tasks/{task_id}` finché lo stato non è terminale.

    Stati terminali plenora: success, failure, cancelled, error.
    """
    deadline = time.monotonic() + timeout
    last_body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = http_client.get(
            f"{API_PREFIX}/tasks/{task_id}", headers=auth_headers
        )
        if r.status_code >= 400:
            return {"status": "error", "http_status": r.status_code, "body": r.text}
        last_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        status = (last_body.get("status") or "").lower()
        if status in {"success", "failure", "cancelled", "error", "completed", "failed"}:
            return last_body
        time.sleep(interval)
    return {"status": "timeout", **last_body}


@pytest.fixture(scope="function")
def run_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    project_id: str,
    canvas_id: str,
) -> str:
    """Esegue il canvas del progetto e ritorna il task_id (run_id).

    Il fixture è function-scope: ogni test che lo richiede triggera un
    run isolato. Polla fino a stato terminale o timeout 60s.
    """
    r = http_client.post(
        f"{API_PREFIX}/projects/{project_id}/execute",
        json={}, headers=auth_headers,
    )
    if r.status_code >= 400:
        pytest.skip(f"Avvio run fallito: HTTP {r.status_code} — {r.text[:200]}")
    body = r.json()
    task_id = body.get("task_id") or body.get("id")
    if not task_id:
        pytest.skip(f"Risposta execute senza task_id: {body}")

    final = _poll_task(http_client, auth_headers, str(task_id))
    status = (final.get("status") or "").lower()
    if status not in {"success", "completed"}:
        pytest.skip(f"Run non completata con success: {final}")
    return str(task_id)


@pytest.fixture(scope="function")
def result_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    run_id: str,
) -> str:
    """Estrae il result_id dall'ultimo run completato.

    Plenora memorizza il result associato al `task_id`/`project_id`.
    Riusiamo il run_id come result_id quando il backend non espone un
    id distinto (la preview e il download accettano entrambi).
    """
    r = http_client.get(f"{API_PREFIX}/tasks/{run_id}", headers=auth_headers)
    if r.status_code >= 400:
        pytest.skip(f"Lettura task fallita: HTTP {r.status_code}")
    body = r.json()
    result = body.get("result") or {}
    rid = result.get("result_id") or body.get("result_id") or run_id
    return str(rid)


# ── Hooks ───────────────────────────────────────────────────────────────

def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip dei test slow/interactive a meno di flag esplicite."""
    run_slow = config.getoption("--run-slow", default=False)
    run_interactive = config.getoption("--run-interactive", default=False)

    skip_slow = pytest.mark.skip(reason="usa --run-slow per eseguire")
    skip_interactive = pytest.mark.skip(reason="usa --run-interactive per eseguire")

    for item in items:
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)
        if "interactive" in item.keywords and not run_interactive:
            item.add_marker(skip_interactive)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Esegui anche i test marker 'slow' (default: skipped)",
    )
    parser.addoption(
        "--run-interactive",
        action="store_true",
        default=False,
        help="Esegui anche i test 'interactive' (richiedono verifica umana)",
    )
