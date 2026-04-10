"""
conftest.py per tests_v5/carica/integration/
Contiene tutte le funzioni builder e fixture inline — non dipende dal conftest globale.
"""
import time
import pytest
from typing import Dict, Any, List, Optional


# =============================================================================
# COSTANTI
# =============================================================================

MOCK_ARCGIS_URL     = "http://localhost:8999/arcgis"
MOCK_ARCGIS_LAYER_0 = "http://localhost:8999/arcgis/rest/services/mock/FeatureServer/0"
MOCK_ARCGIS_TOKEN   = "mock-arcgis-token-123"

TASK_POLL_TIMEOUT  = 120
TASK_POLL_INTERVAL = 1.0


# =============================================================================
# HELPER: POLLING
# =============================================================================

def poll_task(client, task_id: str, timeout: int = TASK_POLL_TIMEOUT) -> Dict[str, Any]:
    """Polling su /api/tasks/{task_id} fino a SUCCESS, FAILURE o timeout."""
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


# =============================================================================
# CANVAS BUILDER — CARICA
# =============================================================================

def build_carica_canvas(
    file_source_id: str,
    output_format: str = "csv",
    output_filename: str = "output_carica",
) -> Dict:
    """Canvas CARICA: file_source → result."""
    return {
        "nodes": [
            {
                "id":             "node-source",
                "type":           "file_source",
                "position":       {"x": 100, "y": 200},
                "file_source_id": file_source_id,
                "fileId":         file_source_id,
                "fileName":       "source.csv",
                "columns":        [],
            },
            {
                "id":       "node-result",
                "type":     "result",
                "position": {"x": 400, "y": 200},
                "fileName": output_filename,
                "format":   output_format,
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-source", "target": "node-result"},
        ],
    }


# =============================================================================
# HELPER: ESECUZIONE CARICA
# =============================================================================

def execute_carica(
    client,
    project_id: str,
    canvas: Dict,
    carica_node_data: Dict,
    output_format: str = "csv",
    timeout: int = 120,
) -> Dict[str, Any]:
    """Esegue un task CARICA via POST /projects/{id}/carica-async."""
    payload = {
        "carica_node_data":  carica_node_data,
        "format":            output_format,
        "force_recalculate": True,
    }
    res = client.post(f"/projects/{project_id}/carica-async", json=payload)
    assert res.status_code == 200, f"Errore avvio CARICA: {res.text}"
    task_id = res.json().get("task_id")
    assert task_id, f"task_id non trovato: {res.json()}"
    return poll_task(client, task_id, timeout=timeout)


# =============================================================================
# NODE BUILDER — DB
# =============================================================================

def make_db_carica_node(
    connection_id: str,
    table_name: str,
    write_mode: str = "create",
    schema_name: str = "public",
    column_mappings: Optional[list] = None,
    key_column: Optional[str] = None,
    srid: int = 4326,
    geometry_column: str = "geometry",
    batch_size: int = 1000,
    transaction_mode: str = "all_or_nothing",
) -> Dict:
    """Costruisce il nodo CARICA per scrittura su database."""
    node = {
        "type":             "carica",
        "dest_type":        "db",
        "connection_id":    connection_id,
        "table_name":       table_name,
        "schema_name":      schema_name,
        "write_mode":       write_mode,
        "srid":             srid,
        "geometry_column":  geometry_column,
        "batch_size":       batch_size,
        "transaction_mode": transaction_mode,
        "column_mappings":  column_mappings or [],
    }
    if key_column:
        node["match_columns"]     = [key_column]
        node["key_column"]        = key_column
        node["key_columns"]       = [key_column]
        node["unique_key_column"] = key_column
    return node


# =============================================================================
# NODE BUILDER — ARCGIS
# =============================================================================

def make_arcgis_carica_node(
    connection_id: str,
    layer_url: str,
    operation: str = "add",
    geometry_column: str = "geometry",
    source_srid: int = 4326,
    batch_size: int = 500,
) -> Dict:
    """Costruisce il nodo CARICA per scrittura su ArcGIS Feature Service."""
    return {
        "type":                 "carica",
        "dest_type":            "arcgis",
        "arcgis_connection_id": connection_id,
        "arcgis_layer_url":     layer_url,
        "arcgis_operation":     operation,
        "geometry_column":      geometry_column,
        "source_srid":          source_srid,
        "batch_size":           batch_size,
    }


# =============================================================================
# FIXTURE CSV PER TEST CRITICI C-31→C-45
# =============================================================================

import csv as _csv
import io as _io
import pytest


@pytest.fixture(scope="session")
def csv_immobili(client) -> str:
    """CSV sintetico immobili per test CARICA."""
    rows = [{"id": i, "comune": f"Comune_{i % 10}", "valore": float(i * 100),
             "superficie": 50 + i % 100} for i in range(1, 201)]
    buf = _io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=["id", "comune", "valore", "superficie"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post("/files/upload",
                      files={"file": ("carica_immobili.csv", data, "text/csv")},
                      data={"name": "[AUTO] C immobili"})
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


@pytest.fixture(scope="session")
def csv_carica_type_error(client) -> str:
    """CSV con tipo misto (numeri + stringhe) nella colonna numero."""
    rows = [
        {"id": 1, "numero": "95",    "nome": "Milano"},
        {"id": 2, "numero": "100",   "nome": "Roma"},
        {"id": 3, "numero": "26A",   "nome": "Napoli"},
        {"id": 4, "numero": "150",   "nome": "Torino"},
        {"id": 5, "numero": "200B",  "nome": "Firenze"},
    ]
    buf = _io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=["id", "numero", "nome"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post("/files/upload",
                      files={"file": ("carica_tipo_misto.csv", data, "text/csv")},
                      data={"name": "[AUTO] C tipo misto"})
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


@pytest.fixture(scope="session")
def csv_carica_points(client) -> str:
    """CSV con 200 punti WKT per test geometria."""
    rows = []
    for i in range(200):
        lon = 9.0 + (i % 20) * 0.01
        lat = 45.0 + (i // 20) * 0.01
        rows.append({"id": i+1, "nome": f"Feature_{i+1:03d}", "valore": round(i*2.5, 2),
                     "geometry": f"POINT({lon:.4f} {lat:.4f})"})
    buf = _io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=["id","nome","valore","geometry"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post("/files/upload",
                      files={"file": ("carica_points.csv", data, "text/csv")},
                      data={"name": "[AUTO] C points"})
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


# =============================================================================
# FIXTURE: cleanup tabelle carica dopo ogni test
# =============================================================================

@pytest.fixture(autouse=True)
def cleanup_carica_tables(pg_conn, client):
    """
    Pulisce automaticamente le tabelle create dai test CARICA.
    Usa DROP TABLE IF EXISTS ... CASCADE su tutte le tabelle
    con prefisso 'c_crit_', 'c_ext_', 'carica_' dopo ogni test.
    """
    yield  # esegui il test prima

    # Teardown: elimina tabelle di test via API o direttamente via psycopg2
    try:
        import psycopg2
        # Recupera i parametri di connessione dalla connessione pg_conn
        res = client.get(f"/connections/{pg_conn}")
        if res.status_code != 200:
            return
        conn_data = res.json()
        host = conn_data.get("host", "localhost")
        port = conn_data.get("port", 5432)
        dbname = conn_data.get("database", "postgres")
        user = conn_data.get("username", "postgres")
        password = conn_data.get("password", "postgres")

        with psycopg2.connect(
            host=host, port=port, dbname=dbname,
            user=user, password=password,
            connect_timeout=5
        ) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                # Trova tutte le tabelle con prefissi di test
                cur.execute("""
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public'
                    AND (
                        tablename LIKE 'c_crit_%'
                        OR tablename LIKE 'c_ext_%'
                        OR tablename LIKE 'carica_test_%'
                        OR tablename LIKE 'auto_c_%'
                        OR tablename LIKE 'test_carica_%'
                    )
                """)
                tables = [row[0] for row in cur.fetchall()]
                for table in tables:
                    cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    except Exception as e:
        # Non bloccare i test se il cleanup fallisce
        print(f"[WARN] Cleanup tabelle CARICA fallito: {e}")
