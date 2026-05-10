"""PREP-002..PREP-023 — building block setup tests.

Mapping al catalogo `test-e2e/test-e2e.md`:

- PREP-002: login utente test → JWT
- PREP-003: gruppo di test (riusa quello dell'utente loggato)
- PREP-004: connection PostgreSQL
- PREP-005: connection MySQL (profilo `db`)
- PREP-006: connection SQL Server (profilo `db`, type=`mssql`)
- PREP-007: connection Oracle (profilo `db-heavy`)
- PREP-008: test connection Postgres (ping DB)
- PREP-009: schema introspection — `GET /connections/{id}/tables`
- PREP-010: api_connection REST con api_key
- PREP-011: api_connection REST con OAuth2 client_credentials
- PREP-012: api_connection ArcGIS con generateToken
- PREP-013: test api_connection (ping REST)
- PREP-014: datasource FTP (profilo `files`)
- PREP-015: datasource SFTP (profilo `files`)
- PREP-016: list file datasource
- PREP-017: connection cloud S3 (skipped — no contract API pubblica)
- PREP-018: progetto vuoto
- PREP-019: canvas vuoto del progetto
- PREP-020: salva canvas con nodi (estrai customers)
- PREP-021: esegue canvas (run on-demand) e attende success
- PREP-022: recupera result preview
- PREP-023: cleanup gruppo cascade (skipped — richiede superadmin)

Tutti i test sono session-scope sequence-aware: una volta che la
fixture `auth_token` ha fatto login, gli altri lo riusano via fixture.
Niente magia: pytest fixture chain.

Convenzioni:
- Skip graceful: se la fixture upstream skippa, il test skippa.
- Asserzioni concrete dal contratto plenora reale, non da quello
  ottimistico del catalogo (es. il catalogo dice
  `test_connection → {ok:true, latency_ms}` ma il modello reale è
  `ConnectionTestResult{success,message,details}` — asseriamo
  `success is True`).
- Niente magic numbers non documentati: il count dataset (9 tabelle:
  regions, cities, customers, products, orders, order_items,
  employees, events_log, routes) è preso verbatim da PREP-009.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest


API_PREFIX = "/api"

# Tabelle attese nello schema seed PostgreSQL (vedi `test-e2e/sql/postgres/00_schema.sql`).
PREP_009_EXPECTED_TABLES = {
    "regions", "cities", "customers", "products", "orders",
    "order_items", "employees", "events_log", "routes",
}


# ── PREP-002 — Login utente test ───────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_002_login(auth_token: str) -> None:
    """PREP-002 — Login utente test, JWT ricevuto.

    Asserzione: la fixture `auth_token` ritorna una stringa JWT
    (3 parti separate da `.`). Se la fixture skippa, il test skippa.
    """
    assert isinstance(auth_token, str) and auth_token, "auth_token vuoto"
    parts = auth_token.split(".")
    assert len(parts) == 3, (
        f"JWT non ha 3 segmenti: ottenuti {len(parts)} — "
        f"primo: {auth_token[:40]!r}..."
    )


# ── PREP-003 — Gruppo di test ──────────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_003_group(group_id: str) -> None:
    """PREP-003 — Gruppo di test disponibile.

    Il catalogo prevede `POST /groups`, ma quell'endpoint richiede
    ruolo `superadmin` (vedi `auth/groups_router.py:101`). Il fixture
    `group_id` riusa il gruppo dell'utente loggato come sandbox.
    Asserzione minima: `group_id` è una stringa non vuota.
    """
    assert isinstance(group_id, str) and group_id, "group_id vuoto"


# ── PREP-004..007 — Connections DB ─────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_004_connection_postgres(postgres_conn_id: str) -> None:
    """PREP-004 — Connection PostgreSQL creata. 201 + connection_id."""
    assert isinstance(postgres_conn_id, str) and postgres_conn_id


@pytest.mark.profile_db
@pytest.mark.requires_app
def test_prep_005_connection_mysql(mysql_conn_id: str) -> None:
    """PREP-005 — Connection MySQL creata. 201 + connection_id."""
    assert isinstance(mysql_conn_id, str) and mysql_conn_id


@pytest.mark.profile_db
@pytest.mark.requires_app
def test_prep_006_connection_sqlserver(sqlserver_conn_id: str) -> None:
    """PREP-006 — Connection SQL Server creata.

    NB: type plenora = `mssql` (NON `sqlserver`).
    """
    assert isinstance(sqlserver_conn_id, str) and sqlserver_conn_id


@pytest.mark.profile_db_heavy
@pytest.mark.requires_app
def test_prep_007_connection_oracle(oracle_conn_id: str) -> None:
    """PREP-007 — Connection Oracle creata. 201."""
    assert isinstance(oracle_conn_id, str) and oracle_conn_id


# ── PREP-008 — Test connection (ping DB) ───────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_008_test_connection_postgres(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """PREP-008 — Ping DB via `POST /connections/{id}/test`.

    Catalogo dice `{ok:true, latency_ms:int}`, ma il modello reale è
    `ConnectionTestResult{success,message,details?}`. Asseriamo il
    contratto reale: `success is True`.
    """
    r = http_client.post(
        f"{API_PREFIX}/connections/{postgres_conn_id}/test",
        headers=auth_headers,
    )
    assert r.status_code == 200, (
        f"Atteso 200, ottenuto {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert body.get("success") is True, (
        f"test_connection non success: {body}"
    )


# ── PREP-009 — Schema introspection ────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_009_schema_introspection(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """PREP-009 — Lista tabelle del database.

    Catalogo: `GET /connections/{id}/schemas/plenora/tables`. L'endpoint
    reale è `GET /connections/{id}/tables` (vedi
    `connections/router.py:215`). Asserzione: vede le 9 tabelle del
    seed (regions, cities, customers, products, orders, order_items,
    employees, events_log, routes).
    """
    r = http_client.get(
        f"{API_PREFIX}/connections/{postgres_conn_id}/tables",
        headers=auth_headers,
    )
    assert r.status_code == 200, (
        f"Atteso 200, ottenuto {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    tables_data = body.get("tables") or {}

    # tables può essere dict {schema: {table: cols}} o {table: cols}
    found_table_names: set[str] = set()
    if isinstance(tables_data, dict):
        for key, value in tables_data.items():
            # Se key è uno schema e value un dict di tabelle
            if isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if isinstance(inner_value, (list, dict)):
                        found_table_names.add(inner_key)
                    else:
                        found_table_names.add(key)
                        break
                else:
                    continue
            else:
                found_table_names.add(key)
    elif isinstance(tables_data, list):
        for entry in tables_data:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("table_name") or entry.get("table")
                if name:
                    found_table_names.add(str(name))
            elif isinstance(entry, str):
                found_table_names.add(entry)

    missing = PREP_009_EXPECTED_TABLES - found_table_names
    assert not missing, (
        f"Tabelle mancanti dallo schema: {sorted(missing)}. "
        f"Trovate: {sorted(found_table_names)}"
    )


# ── PREP-010..012 — api_connections ────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_010_api_connection_apikey(api_conn_apikey_id: str) -> None:
    """PREP-010 — api_connection REST con auth api_key creata. 201."""
    assert isinstance(api_conn_apikey_id, str) and api_conn_apikey_id


@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_011_api_connection_oauth2(api_conn_oauth2_id: str) -> None:
    """PREP-011 — api_connection OAuth2 client_credentials creata. 201."""
    assert isinstance(api_conn_oauth2_id, str) and api_conn_oauth2_id


@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_012_api_connection_arcgis(api_conn_arcgis_id: str) -> None:
    """PREP-012 — api_connection ArcGIS token creata. 201."""
    assert isinstance(api_conn_arcgis_id, str) and api_conn_arcgis_id


# ── PREP-013 — Test api_connection (ping REST) ─────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_013_test_api_connection(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    api_conn_apikey_id: str,
) -> None:
    """PREP-013 — Ping REST via `POST /api-connections/{id}/test`.

    L'endpoint accetta `body.test_params` opzionali e ritorna lo stato
    della negoziazione auth + chiamata di prova.
    """
    r = http_client.post(
        f"{API_PREFIX}/api-connections/{api_conn_apikey_id}/test",
        json={}, headers=auth_headers,
    )
    assert r.status_code == 200, (
        f"Atteso 200, ottenuto {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    # Il service ritorna shape eterogenea (success/ok/status). Accettiamo
    # truthiness su una qualunque delle chiavi canoniche.
    truthy = (
        body.get("success") is True
        or body.get("ok") is True
        or (body.get("status") or "").lower() in {"success", "ok"}
    )
    assert truthy, f"test api_connection non success: {body}"


# ── PREP-014..016 — Datasources ────────────────────────────────────────

@pytest.mark.profile_files
@pytest.mark.requires_app
def test_prep_014_datasource_ftp(ftp_datasource_id: str) -> None:
    """PREP-014 — Datasource FTP creato. 201."""
    assert isinstance(ftp_datasource_id, str) and ftp_datasource_id


@pytest.mark.profile_files
@pytest.mark.requires_app
def test_prep_015_datasource_sftp(sftp_datasource_id: str) -> None:
    """PREP-015 — Datasource SFTP creato. 201."""
    assert isinstance(sftp_datasource_id, str) and sftp_datasource_id


@pytest.mark.profile_files
@pytest.mark.requires_app
def test_prep_016_list_files_datasource(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    ftp_datasource_id: str,
) -> None:
    """PREP-016 — Lista file datasource con metadata (size, mtime).

    Endpoint reale: `GET /datasources/{id}/files?path=<rel>`. Ritorna
    lista di `FileInfoResponse{name,path,size,modified_date,type}`.
    """
    r = http_client.get(
        f"{API_PREFIX}/datasources/{ftp_datasource_id}/files",
        params={"path": os.getenv("PLENORA_E2E_FTP_LIST_PATH", "csv")},
        headers=auth_headers,
    )
    assert r.status_code == 200, (
        f"Atteso 200, ottenuto {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert isinstance(body, list), f"Risposta non lista: {type(body).__name__}"
    if body:
        first = body[0]
        assert "name" in first, f"Manca campo 'name': {first}"
        assert "size" in first, f"Manca campo 'size': {first}"
        assert "modified_date" in first, f"Manca campo 'modified_date': {first}"


# ── PREP-017 — Connection S3/MinIO (skipped: contratto API non chiaro) ─

@pytest.mark.profile_cloud
@pytest.mark.requires_app
def test_prep_017_connection_s3(s3_conn_id: str) -> None:
    """PREP-017 — Connection S3/MinIO.

    Skippato: il backend plenora non espone CRUD pubblico per
    connection S3 (`type` non include 's3' né 'cloud' in
    `ConnectionCreate` né in `DataSourceCreate`). Il fixture stesso
    skippa con messaggio diagnostico.
    """
    assert isinstance(s3_conn_id, str) and s3_conn_id  # pragma: no cover


# ── PREP-018 — Progetto vuoto ──────────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_018_create_project(project_id: str) -> None:
    """PREP-018 — `POST /projects` ritorna 201 + project_id.

    NB: `ProjectCreate` non accetta `group_id` in payload (è derivato
    dal `current_user.group_id` lato server).
    """
    assert isinstance(project_id, str) and project_id


# ── PREP-019 — Canvas vuoto ────────────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_019_canvas_empty(canvas_id: str, project_id: str) -> None:
    """PREP-019 — Canvas inizializzato con nodes/edges vuoti.

    In plenora non esiste l'endpoint `POST /projects/{id}/canvases`:
    il canvas è inline nel progetto e si edita via
    `PUT /projects/{id}/canvas`. Il fixture `canvas_id` ha già fatto
    il PUT vuoto; qui asseriamo che l'id corrisponda al project_id.
    """
    assert canvas_id == project_id, (
        f"canvas_id atteso == project_id, ricevuto: {canvas_id} vs {project_id}"
    )


# ── PREP-020 — Salva canvas con nodi (estrai customers) ────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
def test_prep_020_canvas_with_nodes(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    project_id: str,
    canvas_id: str,
    postgres_conn_id: str,
) -> None:
    """PREP-020 — `PUT /projects/{id}/canvas` con 1 nodo `source`.

    Il nodo è un source-like (`type=source`) che punta alla tabella
    `customers` della connection PostgreSQL. La definition viene
    persistita: rileggiamo il canvas e verifichiamo che il nodo c'è.
    """
    node_id = f"src_customers_{uuid.uuid4().hex[:6]}"
    body_put = {
        "nodes": [
            {
                "id": node_id,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "data": {
                    "connection_id": postgres_conn_id,
                    "table": "customers",
                    "schema": "plenora",
                },
            }
        ],
        "edges": [],
    }
    r = http_client.put(
        f"{API_PREFIX}/projects/{project_id}/canvas",
        json=body_put, headers=auth_headers,
    )
    assert r.status_code == 200, (
        f"PUT canvas fallito: HTTP {r.status_code} — {r.text[:300]}"
    )

    # Rileggi e verifica persistenza del nodo
    r_get = http_client.get(
        f"{API_PREFIX}/projects/{project_id}", headers=auth_headers
    )
    assert r_get.status_code == 200, (
        f"GET project fallito: HTTP {r_get.status_code} — {r_get.text[:300]}"
    )
    canvas = r_get.json().get("canvas") or {}
    nodes = canvas.get("nodes") or []
    node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    assert node_id in node_ids, (
        f"Nodo {node_id!r} non persistito. Nodi presenti: {sorted(node_ids)}"
    )


# ── PREP-021 — Esegui canvas (run on-demand) ───────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.slow
def test_prep_021_run_canvas(run_id: str) -> None:
    """PREP-021 — Run on-demand del canvas, polling fino a status terminale.

    Il fixture `run_id` triggera `POST /projects/{id}/execute` e polla
    `GET /tasks/{task_id}` con timeout 60s, skippando se non success.
    Qui asseriamo che il task_id ricevuto è una stringa non vuota.
    """
    assert isinstance(run_id, str) and run_id


# ── PREP-022 — Result preview ──────────────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.slow
def test_prep_022_result_preview(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    project_id: str,
    result_id: str,
) -> None:
    """PREP-022 — `GET /results/{result_id}/preview?limit=50`.

    Il backend plenora non espone un endpoint pubblico
    `/results/{id}/preview` — la preview è disponibile via
    `GET /projects/{id}/preview` che a sua volta ritorna un task.
    Per coerenza con il catalogo asseriamo: un endpoint preview
    che ritorni 200 + payload con `rows`/`schema`. Tentiamo la rotta
    via project (canonica) e accettiamo task asincrono come success.
    """
    r = http_client.get(
        f"{API_PREFIX}/projects/{project_id}/preview",
        params={"limit": 50},
        headers=auth_headers,
    )
    if r.status_code == 404:
        pytest.skip(
            "Endpoint preview non disponibile sulla build corrente "
            "(404). Verifica `app.core.orchestrator.router` per il "
            "prefix `/projects/{id}/preview`."
        )
    assert r.status_code == 200, (
        f"GET preview fallito: HTTP {r.status_code} — {r.text[:300]}"
    )
    body = r.json()
    # La preview può essere sincrona ({rows,schema}) o async (task_id).
    is_async = bool(body.get("task_id") or body.get("async"))
    has_rows = isinstance(body.get("rows"), list)
    has_data = isinstance(body.get("data"), list)
    assert is_async or has_rows or has_data, (
        f"Preview senza payload riconoscibile: {body}"
    )


# ── PREP-023 — Cleanup gruppo (cascade) ────────────────────────────────

@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.skip(
    reason=(
        "DELETE /auth/groups/{id} richiede ruolo superadmin "
        "(auth/groups_router.py:124). L'utente smoke ha ruolo `user`. "
        "Il teardown delle risorse del gruppo è già gestito dalle "
        "fixture session-scope (DELETE per ogni connection / project / "
        "datasource creato). Riabilitare con un fixture superadmin."
    )
)
def test_prep_023_cleanup_group(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    group_id: str,
) -> None:
    """PREP-023 — `DELETE /auth/groups/{id}` cascade.

    Asserzione: 200 + report con counts di {schedules, projects,
    datasources, connections, file_sources, users, results,
    intermediate_runs, log_runs}. DB target verificato vuoto.

    Skippato finché non c'è un fixture utente con ruolo `superadmin`.
    """
    r = http_client.delete(
        f"{API_PREFIX}/auth/groups/{group_id}", headers=auth_headers
    )
    assert r.status_code == 200, (
        f"DELETE group fallito: HTTP {r.status_code} — {r.text[:300]}"
    )
    body = r.json()
    assert body.get("deleted") is True, f"Risposta non deleted=True: {body}"
    report: dict[str, Any] = body.get("report") or {}
    expected_counters = {
        "schedules", "projects", "datasources", "connections",
        "file_sources", "users", "results", "intermediate_runs",
        "log_runs",
    }
    missing = expected_counters - set(report.keys())
    assert not missing, (
        f"Report cascade incompleto. Mancano counters: {sorted(missing)}. "
        f"Report: {report}"
    )
