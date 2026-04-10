"""
conftest.py — Fixture globali per la suite di test automatici URBY-ETL.

Architettura
============
I test girano contro il backend FastAPI reale (localhost:8000) già avviato.
Celery usa SQLite in modalità ALWAYS_EAGER — i task girano sincroni.

Fixture disponibili:
  - client          → sessione requests con JWT auth e base URL
  - pg_conn         → connessione PostgreSQL registrata via API (cleanup automatico)
  - oracle_conn     → connessione Oracle registrata via API (cleanup automatico)
  - mssql_conn      → connessione SQL Server registrata via API (cleanup automatico)
  - mysql_conn      → connessione MySQL registrata via API (cleanup automatico)
  - mariadb_conn    → connessione MariaDB registrata via API (cleanup automatico)
  - pg_table        → factory: crea tabella PG + dati, la droppa al teardown
  - make_project    → factory: crea progetto ESTRAI, lo elimina al teardown
  - poll_task       → helper: poll su task_id fino a SUCCESS o timeout

Credenziali
===========
Tutte le credenziali Docker sono in TEST_DB_CONFIG (sezione in fondo).
L'utente di test (admin) deve esistere nel backend prima di lanciare i test.

Uso tipico in un test
=====================
    def test_E01(pg_conn, pg_table, make_project, client):
        table = pg_table(
            conn_id=pg_conn,
            table_name="test_e01_points",
            ddl="id SERIAL PRIMARY KEY, nome TEXT, geom geometry(Point,4326)",
            rows=[
                "INSERT INTO test_e01_points (nome, geom) VALUES "
                "('Roma', ST_GeomFromText('POINT(12.4964 41.9028)', 4326))"
            ]
        )
        project_id = make_project(
            name="E-01 Test",
            project_type="estrai",
            connection_id=pg_conn,
            canvas={...}
        )
        res = client.post(f"/api/projects/{project_id}/execute")
        task_id = res.json()["task_id"]
        result = poll_task(client, task_id)
        assert result["success"] is True
"""

import time
import uuid
import pytest
import psycopg2
import requests
from typing import Callable, Dict, Any, Optional, List

# =============================================================================
# CONFIGURAZIONE
# =============================================================================

BASE_URL = "http://localhost:8000/api"

# Utente di test — deve esistere nel backend
TEST_USER = "admin"
TEST_PASSWORD = "admin123"

# Credenziali Docker
TEST_DB_CONFIG = {
    "postgres": {
        "type": "postgres",
        "host": "127.0.0.1",  # forza IPv4 — localhost risolve in ::1 su Windows
        "port": 5432,
        "database": "dataflow_test",
        "username": "dataflow",
        "password": "dataflow_test_2026",
    },
    "mssql": {
        "type": "mssql",
        "host": "localhost",
        "port": 1433,
        "database": "master",
        "username": "sa",
        "password": "DataFlow_Test_2026!",
    },
    "mysql": {
        "type": "mysql",
        "host": "localhost",
        "port": 3306,
        "database": "dataflow_test",
        "username": "dataflow",
        "password": "dataflow_test_2026",
    },
    "mariadb": {
        "type": "mysql",          # il backend usa driver mysql anche per MariaDB
        "host": "localhost",
        "port": 3307,
        "database": "dataflow_test",
        "username": "dataflow",
        "password": "dataflow_test_2026",
    },
    "oracle": {
        "type": "oracle",
        "host": "localhost",
        "port": 1521,
        "database": "FREEPDB1",   # service name Oracle Free 23ai
        "username": "dataflow",
        "password": "dataflow_test_2026",
    },
}

# Timeout polling task (secondi)
TASK_POLL_TIMEOUT = 60
TASK_POLL_INTERVAL = 1


# =============================================================================
# CLIENT AUTENTICATO
# =============================================================================

def _get_token() -> str:
    """Fa login e restituisce il JWT token."""
    res = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": TEST_USER, "password": TEST_PASSWORD},
    )
    assert res.status_code == 200, f"Login fallito: {res.text}"
    return res.json()["access_token"]


class _AuthClient:
    """
    Wrapper leggero intorno a requests.Session.
    Aggiunge automaticamente il JWT token e il BASE_URL a ogni chiamata.
    """
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.session.get(f"{BASE_URL}{path}", **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.session.post(f"{BASE_URL}{path}", **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.session.put(f"{BASE_URL}{path}", **kwargs)

    def patch(self, path: str, **kwargs) -> requests.Response:
        return self.session.patch(f"{BASE_URL}{path}", **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.session.delete(f"{BASE_URL}{path}", **kwargs)


@pytest.fixture(scope="session")
def client() -> _AuthClient:
    """Client HTTP autenticato — riusato per tutta la session di test."""
    token = _get_token()
    return _AuthClient(token)


# =============================================================================
# HELPER: POLLING TASK
# =============================================================================

def poll_task(
    client: _AuthClient,
    task_id: str,
    timeout: int = TASK_POLL_TIMEOUT,
    interval: float = TASK_POLL_INTERVAL,
) -> Dict[str, Any]:
    """
    Fa polling su /api/tasks/{task_id} fino a SUCCESS, FAILURE o timeout.

    Returns:
        Il dict del risultato task (campo 'result' o intero body).

    Raises:
        TimeoutError se il task non termina entro `timeout` secondi.
        AssertionError se il task termina in FAILURE.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/tasks/{task_id}")

        # 404 = task non ancora registrato dal worker (PENDING iniziale) → riprova
        if res.status_code == 404:
            time.sleep(interval)
            continue

        assert res.status_code == 200, f"Errore polling task: {res.text}"
        data = res.json()
        status = data.get("status", "")

        if status in ("success", "SUCCESS"):
            return data.get("result", data)
        if status in ("failure", "FAILURE", "revoked", "REVOKED"):
            raise AssertionError(f"Task {task_id} terminato con status {status}: {data}")

        time.sleep(interval)

    raise TimeoutError(f"Task {task_id} non completato entro {timeout}s")


@pytest.fixture
def poll(client: _AuthClient) -> Callable:
    """Fixture che espone poll_task con client già iniettato."""
    def _poll(task_id: str, timeout: int = TASK_POLL_TIMEOUT) -> Dict[str, Any]:
        return poll_task(client, task_id, timeout=timeout)
    return _poll


# =============================================================================
# FIXTURE: REGISTRAZIONE CONNESSIONI VIA API
# =============================================================================

def _register_connection(client: _AuthClient, db_key: str, name: str = None) -> str:
    """
    Registra una connessione DB nel backend via POST /api/connections.
    Restituisce il connection_id.
    """
    cfg = TEST_DB_CONFIG[db_key].copy()
    cfg["name"] = name or f"test_{db_key}_{uuid.uuid4().hex[:8]}"
    cfg["description"] = f"Connessione di test automatico [{db_key}]"

    res = client.post("/connections", json=cfg)
    assert res.status_code == 200, f"Errore creazione connessione {db_key}: {res.text}"
    return res.json()["id"]


def _delete_connection(client: _AuthClient, connection_id: str):
    """Elimina una connessione dal backend."""
    client.delete(f"/connections/{connection_id}")


@pytest.fixture
def pg_conn(client: _AuthClient) -> str:
    """Registra connessione PostgreSQL → yield connection_id → cleanup."""
    conn_id = _register_connection(client, "postgres")
    yield conn_id
    _delete_connection(client, conn_id)


@pytest.fixture
def oracle_conn(client: _AuthClient) -> str:
    """Registra connessione Oracle → yield connection_id → cleanup."""
    conn_id = _register_connection(client, "oracle")
    yield conn_id
    _delete_connection(client, conn_id)


@pytest.fixture
def mssql_conn(client: _AuthClient) -> str:
    """Registra connessione SQL Server → yield connection_id → cleanup."""
    conn_id = _register_connection(client, "mssql")
    yield conn_id
    _delete_connection(client, conn_id)


@pytest.fixture
def mysql_conn(client: _AuthClient) -> str:
    """Registra connessione MySQL → yield connection_id → cleanup."""
    conn_id = _register_connection(client, "mysql")
    yield conn_id
    _delete_connection(client, conn_id)


@pytest.fixture
def mariadb_conn(client: _AuthClient) -> str:
    """Registra connessione MariaDB → yield connection_id → cleanup."""
    conn_id = _register_connection(client, "mariadb")
    yield conn_id
    _delete_connection(client, conn_id)


# =============================================================================
# FIXTURE: CREAZIONE TABELLE SUI DB
# =============================================================================

@pytest.fixture
def pg_table() -> Callable:
    """
    Factory per creare/droppare tabelle PostgreSQL.

    Uso:
        table_name = pg_table(
            ddl="id SERIAL PRIMARY KEY, nome TEXT, geom geometry(Point,4326)",
            rows=["INSERT INTO {table} VALUES (1, 'Roma', ST_GeomFromText(...))"],
            table_name="test_mia_tabella"   # opzionale, auto-generato se omesso
        )
    """
    created = []

    def _create(
        ddl: str,
        rows: Optional[List[str]] = None,
        table_name: Optional[str] = None,
        schema: str = "public",
    ) -> str:
        cfg = TEST_DB_CONFIG["postgres"]
        name = table_name or f"test_{uuid.uuid4().hex[:12]}"
        full_name = f"{schema}.{name}" if schema != "public" else name

        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], dbname=cfg["database"],
            user=cfg["username"], password=cfg["password"]
        )
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute(f"DROP TABLE IF EXISTS {full_name} CASCADE")
        cur.execute(f"CREATE TABLE {full_name} ({ddl})")

        for row_sql in (rows or []):
            # sostituisce {table} con il nome reale se presente nella query
            cur.execute(row_sql.replace("{table}", full_name))

        cur.close()
        conn.close()

        created.append((full_name, cfg))
        return name  # restituisce il nome senza schema (per canvas JSON)

    yield _create

    # Teardown: droppa tutte le tabelle create
    for full_name, cfg in created:
        try:
            conn = psycopg2.connect(
                host=cfg["host"], port=cfg["port"], dbname=cfg["database"],
                user=cfg["username"], password=cfg["password"]
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {full_name} CASCADE")
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[WARN] Errore teardown tabella {full_name}: {e}")


# =============================================================================
# FIXTURE: CREAZIONE PROGETTI VIA API
# =============================================================================

@pytest.fixture
def make_project(client: _AuthClient) -> Callable:
    """
    Factory per creare/eliminare progetti URBY-ETL.

    Uso:
        project_id = make_project(
            name="E-01 Test",
            project_type="estrai",
            connection_id="...",
            canvas={...}          # opzionale: aggiorna il canvas dopo la creazione
        )
    """
    created_ids = []

    def _create(
        name: str,
        project_type: str = "estrai",
        connection_id: Optional[str] = None,
        canvas: Optional[Dict[str, Any]] = None,
        description: str = "",
    ) -> str:
        payload = {
            "name": name,
            "project_type": project_type,
            "description": description,
        }
        if connection_id:
            payload["default_connection_id"] = connection_id

        res = client.post("/projects", json=payload)
        assert res.status_code == 200, f"Errore creazione progetto: {res.text}"
        body = res.json()
        project_id = body.get("id") or body.get("metadata", {}).get("id")
        assert project_id, f"id progetto non trovato nella risposta: {body}"
        created_ids.append(project_id)

        # Aggiorna canvas se fornito
        if canvas:
            res2 = client.put(f"/projects/{project_id}/canvas", json=canvas)
            assert res2.status_code == 200, f"Errore aggiornamento canvas: {res2.text}"

        return project_id

    yield _create

    # Teardown: elimina tutti i progetti creati
    for pid in created_ids:
        try:
            client.delete(f"/projects/{pid}")
        except Exception as e:
            print(f"[WARN] Errore teardown progetto {pid}: {e}")


# =============================================================================
# HELPER: CANVAS BUILDER (utility per costruire canvas JSON rapidamente)
# =============================================================================

def build_estrai_canvas(
    table_name: str,
    columns: List[Dict],
    result_filename: str = "output",
    result_format: str = "csv",
    filters: Optional[List[Dict]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Costruisce un canvas ESTRAI minimale con un nodo source + un nodo result.

    Args:
        table_name:       nome tabella sorgente
        columns:          lista di {"name": "col", "type": "str", "visible": True}
        result_filename:  nome file output (senza estensione)
        result_format:    "csv", "json", "xlsx", "geojson", ...
        filters:          lista filtri WHERE opzionali
        limit:            LIMIT opzionale sul result node

    Returns:
        Dict canvas compatibile con CanvasState del backend.
    """
    source_id = "node-source-1"
    result_id = "node-result-1"

    return {
        "nodes": [
            {
                "id": source_id,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "table_name": table_name,
                "columns": columns,
                "filters": filters or [],
            },
            {
                "id": result_id,
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": result_filename,
                "format": result_format,
                "limit": limit,
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": source_id,
                "target": result_id,
            }
        ],
    }
"""
tests/manipola/conftest.py
==========================
Fixture condivise per la suite test MANIPOLA.

Sorgenti dati disponibili:
  - csv_immobili      → 6819 righe, dataset sintetico immobili (M-01, M-03, M-07, M-08, M-09)
  - csv_pivot         → 10000 righe per test pivot (M-02)
  - csv_filter        → 166075 righe con colonne filtrabili (M-05, M-06)
  - csv_join_tables   → dizionario 5 CSV collegabili (M-04)
  - gpkg_polygons     → layer poligoni EPSG:4326 (M-10, M-15, M-19, M-20, M-21)
  - gpkg_points       → layer punti EPSG:4326 (M-11, M-16, M-17)
  - gpkg_multipolygon → layer MultiPolygon (M-18)
  - gpkg_utm32632     → layer EPSG:32632 (M-14, M-22)

Canvas builder:
  - build_manipola_canvas() → costruisce canvas MANIPOLA con file_source + transform + result

Helper:
  - upload_csv()   → carica CSV e restituisce file_id
  - upload_gpkg()  → carica GPKG e restituisce file_id
  - execute_manipola() → esegue progetto e ritorna result
"""

import csv
import io
import os
import random
import string
import tempfile
import zipfile

# Directory temp locale — evita lock su AppData\Local\Temp (Windows + fiona/GDAL)
_TEST_TEMP_DIR = __import__("pathlib").Path(__file__).parent / "_gpkg_temp"
_TEST_TEMP_DIR.mkdir(exist_ok=True)

@pytest.fixture(scope="session", autouse=True)
def _cleanup_gpkg_temp():
    """Pulisce _gpkg_temp a fine sessione test."""
    yield
    import gc, time, shutil
    gc.collect()
    time.sleep(0.5)
    try:
        shutil.rmtree(_TEST_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


def _safe_unlink(path: str, retries: int = 5, delay: float = 0.3) -> None:
    """Elimina file con retry — su Windows fiona/GDAL può tenere il file locked."""
    import gc, time
    gc.collect()
    for i in range(retries):
        try:
            os.unlink(path)
            return
        except PermissionError:
            if i < retries - 1:
                time.sleep(delay)
    try:
        os.unlink(path)
    except Exception:
        pass

from typing import Any, Callable, Dict, List, Optional

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# GENERATORI DATI SINTETICI
# ─────────────────────────────────────────────────────────────────────────────

def _gen_immobili(n: int = 6819, seed: int = 42) -> bytes:
    """
    Genera dataset sintetico immobili con duplicati intenzionali.
    Duplicati: ~391 righe ripetute → 6428 righe uniche.
    Colonne: id, comune, zona, tipo, valore_omi, superficie, piano, anno, lat, lon
    """
    random.seed(seed)
    comuni = ["Roma", "Milano", "Napoli", "Torino", "Bologna", "Firenze", "Palermo", "Genova"]
    zone = ["A", "B", "C", "D", "R1", "R2", "R3"]
    tipi = ["Residenziale", "Commerciale", "Ufficio", "Industriale", "Agricolo"]

    rows = []
    for i in range(6428):  # righe uniche
        rows.append({
            "id": i + 1,
            "comune": random.choice(comuni),
            "zona": random.choice(zone),
            "tipo": random.choice(tipi),
            "valore_omi": round(random.uniform(500, 5000), 2),
            "superficie": random.randint(30, 500),
            "piano": random.randint(0, 10),
            "anno": random.randint(2010, 2023),
            "lat": round(41.0 + random.random() * 6.0, 6),
            "lon": round(12.0 + random.random() * 5.0, 6),
        })

    # Aggiungi ~391 duplicati
    duplicates = random.choices(rows[:200], k=n - 6428)
    all_rows = rows + duplicates
    random.shuffle(all_rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(all_rows)
    return buf.getvalue().encode("utf-8")


def _gen_pivot_data(n: int = 10000, seed: int = 42) -> bytes:
    """
    Genera dataset per test pivot.
    Colonne: anno, trimestre, comune, categoria, valore
    Struttura: ~5 anni × 4 trimestri × 10 comuni × 5 categorie = ~1000 combinazioni
    """
    random.seed(seed)
    anni = [2019, 2020, 2021, 2022, 2023]
    trimestri = ["Q1", "Q2", "Q3", "Q4"]
    comuni = ["Roma", "Milano", "Napoli", "Torino", "Bologna",
              "Firenze", "Palermo", "Genova", "Venezia", "Bari"]
    categorie = ["A", "B", "C", "D", "E"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["anno", "trimestre", "comune", "categoria", "valore"])
    for _ in range(n):
        writer.writerow([
            random.choice(anni),
            random.choice(trimestri),
            random.choice(comuni),
            random.choice(categorie),
            round(random.uniform(100, 9999), 2),
        ])
    return buf.getvalue().encode("utf-8")


def _gen_filter_data(n: int = 166075, seed: int = 42) -> bytes:
    """
    Genera dataset per test filtri.
    Colonne: id, regione, provincia, tipo_uso, valore, anno, attivo
    ~52803 righe con tipo_uso='Residenziale' AND anno>=2020 AND attivo=True
    """
    random.seed(seed)
    regioni = ["Lombardia", "Lazio", "Campania", "Sicilia", "Veneto",
               "Emilia-Romagna", "Piemonte", "Puglia", "Toscana", "Calabria"]
    province = [f"PR{i:02d}" for i in range(20)]
    tipi_uso = ["Residenziale", "Commerciale", "Industriale", "Agricolo", "Altro"]
    anni = list(range(2015, 2024))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "regione", "provincia", "tipo_uso", "valore", "anno", "attivo"])

    # Genera le ~52803 righe che passeranno il filtro
    target_pass = 52803
    written_pass = 0
    rows = []

    for i in range(n):
        if written_pass < target_pass and i % 3 == 0:
            row = [i + 1, random.choice(regioni), random.choice(province),
                   "Residenziale", round(random.uniform(500, 5000), 2),
                   random.randint(2020, 2023), True]
            written_pass += 1
        else:
            row = [i + 1, random.choice(regioni), random.choice(province),
                   random.choice(["Commerciale", "Industriale", "Agricolo", "Altro"]),
                   round(random.uniform(100, 3000), 2),
                   random.choice(anni), random.choice([True, False])]
        rows.append(row)

    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _gen_join_tables(seed: int = 42) -> Dict[str, bytes]:
    """
    Genera 5 CSV per test join sequenziale.
    comuni → zone → immobili → valutazioni → storico
    """
    random.seed(seed)

    comuni = [(i + 1, f"Comune_{i+1:03d}", random.choice(["Nord", "Centro", "Sud"]))
              for i in range(50)]
    zone = [(i + 1, random.randint(1, 50), f"Zona_{i+1:03d}", random.choice(["A", "B", "C"]))
            for i in range(200)]
    immobili = [(i + 1, random.randint(1, 200), f"Via {i+1}", random.randint(30, 400))
                for i in range(500)]
    valutazioni = [(i + 1, random.randint(1, 500), round(random.uniform(500, 5000), 2),
                    random.randint(2018, 2023))
                   for i in range(500)]
    storico = [(i + 1, random.randint(1, 500), random.randint(2010, 2022),
                round(random.uniform(400, 4500), 2))
               for i in range(800)]

    def to_csv(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    return {
        "comuni":      to_csv(["comune_id", "nome_comune", "area"],      comuni),
        "zone":        to_csv(["zona_id", "comune_id", "nome_zona", "classe"], zone),
        "immobili":    to_csv(["immobile_id", "zona_id", "indirizzo", "superficie"], immobili),
        "valutazioni": to_csv(["val_id", "immobile_id", "valore_omi", "anno_val"], valutazioni),
        "storico":     to_csv(["storico_id", "val_id", "anno_storico", "valore_storico"], storico),
    }


def _gen_gpkg_polygons(seed: int = 42) -> bytes:
    """Layer poligoni EPSG:4326 — 20 comuni sintetici con geometria."""
    import geopandas as gpd
    from shapely.geometry import box
    random.seed(seed)

    rows = []
    for i in range(20):
        lon = 6.5 + random.random() * 11.0
        lat = 36.5 + random.random() * 10.0
        size = 0.05 + random.random() * 0.2
        rows.append({
            "id": i + 1,
            "nome": f"Comune_{i+1:02d}",
            "provincia": f"PR{i % 10:02d}",
            "area_kmq": round(size * 10000, 2),
            "geometry": box(lon, lat, lon + size, lat + size),
        })
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    _gpkg_path = _TEST_TEMP_DIR / "comuni.gpkg"
    gdf.to_file(str(_gpkg_path), driver="GPKG", layer="comuni")
    del gdf
    import gc; gc.collect()
    data = _gpkg_path.read_bytes()
    return data


def _gen_gpkg_points(seed: int = 42) -> bytes:
    """Layer punti EPSG:4326 — 50 POI sintetici."""
    import geopandas as gpd
    from shapely.geometry import Point
    random.seed(seed)

    rows = []
    for i in range(50):
        rows.append({
            "id": i + 1,
            "nome": f"POI_{i+1:02d}",
            "categoria": random.choice(["parco", "museo", "chiesa", "piazza", "fontana"]),
            "geometry": Point(6.5 + random.random() * 11.0, 36.5 + random.random() * 10.0),
        })
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    _gpkg_path = _TEST_TEMP_DIR / "poi.gpkg"
    gdf.to_file(str(_gpkg_path), driver="GPKG", layer="poi")
    del gdf
    import gc; gc.collect()
    data = _gpkg_path.read_bytes()
    return data


def _gen_gpkg_multipolygon(seed: int = 42) -> bytes:
    """Layer MultiPolygon EPSG:4326 — 10 province con più comuni."""
    import geopandas as gpd
    from shapely.geometry import MultiPolygon, box
    random.seed(seed)

    rows = []
    for i in range(10):
        lon_base = 7.0 + i * 1.2
        lat_base = 37.0 + (i % 5) * 1.5
        parts = [
            box(lon_base, lat_base, lon_base + 0.3, lat_base + 0.3),
            box(lon_base + 0.5, lat_base + 0.1, lon_base + 0.8, lat_base + 0.4),
        ]
        rows.append({
            "id": i + 1,
            "nome": f"Provincia_{i+1:02d}",
            "n_comuni": random.randint(5, 30),
            "geometry": MultiPolygon(parts),
        })
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    _gpkg_path = _TEST_TEMP_DIR / "province.gpkg"
    gdf.to_file(str(_gpkg_path), driver="GPKG", layer="province")
    del gdf
    import gc; gc.collect()
    data = _gpkg_path.read_bytes()
    return data


def _gen_gpkg_utm32632(seed: int = 42) -> bytes:
    """Layer punti EPSG:32632 — 30 punti in UTM zona 32N."""
    import geopandas as gpd
    from shapely.geometry import Point
    random.seed(seed)

    rows = []
    for i in range(30):
        x = 350000 + random.random() * 400000
        y = 4900000 + random.random() * 200000
        rows.append({
            "id": i + 1,
            "codice": f"PT{i+1:03d}",
            "valore": round(random.uniform(100, 1000), 2),
            "geometry": Point(x, y),
        })
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:32632")
    _gpkg_path = _TEST_TEMP_DIR / "punti_utm.gpkg"
    gdf.to_file(str(_gpkg_path), driver="GPKG", layer="punti_utm")
    del gdf
    import gc; gc.collect()
    data = _gpkg_path.read_bytes()
    return data


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE FILE (scope=module — generati una sola volta per modulo test)
# ─────────────────────────────────────────────────────────────────────────────


def _gen_gpkg_no_crs(seed: int = 42) -> bytes:
    """Layer poligoni SENZA CRS — per testare il branch set_crs(default)."""
    import geopandas as gpd
    from shapely.geometry import box
    random.seed(seed)
    rows = []
    for i in range(10):
        lon = 9.0 + random.random() * 3.0
        lat = 44.0 + random.random() * 3.0
        size = 0.05 + random.random() * 0.1
        rows.append({
            "id": i + 1,
            "nome": f"NoCRS_{i+1:02d}",
            "geometry": box(lon, lat, lon + size, lat + size),
        })
    # Crea GeoDataFrame senza CRS
    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    # Rimuovi CRS esplicitamente
    gdf = gdf.set_crs(None, allow_override=True)
    _gpkg_path = _TEST_TEMP_DIR / "nocrs.gpkg"
    # Salva con fiona passando crs=None
    import fiona
    schema = {"geometry": "Polygon", "properties": {"id": "int", "nome": "str"}}
    with fiona.open(str(_gpkg_path), "w", driver="GPKG", schema=schema, layer="nocrs") as dst:
        for _, row in gdf.iterrows():
            dst.write({
                "geometry": row["geometry"].__geo_interface__,
                "properties": {"id": row["id"], "nome": row["nome"]},
            })
    data = _gpkg_path.read_bytes()
    return data


def _gen_csv_with_wkt(seed: int = 42) -> bytes:
    """CSV con colonna 'geom_wkt' contenente WKT Point — plain DataFrame per to_geodataframe branch.

    Usiamo Point (WKT senza virgole interne) per evitare che il parser CSV
    spezzi il valore POLYGON ((lon lat, lon lat, ...)) su piu colonne.
    """
    from shapely.geometry import Point as _Point
    random.seed(seed)
    lines = ["id,nome,valore,geom_wkt"]
    for i in range(15):
        lon = 9.0 + random.random() * 3.0
        lat = 44.0 + random.random() * 3.0
        geom = _Point(lon, lat)
        lines.append(f"{i+1},Feature_{i+1},{round(random.uniform(10,100),2)},{geom.wkt}")
    return "\n".join(lines).encode("utf-8")


@pytest.fixture(scope="module")
def gpkg_no_crs(client):
    """GeoPackage layer poligoni SENZA CRS — branch set_crs(default)."""
    data = _gen_gpkg_no_crs()
    res = client.post(
        "/files/upload",
        files={"file": ("nocrs.gpkg", data, "application/geopackage+sqlite3")},
        data={"name": "[AUTO] M nocrs", "layer": "nocrs"},
    )
    assert res.status_code == 200, f"Upload gpkg_no_crs fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def csv_with_wkt(client):
    """CSV con colonna WKT 'geom_wkt' — branch not-GeoDataFrame + geom_column presente."""
    data = _gen_csv_with_wkt()
    res = client.post(
        "/files/upload",
        files={"file": ("features_wkt.csv", data, "text/csv")},
        data={"name": "[AUTO] M csv wkt"},
    )
    assert res.status_code == 200, f"Upload csv_with_wkt fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def csv_immobili(client):
    """CSV immobili 6819 righe con ~391 duplicati → 6428 uniche."""
    data = _gen_immobili()
    res = client.post(
        "/files/upload",
        files={"file": ("immobili.csv", data, "text/csv")},
        data={"name": "[AUTO] M immobili"},
    )
    assert res.status_code == 200, f"Upload csv_immobili fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def csv_pivot(client):
    """CSV 10000 righe per test pivot."""
    data = _gen_pivot_data()
    res = client.post(
        "/files/upload",
        files={"file": ("pivot_data.csv", data, "text/csv")},
        data={"name": "[AUTO] M pivot"},
    )
    assert res.status_code == 200
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def csv_filter(client):
    """CSV 166075 righe per test filtri."""
    print("\n[MANIPOLA] Generazione csv_filter (166k righe)...")
    data = _gen_filter_data()
    res = client.post(
        "/files/upload",
        files={"file": ("filter_data.csv", data, "text/csv")},
        data={"name": "[AUTO] M filter"},
    )
    assert res.status_code == 200
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def csv_join_tables(client):
    """5 CSV per test join sequenziale → dict {nome: file_id}."""
    tables = _gen_join_tables()
    ids = {}
    for name, data in tables.items():
        res = client.post(
            "/files/upload",
            files={"file": (f"{name}.csv", data, "text/csv")},
            data={"name": f"[AUTO] M join {name}"},
        )
        assert res.status_code == 200, f"Upload {name} fallito: {res.text}"
        ids[name] = res.json()["id"]
    yield ids
    for fid in ids.values():
        client.delete(f"/files/{fid}")




def _gen_join_common_key(n_entities: int = 100, seed: int = 42) -> Dict[str, bytes]:
    """
    Genera 5 CSV con chiave comune 'entity_id' per test multi_join.
    Tabelle: base + 4 tabelle attributi.
    Left join: tutti gli entity_id di base devono essere presenti nel risultato.
    """
    random.seed(seed)
    entity_ids = list(range(1, n_entities + 1))

    def to_csv(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    # Base: 100 entità
    base = [(eid, f"Entita_{eid:03d}", random.choice(["A", "B", "C"])) for eid in entity_ids]
    # Dettagli 1: 80 entità (20 mancanti → NULL in left join)
    det1 = [(eid, round(random.uniform(100, 1000), 2)) for eid in entity_ids[:80]]
    # Dettagli 2: 90 entità
    det2 = [(eid, random.choice(["Nord", "Centro", "Sud"])) for eid in entity_ids[:90]]
    # Dettagli 3: tutti 100
    det3 = [(eid, random.randint(2010, 2023)) for eid in entity_ids]
    # Dettagli 4: tutti 100
    det4 = [(eid, round(random.uniform(0, 100), 1)) for eid in entity_ids]

    return {
        "base":   to_csv(["entity_id", "nome", "categoria"], base),
        "det1":   to_csv(["entity_id", "valore"], det1),
        "det2":   to_csv(["entity_id", "area"], det2),
        "det3":   to_csv(["entity_id", "anno"], det3),
        "det4":   to_csv(["entity_id", "score"], det4),
    }


@pytest.fixture(scope="module")
def csv_join_common_key(client):
    """5 CSV con entity_id comune per test multi_join M-04."""
    tables = _gen_join_common_key()
    ids = {}
    for name, data in tables.items():
        res = client.post(
            "/files/upload",
            files={"file": (f"join_{name}.csv", data, "text/csv")},
            data={"name": f"[AUTO] M join_ck {name}"},
        )
        assert res.status_code == 200, f"Upload {name} fallito: {res.text}"
        ids[name] = res.json()["id"]
    yield ids
    for fid in ids.values():
        client.delete(f"/files/{fid}")

@pytest.fixture(scope="module")
def gpkg_polygons(client):
    """GeoPackage layer poligoni EPSG:4326."""
    data = _gen_gpkg_polygons()
    res = client.post(
        "/files/upload",
        files={"file": ("comuni.gpkg", data, "application/geopackage+sqlite3")},
        data={"name": "[AUTO] M poligoni", "layer": "comuni"},
    )
    assert res.status_code == 200, f"Upload gpkg_polygons fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def gpkg_points(client):
    """GeoPackage layer punti EPSG:4326."""
    data = _gen_gpkg_points()
    res = client.post(
        "/files/upload",
        files={"file": ("poi.gpkg", data, "application/geopackage+sqlite3")},
        data={"name": "[AUTO] M punti", "layer": "poi"},
    )
    assert res.status_code == 200, f"Upload gpkg_points fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def gpkg_multipolygon(client):
    """GeoPackage layer MultiPolygon EPSG:4326."""
    data = _gen_gpkg_multipolygon()
    res = client.post(
        "/files/upload",
        files={"file": ("province.gpkg", data, "application/geopackage+sqlite3")},
        data={"name": "[AUTO] M multipolygon", "layer": "province"},
    )
    assert res.status_code == 200, f"Upload gpkg_multipolygon fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


@pytest.fixture(scope="module")
def gpkg_utm32632(client):
    """GeoPackage layer punti EPSG:32632."""
    data = _gen_gpkg_utm32632()
    res = client.post(
        "/files/upload",
        files={"file": ("punti_utm.gpkg", data, "application/geopackage+sqlite3")},
        data={"name": "[AUTO] M utm32632", "layer": "punti_utm"},
    )
    assert res.status_code == 200, f"Upload gpkg_utm32632 fallito: {res.text}"
    file_id = res.json()["id"]
    yield file_id
    client.delete(f"/files/{file_id}")


# ─────────────────────────────────────────────────────────────────────────────
# CANVAS BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_manipola_canvas(
    file_source_id: str,
    transform_op: str,
    transform_config: Dict[str, Any],
    output_format: str = "csv",
    output_filename: str = "output",
    file_columns: Optional[List[Dict]] = None,
    second_file_source_id: Optional[str] = None,
    second_file_columns: Optional[List[Dict]] = None,
) -> Dict:
    """
    Costruisce canvas MANIPOLA minimale:
      [file_source] → [transform] → [result]

    Opzionale: secondo file_source per operazioni binary (join spaziale, ecc.)

    Args:
      file_source_id:    ID file sorgente principale
      transform_op:      es. "distinct", "pivot", "filter", ecc.
      transform_config:  dict params per il transform (va in node.config)
      output_format:     "csv", "xlsx", "gpkg", "shp"
      output_filename:   nome file output
      file_columns:      lista colonne [{name, type, visible}] (opzionale)
      second_file_source_id: secondo file sorgente (per operazioni binary)
      second_file_columns:   colonne del secondo file
    """
    nodes = [
        {
            "id": "node-src-1",
            "type": "file_source",
            "position": {"x": 100, "y": 200},
            "file_source_id": file_source_id,
            "columns": file_columns or [],
        },
    ]
    edges = []

    if second_file_source_id:
        nodes.append({
            "id": "node-src-2",
            "type": "file_source",
            "position": {"x": 100, "y": 400},
            "file_source_id": second_file_source_id,
            "columns": second_file_columns or [],
        })

    nodes.append({
        "id": "node-transform",
        "type": "transform",
        "position": {"x": 400, "y": 300},
        "transform_op": transform_op,
        "config": transform_config,
    })

    nodes.append({
        "id": "node-result",
        "type": "result",
        "position": {"x": 700, "y": 300},
        "fileName": output_filename,
        "format": output_format,
    })

    edges.append({
        "id": "edge-src1-transform",
        "source": "node-src-1",
        "target": "node-transform",
    })

    if second_file_source_id:
        edges.append({
            "id": "edge-src2-transform",
            "source": "node-src-2",
            "target": "node-transform",
            "targetHandle": "input-1",
        })

    edges.append({
        "id": "edge-transform-result",
        "source": "node-transform",
        "target": "node-result",
    })

    return {"nodes": nodes, "edges": edges}



def build_estrai_file_canvas(
    file_source_id: str,
    output_format: str = "csv",
    output_filename: str = "output",
) -> Dict[str, Any]:
    """
    Canvas ESTRAI minimalista con file_source → result.
    Usato da M-12 per creare un progetto ESTRAI a partire da un file CSV.
    """
    return {
        "nodes": [
            {
                "id": "node-file-src",
                "type": "file_source",
                "position": {"x": 100, "y": 200},
                "file_source_id": file_source_id,
                "columns": [],
            },
            {
                "id": "node-result",
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": output_filename,
                "format": output_format,
            },
        ],
        "edges": [
            {"id": "edge-src-result", "source": "node-file-src", "target": "node-result"},
        ],
    }


def build_manipola_canvas_project_source(
    source_project_id: str,
    transform_op: str,
    transform_config: Dict[str, Any],
    source_columns: List[Dict] = None,
    output_format: str = "csv",
    output_filename: str = "output",
) -> Dict:
    """
    Canvas MANIPOLA con project_source (output di un progetto ESTRAI) come sorgente.
    Usato per M-12 (ProjectSourceNode).

    source_project_id: ID del progetto ESTRAI già eseguito
    source_columns:    lista di {"name": "...", "type": "..."} — colonne visibili nel nodo
    """
    nodes = [
        {
            "id": "node-src",
            "type": "project_source",
            "position": {"x": 100, "y": 300},
            "source_project_id": source_project_id,
            "columns": source_columns or [],
        },
        {
            "id": "node-transform",
            "type": "transform",
            "position": {"x": 400, "y": 300},
            "transform_op": transform_op,
            "config": transform_config,
        },
        {
            "id": "node-result",
            "type": "result",
            "position": {"x": 700, "y": 300},
            "fileName": output_filename,
            "format": output_format,
        },
    ]
    edges = [
        {"id": "edge-src-transform", "source": "node-src", "target": "node-transform"},
        {"id": "edge-transform-result", "source": "node-transform", "target": "node-result"},
    ]
    return {"nodes": nodes, "edges": edges}


def build_manipola_canvas_multi_source(
    file_source_ids: List[str],
    transform_op: str,
    transform_config: Dict[str, Any],
    output_format: str = "csv",
    output_filename: str = "output",
) -> Dict:
    """
    Canvas MANIPOLA con N sorgenti → multi_join → transform → result.
    Usato per M-04 (join sequenziale 5 DataFrame).
    """
    nodes = []
    edges = []

    for i, fid in enumerate(file_source_ids):
        src_id = f"node-src-{i}"
        nodes.append({
            "id": src_id,
            "type": "file_source",
            "position": {"x": 100, "y": 100 + i * 150},
            "file_source_id": fid,
            "columns": [],
        })

    # multi_join — join_keys e multi_join_type al top-level per _nf()
    nodes.append({
        "id": "node-multijoin",
        "type": "multi_join",
        "position": {"x": 400, "y": 300},
        "join_keys": transform_config.get("join_keys", []),
        "multi_join_type": transform_config.get("join_type", "inner"),
        "config": transform_config,
    })

    for i in range(len(file_source_ids)):
        edges.append({
            "id": f"edge-src{i}-mj",
            "source": f"node-src-{i}",
            "target": "node-multijoin",
            "targetHandle": f"input-{i}",
        })

    nodes.append({
        "id": "node-result",
        "type": "result",
        "position": {"x": 700, "y": 300},
        "fileName": output_filename,
        "format": output_format,
    })

    edges.append({
        "id": "edge-mj-result",
        "source": "node-multijoin",
        "target": "node-result",
    })

    return {"nodes": nodes, "edges": edges}


def build_manipola_canvas_sjoin(
    left_file_id: str,
    right_file_id: str,
    predicate: str = "intersects",
    how: str = "inner",
    output_format: str = "csv",
    output_filename: str = "sjoin_output",
    left_geom: str = "geometry",
    right_geom: str = "geometry",
) -> Dict:
    """Canvas MANIPOLA: file_source × file_source → sjoin → result."""
    return {
        "nodes": [
            {
                "id": "node-src-left",
                "type": "file_source",
                "position": {"x": 100, "y": 200},
                "file_source_id": left_file_id,
                "columns": [],
            },
            {
                "id": "node-src-right",
                "type": "file_source",
                "position": {"x": 100, "y": 400},
                "file_source_id": right_file_id,
                "columns": [],
            },
            {
                "id": "node-transform",
                "type": "transform",
                "position": {"x": 400, "y": 300},
                "transform_op": "sjoin",
                "config": {
                    "left_geom": left_geom,
                    "right_geom": right_geom,
                    "predicate": predicate,
                    "how": how,
                },
            },
            {
                "id": "node-result",
                "type": "result",
                "position": {"x": 700, "y": 300},
                "fileName": output_filename,
                "format": output_format,
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-src-left",  "target": "node-transform"},
            {"id": "e2", "source": "node-src-right", "target": "node-transform", "targetHandle": "input-1"},
            {"id": "e3", "source": "node-transform", "target": "node-result"},
        ],
    }


def build_manipola_canvas_geo(
    file_source_id: str,
    geo_op: str,
    geo_config: Dict[str, Any],
    output_format: str = "gpkg",
    output_filename: str = "geo_output",
    second_file_source_id: Optional[str] = None,
) -> Dict:
    """
    Canvas MANIPOLA per operazioni geospaziali single o multi-input:
      [file_source(s)] → [transform geo_op] → [result]

    Args:
      file_source_id:        ID file sorgente principale
      geo_op:                op name es. "geo_buffer", "geo_dissolve", "geo_clip"
      geo_config:            dict params per il transform
      output_format:         "gpkg", "shp", "csv"
      output_filename:       nome file output
      second_file_source_id: secondo file sorgente (clip, union, difference)
    """
    nodes = [
        {
            "id": "node-src-1",
            "type": "file_source",
            "position": {"x": 100, "y": 200},
            "file_source_id": file_source_id,
            "columns": [],
        },
    ]
    edges = []

    if second_file_source_id:
        nodes.append({
            "id": "node-src-2",
            "type": "file_source",
            "position": {"x": 100, "y": 400},
            "file_source_id": second_file_source_id,
            "columns": [],
        })

    nodes.append({
        "id": "node-transform",
        "type": "transform",
        "position": {"x": 400, "y": 300},
        "transform_op": geo_op,
        "config": geo_config,
    })
    nodes.append({
        "id": "node-result",
        "type": "result",
        "position": {"x": 700, "y": 300},
        "fileName": output_filename,
        "format": output_format,
    })

    edges.append({"id": "e1", "source": "node-src-1", "target": "node-transform"})
    if second_file_source_id:
        edges.append({"id": "e2", "source": "node-src-2", "target": "node-transform", "targetHandle": "input-1"})
    edges.append({"id": "e3", "source": "node-transform", "target": "node-result"})

    return {"nodes": nodes, "edges": edges}



# =============================================================================
# ARRICCHISCI — CONFIGURAZIONE MOCK SERVER
# =============================================================================

MOCK_URL = "http://localhost:8999"

MOCK_BEARER_TOKEN         = "mock-access-token-1"
MOCK_API_KEY              = "test-api-key-valid"
MOCK_BASIC_USER           = "testuser"
MOCK_BASIC_PASSWORD       = "testpass"
MOCK_OAUTH2_CLIENT_ID     = "test-client"
MOCK_OAUTH2_CLIENT_SECRET = "test-secret"
MOCK_OAUTH2_TOKEN_URL     = f"{MOCK_URL}/auth/token"


# =============================================================================
# RESET MOCK SERVER — autouse prima di ogni test
# =============================================================================

@pytest.fixture(autouse=True)
def mock_reset():
    """Azzera contatori e stato del mock server prima di ogni test."""
    try:
        requests.post(f"{MOCK_URL}/mock/reset", timeout=3)
    except requests.exceptions.ConnectionError:
        pytest.skip(
            f"Mock server non raggiungibile su {MOCK_URL} — "
            "avvia: uvicorn mock_api_server:app --port 8999"
        )


# =============================================================================
# FACTORY: CONNESSIONI API
# =============================================================================

@pytest.fixture
def make_api_connection(client) -> Callable:
    """
    Factory per registrare connessioni API nel backend via POST /api/api-connections.
    Restituisce il connection_id (api_id). Cleanup automatico al teardown.

    Uso:
        api_id = make_api_connection(
            url="http://localhost:8999/mock/simple",
            auth_type="none",
            output_mapping=[{"path": "capital", "column": "capitale"}],
            options={"rate_limit": 0, "timeout": 10, "max_retries": 1},
        )

    Il valore restituito è l'api_id da usare nel canvas node come "api_id".
    auth_type: "none" | "bearer" | "api_key" | "basic" | "oauth2_client_credentials"
    """
    created_ids = []

    def _create(
        url: str,
        auth_type: str = "none",
        output_mapping: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        name: Optional[str] = None,
        method: str = "GET",
        api_type: str = "enricher",
        # Credenziali opzionali
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
            print(f"[WARN] Errore teardown API connection {aid}: {e}")


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
    """CSV encoding Latin-1 con caratteri accentati nella colonna note (A-61).
    Usa nomi paese inglesi (compatibili col mock /mock/simple) e accenti
    nella colonna note per verificare encoding auto-detect.
    """
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
    """10 righe, 3 unici: Italy×4, France×3, Germany×3 — per A-49 cache dedup."""
    rows = [{"paese": p, "codice": c} for p, c in [
        ("Italy",   "IT"), ("France",  "FR"), ("Germany", "DE"),
        ("Italy",   "IT"), ("France",  "FR"), ("Germany", "DE"),
        ("Italy",   "IT"), ("France",  "FR"), ("Italy",   "IT"),
        ("Germany", "DE"),
    ]]
    return _to_csv_bytes(rows)


def _gen_csv_100_righe() -> bytes:
    """100 righe, 3 unici — per A-67 parallel workers."""
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
# CANVAS BUILDER — ARRICCHISCI
#
# NOTA: il nodo api_enrich usa "api_id" (non "connection_id") per riferirsi
# alla connessione API. Il param_mapping è un dict {param_api: colonna_df}.
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
    """
    Canvas ARRICCHISCI standard:
      [file_source] → [api_enrich] → [result]

    Args:
        file_source_id:  ID file sorgente CSV/GPKG
        api_id:          ID connessione API (restituito da make_api_connection)
        param_mapping:   dict {param_api: colonna_df}, es. {"country": "paese"}
        output_format:   "csv", "gpkg", "geojson"
        output_filename: nome file output
        continue_on_error: True = pipeline continua anche su errori di riga
        max_errors:      soglia errori massimi (None = illimitato)
        parallel_workers: numero worker paralleli (1-16)
    """
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
            {
                "id":             "node-src",
                "type":           "file_source",
                "position":       {"x": 100, "y": 200},
                "file_source_id": file_source_id,
                "columns":        [],
            },
            enrich_node,
            {
                "id":       "node-result",
                "type":     "result",
                "position": {"x": 700, "y": 200},
                "fileName": output_filename,
                "format":   output_format,
            },
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
    """
    Canvas ARRICCHISCI a catena:
      [file_source] → [api_enrich_1] → [api_enrich_2] → [result]

    Usato per A-50 (REST Countries → Open-Meteo) e A-68.
    Il secondo enricher usa colonne prodotte dal primo come parametri.
    """
    return {
        "nodes": [
            {
                "id":             "node-src",
                "type":           "file_source",
                "position":       {"x": 100, "y": 200},
                "file_source_id": file_source_id,
                "columns":        [],
            },
            {
                "id":               "node-enrich-1",
                "type":             "api_enrich",
                "position":         {"x": 350, "y": 200},
                "api_id":           api_id_1,
                "param_mapping":    param_mapping_1 or {},
                "continue_on_error": False,
                "parallel_workers": 1,
            },
            {
                "id":               "node-enrich-2",
                "type":             "api_enrich",
                "position":         {"x": 600, "y": 200},
                "api_id":           api_id_2,
                "param_mapping":    param_mapping_2 or {},
                "continue_on_error": False,
                "parallel_workers": 1,
            },
            {
                "id":       "node-result",
                "type":     "result",
                "position": {"x": 850, "y": 200},
                "fileName": output_filename,
                "format":   output_format,
            },
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
    """
    Canvas ARRICCHISCI senza FileSource — Generator puro (A-51).
    api_type="generator" sul nodo: il backend lo riconosce e non cerca input.
    """
    return {
        "nodes": [
            {
                "id":               "node-enrich",
                "type":             "api_enrich",
                "position":         {"x": 300, "y": 200},
                "api_id":           api_id,
                "api_type":         "generator",
                "param_mapping":    {},
                "continue_on_error": False,
                "parallel_workers": 1,
            },
            {
                "id":       "node-result",
                "type":     "result",
                "position": {"x": 600, "y": 200},
                "fileName": output_filename,
                "format":   output_format,
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-enrich", "target": "node-result"},
        ],
    }


# =============================================================================
# HELPER: ESECUZIONE PROGETTO ARRICCHISCI
# =============================================================================

def execute_arricchisci(
    client,
    project_id: str,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Esegue un progetto ARRICCHISCI tramite POST /api/projects/{id}/execute
    e fa polling fino a completamento.

    Il risultato del pipeline_task per progetti arricchisci contiene:
        success, project_id, rows_count, columns, message, output_file

    Raises:
        AssertionError se il task termina in FAILURE.
        TimeoutError se non completa entro timeout.
    """
    res = client.post(f"/projects/{project_id}/enrich-async")
    assert res.status_code == 200, f"Errore avvio esecuzione: {res.text}"
    task_id = res.json().get("task_id")
    assert task_id, f"task_id non trovato nella risposta: {res.json()}"
    result = poll_task(client, task_id, timeout=timeout)
    # Normalizza rows_processed → rows_count per uniformità con gli altri moduli
    if "rows_processed" in result and "rows_count" not in result:
        result["rows_count"] = result["rows_processed"]
    return result

# =============================================================================
# COSTANTI MOCK CARICA
# =============================================================================

MOCK_ARCGIS_URL      = "http://localhost:8999/arcgis"
MOCK_ARCGIS_LAYER_0  = "http://localhost:8999/arcgis/rest/services/mock/FeatureServer/0"
MOCK_ARCGIS_TOKEN    = "mock-arcgis-token-123"


# =============================================================================
# FIXTURE: CONNESSIONE DB CARICA
# =============================================================================

@pytest.fixture(scope="session")
def pg_connection_id(client) -> str:
    """Alias di pg_conn con scope=session per i test CARICA."""
    conn_id = _register_connection(client, "postgres")
    yield conn_id
    _delete_connection(client, conn_id)


@pytest.fixture(scope="session", autouse=False)
def cleanup_carica_tables():
    """
    Elimina le tabelle di test CARICA prima della sessione via psycopg2 diretto.
    Usa TEST_DB_CONFIG — nessuna dipendenza dal backend.
    """
    try:
        import psycopg2
        cfg = TEST_DB_CONFIG["postgres"]
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"],
            dbname=cfg["database"], user=cfg["username"],
            password=cfg["password"], gssencmode="disable",
        )
        conn.autocommit = True
        cur = conn.cursor()
        tables = [
            "test_carica_c01", "test_carica_c01b", "test_carica_c01c",
            "test_carica_c02", "test_carica_c02b",
            "test_carica_c03", "test_carica_c03b",
            "test_carica_c04", "test_carica_c04b",
            "test_carica_c05", "test_carica_c06", "test_carica_c06b",
            "test_carica_c07", "test_carica_c07b",
            "test_carica_c09", "test_carica_c09b",
            "test_carica_c10", "test_carica_c17", "test_carica_c18",
            "test_carica_c19", "test_carica_c20", "test_carica_c21",
            "test_carica_c22", "test_carica_c23", "test_carica_c23b",
            "test_carica_c28", "test_carica_c30", "test_carica_c30b",
        ]
        for table in tables:
            cur.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
        cur.close()
        conn.close()
    except Exception:
        pass  # non bloccante
    yield


@pytest.fixture(scope="session")
def arcgis_connection_id(client) -> str:
    """
    Crea una connessione ArcGIS che punta al mock server locale.
    Usa /features/connections (endpoint dedicato ArcGIS).
    """
    payload = {
        "name":        "[AUTO] C - ArcGIS mock",
        "server_url":  MOCK_ARCGIS_URL,
        "server_type": "arcgis_enterprise",
        "auth_type":   "token",
        "username":    "mock_user",
        "password":    "mock_pass",
    }
    res = client.post("/features/connections", json=payload)
    assert res.status_code in (200, 201), f"Connessione ArcGIS fallita: {res.text}"
    body = res.json()
    conn_id = body.get("id") or body.get("connection_id") or body.get("connection", {}).get("id")
    assert conn_id, f"connection_id non trovato: {body}"
    yield conn_id
    client.delete(f"/features/connections/{conn_id}")


# =============================================================================
# FIXTURE: CSV E DATAFRAME PER CARICA
# =============================================================================

@pytest.fixture(scope="session")
def csv_carica_small(client) -> str:
    """CSV con 100 righe e geometria WKT — input per test CARICA DB."""
    import csv, io
    rows = []
    for i in range(100):
        lon = 9.0 + (i % 10) * 0.01
        lat = 45.0 + (i // 10) * 0.01
        rows.append({
            "id":       i + 1,
            "nome":     f"Punto_{i+1:03d}",
            "valore":   round(i * 1.5, 2),
            "geometry": f"POINT({lon:.4f} {lat:.4f})",
        })
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["id","nome","valore","geometry"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post(
        "/files/upload",
        files={"file": ("carica_small.csv", data, "text/csv")},
        data={"name": "[AUTO] C carica_small"},
    )
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


@pytest.fixture(scope="session")
def csv_carica_empty(client) -> str:
    """CSV con solo header (0 righe) — per C-23."""
    data = b"id,nome,valore,geometry\n"
    res = client.post(
        "/files/upload",
        files={"file": ("carica_empty.csv", data, "text/csv")},
        data={"name": "[AUTO] C carica_empty"},
    )
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


@pytest.fixture(scope="session")
def csv_carica_type_error(client) -> str:
    """CSV con stringa in colonna che dovrebbe essere INTEGER — per C-09."""
    import csv, io
    rows = [
        {"id": "NOT_AN_INT", "nome": "Test", "valore": 1.0},
        {"id": 2, "nome": "OK", "valore": 2.0},
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["id","nome","valore"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post(
        "/files/upload",
        files={"file": ("carica_type_error.csv", data, "text/csv")},
        data={"name": "[AUTO] C type_error"},
    )
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


@pytest.fixture(scope="session")
def csv_carica_points(client) -> str:
    """CSV con 500 punti WKT — per test ArcGIS C-11→C-15."""
    import csv, io
    rows = []
    for i in range(500):
        lon = 9.0 + (i % 25) * 0.01
        lat = 45.0 + (i // 25) * 0.01
        rows.append({
            "objectid": i + 1,
            "nome":     f"Feature_{i+1:03d}",
            "valore":   round(i * 2.5, 2),
            "geometry": f"POINT({lon:.4f} {lat:.4f})",
        })
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["objectid","nome","valore","geometry"])
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    res = client.post(
        "/files/upload",
        files={"file": ("carica_points.csv", data, "text/csv")},
        data={"name": "[AUTO] C carica_points"},
    )
    assert res.status_code == 200
    fid = res.json()["id"]
    yield fid
    client.delete(f"/files/{fid}")


# =============================================================================
# BUILDER: CANVAS CARICA
# =============================================================================

def build_carica_canvas(
    file_source_id: str,
    output_format: str = "csv",
    output_filename: str = "output_carica",
) -> Dict:
    """
    Costruisce un canvas per CARICA con file_source → result.
    Il canvas è di tipo arricchisci — l'executor arricchisci gestisce
    il nodo file_source e lo passa al nodo result, salvando il blob nel DB.
    """
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
# HELPER: ESECUZIONE PROGETTO CARICA
# =============================================================================

def execute_carica(
    client,
    project_id: str,
    canvas: Dict,
    carica_node_data: Dict,
    output_format: str = "csv",
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Esegue un task CARICA:
    1. Esegue prima la pipeline upstream (FileSource → Result) per generare output
    2. Poi lancia il task CARICA su quel project_id

    Returns:
        Risultato del task: {"success": bool, "rows_written": int, "status": str, ...}

    Raises:
        TimeoutError se non completa entro timeout.
    """
    # Usa l'endpoint dedicato carica-async (come enrich-async per ARRICCHISCI)
    # Il backend gestisce internamente: ricalcola pipeline upstream + scrive su DB/ArcGIS
    payload = {
        "carica_node_data":  carica_node_data,
        "format":            output_format,
        "force_recalculate": True,
    }
    res = client.post(f"/projects/{project_id}/carica-async", json=payload)
    assert res.status_code == 200, f"Errore avvio CARICA: {res.text}"
    task_id = res.json().get("task_id")
    assert task_id, f"task_id non trovato: {res.json()}"
    result = poll_task(client, task_id, timeout=timeout)
    return result


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
        # Il backend legge match_columns per UPDATE/UPSERT
        node["match_columns"]      = [key_column]
        node["key_column"]         = key_column
        node["key_columns"]        = [key_column]
        node["unique_key_column"]  = key_column
    return node


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
