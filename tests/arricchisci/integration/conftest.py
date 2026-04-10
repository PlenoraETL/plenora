"""
conftest.py per tests_v5/arricchisci/integration/
Contiene tutte le funzioni builder e fixture inline — non dipende dal conftest globale.
"""
import csv
import io
import uuid
import pytest
from typing import Dict, Any, List, Optional, Callable


# =============================================================================
# COSTANTI MOCK SERVER
# =============================================================================

MOCK_URL                  = "http://localhost:8999"
MOCK_BEARER_TOKEN         = "mock-access-token-1"
MOCK_API_KEY              = "test-api-key-valid"
MOCK_BASIC_USER           = "testuser"
MOCK_BASIC_PASSWORD       = "testpass"
MOCK_OAUTH2_CLIENT_ID     = "test-client"
MOCK_OAUTH2_CLIENT_SECRET = "test-secret"
MOCK_OAUTH2_TOKEN_URL     = f"{MOCK_URL}/auth/token"


# =============================================================================
# GENERATORI CSV
# =============================================================================

_PAESI = [
    {"paese": "Italy",       "codice": "IT"},
    {"paese": "France",      "codice": "FR"},
    {"paese": "Germany",     "codice": "DE"},
    {"paese": "Spain",       "codice": "ES"},
    {"paese": "Portugal",    "codice": "PT"},
    {"paese": "Netherlands", "codice": "NL"},
    {"paese": "Belgium",     "codice": "BE"},
    {"paese": "Austria",     "codice": "AT"},
    {"paese": "Sweden",      "codice": "SE"},
    {"paese": "Poland",      "codice": "PL"},
]

_CITTA = [
    {"citta": "Rome",      "paese": "Italy"},
    {"citta": "Paris",     "paese": "France"},
    {"citta": "Berlin",    "paese": "Germany"},
    {"citta": "Madrid",    "paese": "Spain"},
    {"citta": "Lisbon",    "paese": "Portugal"},
    {"citta": "Amsterdam", "paese": "Netherlands"},
    {"citta": "Brussels",  "paese": "Belgium"},
    {"citta": "Vienna",    "paese": "Austria"},
    {"citta": "Stockholm", "paese": "Sweden"},
    {"citta": "Warsaw",    "paese": "Poland"},
]


def _to_csv_bytes(rows: List[Dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _gen_csv_paesi() -> bytes:
    return _to_csv_bytes(_PAESI)

def _gen_csv_paesi_con_null() -> bytes:
    rows = [
        {"paese": "Italy",   "codice": "IT"},
        {"paese": "France",  "codice": "FR"},
        {"paese": "",        "codice": "??"},
        {"paese": "Germany", "codice": "DE"},
        {"paese": "",        "codice": "??"},
    ]
    return _to_csv_bytes(rows)

def _gen_csv_paesi_con_invalidi() -> bytes:
    rows = [
        {"paese": "Italy",          "codice": "IT"},
        {"paese": "France",         "codice": "FR"},
        {"paese": "XXXINVALIDXXX",  "codice": "??"},
        {"paese": "Germany",        "codice": "DE"},
        {"paese": "XXXINVALIDXXX2", "codice": "??"},
        {"paese": "XXXINVALIDXXX3", "codice": "??"},
    ]
    return _to_csv_bytes(rows)

def _gen_csv_citta() -> bytes:
    return _to_csv_bytes(_CITTA)

def _gen_csv_empty() -> bytes:
    return b"paese,codice\n"

def _gen_csv_latin1() -> bytes:
    lines = [
        "paese,codice,note",
        "Italy,IT,Città eterna",
        "France,FR,Île-de-France",
        "Spain,ES,Región Capital",
        "Portugal,PT,Histórica",
        "Sweden,SE,Östermalm",
    ]
    return "\n".join(lines).encode("latin-1")

def _gen_csv_semicolon() -> bytes:
    lines = [
        "paese;codice;popolazione",
        "Italy;IT;60317000",
        "France;FR;67391000",
        "Germany;DE;83240000",
        "Spain;ES;47351000",
        "Portugal;PT;10196700",
    ]
    return "\n".join(lines).encode("utf-8")

def _gen_csv_paesi_ripetuti() -> bytes:
    rows = [{"paese": p, "codice": c} for p, c in [
        ("Italy",   "IT"), ("France",  "FR"), ("Germany", "DE"),
        ("Italy",   "IT"), ("France",  "FR"), ("Germany", "DE"),
        ("Italy",   "IT"), ("France",  "FR"), ("Italy",   "IT"),
        ("Germany", "DE"),
    ]]
    return _to_csv_bytes(rows)

def _gen_csv_100_righe() -> bytes:
    paesi = [("Italy", "IT"), ("France", "FR"), ("Germany", "DE")]
    rows = [{"paese": paesi[i % 3][0], "codice": paesi[i % 3][1]} for i in range(100)]
    return _to_csv_bytes(rows)


# =============================================================================
# FIXTURE FILE (scope=module)
# =============================================================================

def _upload_csv(client, filename: str, data: bytes, tag: str) -> str:
    res = client.post(
        "/files/upload",
        files={"file": (filename, data, "text/csv")},
        data={"name": f"[AUTO] A {tag}"},
    )
    assert res.status_code == 200, f"Upload {tag} fallito: {res.text}"
    return res.json()["id"]


@pytest.fixture(scope="module")
def csv_paesi(client) -> str:
    fid = _upload_csv(client, "paesi.csv", _gen_csv_paesi(), "paesi")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_paesi_con_null(client) -> str:
    fid = _upload_csv(client, "paesi_null.csv", _gen_csv_paesi_con_null(), "paesi null")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_paesi_con_invalidi(client) -> str:
    fid = _upload_csv(client, "paesi_invalidi.csv", _gen_csv_paesi_con_invalidi(), "paesi invalidi")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_citta(client) -> str:
    fid = _upload_csv(client, "citta.csv", _gen_csv_citta(), "città")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_empty(client) -> str:
    fid = _upload_csv(client, "empty.csv", _gen_csv_empty(), "empty")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_latin1(client) -> str:
    fid = _upload_csv(client, "paesi_latin1.csv", _gen_csv_latin1(), "latin1")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_semicolon(client) -> str:
    fid = _upload_csv(client, "paesi_semicolon.csv", _gen_csv_semicolon(), "semicolon")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_paesi_ripetuti(client) -> str:
    fid = _upload_csv(client, "paesi_ripetuti.csv", _gen_csv_paesi_ripetuti(), "paesi ripetuti")
    yield fid
    client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def csv_100_righe(client) -> str:
    fid = _upload_csv(client, "paesi_100.csv", _gen_csv_100_righe(), "100 righe")
    yield fid
    client.delete(f"/files/{fid}")


# =============================================================================
# FIXTURE: make_api_connection
# =============================================================================

@pytest.fixture
def make_api_connection(client) -> Callable:
    created_ids = []

    def _create(
        url: str,
        auth_type: str = "none",
        output_mapping: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        name: Optional[str] = None,
        method: str = "GET",
        api_type: str = "enricher",
        bearer_token: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_header: str = "X-Api-Key",
        basic_username: Optional[str] = None,
        basic_password: Optional[str] = None,
        oauth2_token_url: Optional[str] = None,
        oauth2_client_id: Optional[str] = None,
        oauth2_client_secret: Optional[str] = None,
        extra_payload: Optional[Dict] = None,
    ) -> str:
        auth: Dict[str, Any] = {"type": auth_type}

        if auth_type == "bearer":
            auth["token"] = bearer_token or MOCK_BEARER_TOKEN
        elif auth_type == "api_key":
            auth["key_name"]  = api_key_header
            auth["key_value"] = api_key or MOCK_API_KEY
            auth["location"]  = "header"
        elif auth_type == "basic":
            auth["username"] = basic_username or MOCK_BASIC_USER
            auth["password"] = basic_password or MOCK_BASIC_PASSWORD
        elif auth_type == "oauth2_client_credentials":
            auth["token_url"]     = oauth2_token_url or MOCK_OAUTH2_TOKEN_URL
            auth["client_id"]     = oauth2_client_id or MOCK_OAUTH2_CLIENT_ID
            auth["client_secret"] = oauth2_client_secret or MOCK_OAUTH2_CLIENT_SECRET

        payload = {
            "name":           name or f"[AUTO] A mock_{auth_type}_{uuid.uuid4().hex[:8]}",
            "url":            url,
            "method":         method,
            "api_type":       api_type,
            "auth":           auth,
            "output_mapping": output_mapping or [],
            "options": {
                "rate_limit":  0,
                "timeout":     10,
                "retry_max":   1,
                **(options or {}),
            },
            **(extra_payload or {}),
        }

        res = client.post("/api-connections", json=payload)
        assert res.status_code == 200, f"Errore creazione API connection: {res.text}"
        api_id = res.json()["id"]
        created_ids.append(api_id)
        return api_id

    yield _create

    for aid in created_ids:
        try:
            client.delete(f"/api-connections/{aid}")
        except Exception as e:
            print(f"[WARN] Teardown API connection {aid}: {e}")


# =============================================================================
# CANVAS BUILDER
# =============================================================================

def build_arricchisci_canvas(
    file_source_id: str,
    api_id: str,
    param_mapping: Optional[Dict[str, str]] = None,
    output_format: str = "csv",
    output_filename: str = "output",
    continue_on_error: bool = False,
    max_errors: Optional[int] = None,
    parallel_workers: int = 1,
) -> Dict:
    enrich_node: Dict[str, Any] = {
        "id":               "node-enrich",
        "type":             "api_enrich",
        "position":         {"x": 400, "y": 200},
        "api_id":           api_id,
        "param_mapping":    param_mapping or {},
        "continue_on_error": continue_on_error,
        "parallel_workers": parallel_workers,
    }
    if max_errors is not None:
        enrich_node["max_errors"] = max_errors

    return {
        "nodes": [
            {"id": "node-src", "type": "file_source", "position": {"x": 100, "y": 200},
             "file_source_id": file_source_id, "columns": []},
            enrich_node,
            {"id": "node-result", "type": "result", "position": {"x": 700, "y": 200},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [
            {"id": "e1", "source": "node-src",    "target": "node-enrich"},
            {"id": "e2", "source": "node-enrich",  "target": "node-result"},
        ],
    }


def build_arricchisci_canvas_chain(
    file_source_id: str,
    api_id_1: str,
    api_id_2: str,
    param_mapping_1: Optional[Dict[str, str]] = None,
    param_mapping_2: Optional[Dict[str, str]] = None,
    output_format: str = "csv",
    output_filename: str = "output_chain",
) -> Dict:
    return {
        "nodes": [
            {"id": "node-src", "type": "file_source", "position": {"x": 100, "y": 200},
             "file_source_id": file_source_id, "columns": []},
            {"id": "node-enrich-1", "type": "api_enrich", "position": {"x": 350, "y": 200},
             "api_id": api_id_1, "param_mapping": param_mapping_1 or {},
             "continue_on_error": False, "parallel_workers": 1},
            {"id": "node-enrich-2", "type": "api_enrich", "position": {"x": 600, "y": 200},
             "api_id": api_id_2, "param_mapping": param_mapping_2 or {},
             "continue_on_error": False, "parallel_workers": 1},
            {"id": "node-result", "type": "result", "position": {"x": 850, "y": 200},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [
            {"id": "e1", "source": "node-src",      "target": "node-enrich-1"},
            {"id": "e2", "source": "node-enrich-1",  "target": "node-enrich-2"},
            {"id": "e3", "source": "node-enrich-2",  "target": "node-result"},
        ],
    }


def build_arricchisci_canvas_no_src(
    api_id: str,
    output_format: str = "csv",
    output_filename: str = "output_generator",
) -> Dict:
    return {
        "nodes": [
            {"id": "node-enrich", "type": "api_enrich", "position": {"x": 300, "y": 200},
             "api_id": api_id, "api_type": "generator", "param_mapping": {},
             "continue_on_error": False, "parallel_workers": 1},
            {"id": "node-result", "type": "result", "position": {"x": 600, "y": 200},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [
            {"id": "e1", "source": "node-enrich", "target": "node-result"},
        ],
    }


# =============================================================================
# HELPER: POLLING E ESECUZIONE ARRICCHISCI
# =============================================================================

TASK_POLL_TIMEOUT  = 120
TASK_POLL_INTERVAL = 1.0


def poll_task(client, task_id: str, timeout: int = TASK_POLL_TIMEOUT) -> Dict[str, Any]:
    """Polling su /api/tasks/{task_id} fino a SUCCESS, FAILURE o timeout."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/tasks/{task_id}")
        if res.status_code == 404:
            time.sleep(TASK_POLL_INTERVAL)
            continue
        assert res.status_code == 200, f"Errore polling task: {res.text}"
        data = res.json()
        status = data.get("status", "")
        if status in ("success", "SUCCESS"):
            return data.get("result", data)
        if status in ("failure", "FAILURE", "revoked", "REVOKED"):
            raise AssertionError(f"Task {task_id} terminato con status {status}: {data}")
        time.sleep(TASK_POLL_INTERVAL)
    raise TimeoutError(f"Task {task_id} non completato entro {timeout}s")


def execute_arricchisci(client, project_id: str, timeout: int = 120) -> Dict[str, Any]:
    res = client.post(f"/projects/{project_id}/enrich-async")
    assert res.status_code == 200, f"Errore avvio esecuzione: {res.text}"
    task_id = res.json().get("task_id")
    assert task_id, f"task_id non trovato: {res.json()}"
    result = poll_task(client, task_id, timeout=timeout)  # noqa
    if "rows_processed" in result and "rows_count" not in result:
        result["rows_count"] = result["rows_processed"]
    return result


# =============================================================================
# CANVAS BUILDER — ESTRAI (usato da A-44, A-55→A-74)
# =============================================================================

def build_estrai_canvas(
    table_name: str,
    columns: List[Dict],
    result_filename: str = "output",
    result_format: str = "csv",
    filters=None,
    limit=None,
) -> Dict:
    """Canvas ESTRAI minimale: source → result."""
    return {
        "nodes": [
            {
                "id": "node-source-1",
                "type": "source",
                "position": {"x": 100, "y": 200},
                "table_name": table_name,
                "columns": columns,
                "filters": filters or [],
            },
            {
                "id": "node-result-1",
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": result_filename,
                "format": result_format,
                "limit": limit,
            },
        ],
        "edges": [{"id": "edge-1", "source": "node-source-1", "target": "node-result-1"}],
    }


# =============================================================================
# CLEANUP AUTOMATICO CONNETTORI [AUTO] rimasti da run precedenti
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def cleanup_auto_api_connections(client):
    """Pulisce connettori [AUTO] rimasti da run precedenti PRIMA di iniziare."""
    try:
        res = client.get("/api-connections")
        if res.status_code == 200:
            conns = res.json().get("api_connections", [])
            deleted = 0
            for c in conns:
                if c.get("name", "").startswith("[AUTO]"):
                    client.delete(f"/api-connections/{c['id']}")
                    deleted += 1
            if deleted:
                print(f"\n[CLEANUP] Eliminati {deleted} connettori [AUTO] da run precedenti")
    except Exception as e:
        print(f"\n[CLEANUP WARN] Errore cleanup connettori: {e}")
    yield
    # Cleanup finale a fine sessione
    try:
        res = client.get("/api-connections")
        if res.status_code == 200:
            conns = res.json().get("api_connections", [])
            for c in conns:
                if c.get("name", "").startswith("[AUTO]"):
                    client.delete(f"/api-connections/{c['id']}")
    except Exception:
        pass
