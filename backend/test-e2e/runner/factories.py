"""Test data factories: scenari plenora pronti per l'uso.

Differenza vs ``builders/``:
- ``builders/`` = fluent API low-level (un blocco alla volta)
- ``factories.py`` = scenari high-level pronti (canvas completi, dataset)

Le factories sono **pure** (nessuna chiamata HTTP, nessun side effect):
ritornano dict serializzabili pronti per essere passati come body a
``PUT /api/projects/{id}/canvas`` o ``POST /api/connections`` ecc.

Le factories ``canvas_*`` ritornano un body compatibile con
``CanvasWithPlugins`` (vedi ``app/core/projects/router.py:474``):
``{"nodes": [...], "edges": [...]}``. I node ``type`` validi sono in
``app/core/projects/canvas_models.py:96`` — qui usiamo principalmente
``source`` (estrai SQL da connection+table), ``api_enrich``, ``carica``
(load DB), ``transform``, ``multi_join``, ``datasource_node``,
``file_source`` e ``result``.

I valori di reference (count righe, id attesi) sono derivati dai file
in ``test-e2e/sql/expected/queries/`` — quelli sono la **single source
of truth** del seed dataset. Se il seed cambia, aggiornare prima quei
file e poi qui (o leggerli a runtime con ``load_expected``).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

# ── Import builders (Agent B) — fallback se non disponibili ───────────
#
# I builders sono in fase di implementazione in parallelo (Agent B).
# Quando saranno disponibili, importeremo le classi e ricostruiremo i
# canvas via fluent API. Nel frattempo costruiamo i dict inline, con
# struttura che combacia con `Node`/`Edge` di canvas_models.py.

try:
    from .builders import (  # type: ignore[import-not-found]
        ApiConnectionBuilder,
        CanvasBuilder,
        ConnectionBuilder,
        DatasourceBuilder,
    )

    BUILDERS_AVAILABLE = True
except ImportError:
    BUILDERS_AVAILABLE = False
    ApiConnectionBuilder = None  # type: ignore[assignment]
    CanvasBuilder = None  # type: ignore[assignment]
    ConnectionBuilder = None  # type: ignore[assignment]
    DatasourceBuilder = None  # type: ignore[assignment]


# ── Path helpers ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPECTED_DIR = _REPO_ROOT / "backend" / "test-e2e" / "sql" / "expected" / "queries"


def _load_expected(name: str) -> Any:
    """Carica un fixture JSON da ``sql/expected/queries/{name}.json``.

    Ritorna ``None`` se il file non esiste — utile per non far crashare
    le factory in ambienti dove il seed non è generato.
    """
    path = _EXPECTED_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _node_id(prefix: str = "n") -> str:
    """Genera un id nodo univoco e deterministicamente prefisso-able."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _edge_id(prefix: str = "e") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── Canvas factories (pipeline pronte) ────────────────────────────────

def canvas_empty() -> dict:
    """Canvas vuoto, idempotente. Utile come baseline per PREP-019."""
    return {"nodes": [], "edges": []}


def canvas_estrai_postgres_customers(*, connection_id: str) -> dict:
    """Canvas semplice: 1 nodo che estrae ``customers`` da postgres."""
    src = _node_id("src")
    res = _node_id("res")
    return {
        "nodes": [
            {
                "id": src,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": connection_id,
                "table_name": "customers",
                "alias": "customers",
            },
            {
                "id": res,
                "type": "result",
                "position": {"x": 400, "y": 100},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": src,
                "target": res,
                "join_type": "output",
            }
        ],
    }


def canvas_estrai_with_filter(
    *, connection_id: str, table: str, column: str, operator: str, value: Any
) -> dict:
    """Canvas: 1 source + filtro WHERE su una colonna.

    Esempio: ``canvas_estrai_with_filter(connection_id=cid, table="orders",
    column="total_amount", operator=">", value=100)``.
    """
    src = _node_id("src")
    res = _node_id("res")
    return {
        "nodes": [
            {
                "id": src,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": connection_id,
                "table_name": table,
                "alias": table,
                "filters": [
                    {
                        "column": column,
                        "operator": operator,
                        "value": value,
                    }
                ],
            },
            {
                "id": res,
                "type": "result",
                "position": {"x": 400, "y": 100},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": src,
                "target": res,
                "join_type": "output",
            }
        ],
    }


def canvas_join_customers_cities_regions(*, connection_id: str) -> dict:
    """Canvas: estrai 3 tabelle + 2 join (customers → cities → regions).

    Riproduce l'EXT-003 / MAN-001 (LEFT JOIN cross-table). Edge:
    customers.city_id = cities.id, cities.region_code = regions.code.
    """
    n_customers = _node_id("customers")
    n_cities = _node_id("cities")
    n_regions = _node_id("regions")
    n_join = _node_id("join")
    n_res = _node_id("res")

    return {
        "nodes": [
            {
                "id": n_customers,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": connection_id,
                "table_name": "customers",
                "alias": "customers",
            },
            {
                "id": n_cities,
                "type": "source",
                "position": {"x": 100, "y": 250},
                "connection_id": connection_id,
                "table_name": "cities",
                "alias": "cities",
            },
            {
                "id": n_regions,
                "type": "source",
                "position": {"x": 100, "y": 400},
                "connection_id": connection_id,
                "table_name": "regions",
                "alias": "regions",
            },
            {
                "id": n_join,
                "type": "multi_join",
                "position": {"x": 400, "y": 250},
                "multi_join_type": "left",
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 700, "y": 250},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_customers,
                "target": n_join,
                "join_type": "left",
                "source_column": "city_id",
                "target_column": "id",
            },
            {
                "id": _edge_id(),
                "source": n_cities,
                "target": n_join,
                "join_type": "left",
                "source_column": "region_code",
                "target_column": "code",
            },
            {
                "id": _edge_id(),
                "source": n_regions,
                "target": n_join,
                "join_type": "left",
            },
            {
                "id": _edge_id(),
                "source": n_join,
                "target": n_res,
                "join_type": "output",
            },
        ],
    }


def canvas_spatial_within(
    *,
    connection_id: str,
    points_table: str = "cities",
    polygons_table: str = "regions",
    points_geom: str = "location",
    polygons_geom: str = "boundary",
) -> dict:
    """Canvas spatial: punti dentro poligoni via ``ST_Within``.

    Riproduce EXT-005 (cities WITHIN regions.boundary).
    """
    n_pts = _node_id("points")
    n_poly = _node_id("polygons")
    n_join = _node_id("sjoin")
    n_res = _node_id("res")
    return {
        "nodes": [
            {
                "id": n_pts,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": connection_id,
                "table_name": points_table,
                "alias": points_table,
            },
            {
                "id": n_poly,
                "type": "source",
                "position": {"x": 100, "y": 300},
                "connection_id": connection_id,
                "table_name": polygons_table,
                "alias": polygons_table,
            },
            {
                "id": n_join,
                "type": "multi_join",
                "position": {"x": 400, "y": 200},
                "multi_join_type": "inner",
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 700, "y": 200},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_pts,
                "target": n_join,
                "join_type": "inner",
                "is_spatial": True,
                "spatial_predicate": "ST_Within",
                "source_geom_column": points_geom,
                "target_geom_column": polygons_geom,
            },
            {
                "id": _edge_id(),
                "source": n_poly,
                "target": n_join,
                "join_type": "inner",
            },
            {
                "id": _edge_id(),
                "source": n_join,
                "target": n_res,
                "join_type": "output",
            },
        ],
    }


def canvas_estrai_arricchisci_carica(
    *,
    postgres_conn_id: str,
    api_conn_id: str,
    target_table: str = "customers_enriched",
    source_table: str = "customers",
) -> dict:
    """Canvas e2e tipico: estrai DB → arricchisci REST → carica DB.

    Pipeline 3 nodi: ``source`` → ``api_enrich`` → ``carica``. È il
    pattern canonico dei progetti ``arricchisci`` plenora.
    """
    n_src = _node_id("src")
    n_api = _node_id("api")
    n_load = _node_id("load")
    return {
        "nodes": [
            {
                "id": n_src,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "connection_id": postgres_conn_id,
                "table_name": source_table,
                "alias": source_table,
            },
            {
                "id": n_api,
                "type": "api_enrich",
                "position": {"x": 400, "y": 200},
                "api_id": api_conn_id,
                "param_mapping": {},
                "output_mapping": {},
            },
            {
                "id": n_load,
                "type": "carica",
                "position": {"x": 700, "y": 200},
                "dest_type": "db",
                "connection_id": postgres_conn_id,
                "table_name": target_table,
                "write_mode": "create",
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_src,
                "target": n_api,
                "join_type": "left",
            },
            {
                "id": _edge_id(),
                "source": n_api,
                "target": n_load,
                "join_type": "carica",
            },
        ],
    }


def canvas_csv_to_postgres(
    *,
    datasource_id: str,
    csv_path: str,
    conn_id: str,
    target_table: str,
) -> dict:
    """Pipeline CSV → DB target. Datasource (FTP/SFTP/local) → carica DB."""
    n_ds = _node_id("ds")
    n_load = _node_id("load")
    return {
        "nodes": [
            {
                "id": n_ds,
                "type": "datasource_node",
                "position": {"x": 100, "y": 200},
                "datasource_id": datasource_id,
                "mode": "source",
                "file_path": csv_path,
                "format": "csv",
            },
            {
                "id": n_load,
                "type": "carica",
                "position": {"x": 500, "y": 200},
                "dest_type": "db",
                "connection_id": conn_id,
                "table_name": target_table,
                "write_mode": "create",
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_ds,
                "target": n_load,
                "join_type": "carica",
            }
        ],
    }


def canvas_geojson_with_geo_filter(
    *,
    datasource_id: str,
    geojson_path: str,
    region_polygon_wkt: str,
    geom_column: str = "geometry",
) -> dict:
    """Estrai GeoJSON + filtro spaziale ``ST_Within(polygon)``.

    Il filtro usa il transform_op ``geo_clip`` con polygon WKT come
    parametro di config. Pattern usato da FILES-005..010.
    """
    n_ds = _node_id("ds")
    n_clip = _node_id("clip")
    n_res = _node_id("res")
    return {
        "nodes": [
            {
                "id": n_ds,
                "type": "datasource_node",
                "position": {"x": 100, "y": 200},
                "datasource_id": datasource_id,
                "mode": "source",
                "file_path": geojson_path,
                "format": "geojson",
                "geomColumn": geom_column,
            },
            {
                "id": n_clip,
                "type": "transform",
                "position": {"x": 400, "y": 200},
                "transform_op": "geo_clip",
                "transform_category": "geometric",
                "config": {
                    "geom_column": geom_column,
                    "clip_polygon_wkt": region_polygon_wkt,
                },
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 700, "y": 200},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_ds,
                "target": n_clip,
                "join_type": "left",
            },
            {
                "id": _edge_id(),
                "source": n_clip,
                "target": n_res,
                "join_type": "output",
            },
        ],
    }


def canvas_aggregate_sum_per_region(*, connection_id: str) -> dict:
    """Canvas: ``orders`` aggregati con SUM(total_amount) per anno.

    Riproduce il pattern di ``orders_total_per_year`` dal seed.
    """
    n_src = _node_id("src")
    n_agg = _node_id("agg")
    n_res = _node_id("res")
    return {
        "nodes": [
            {
                "id": n_src,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "connection_id": connection_id,
                "table_name": "orders",
                "alias": "orders",
            },
            {
                "id": n_agg,
                "type": "transform",
                "position": {"x": 400, "y": 200},
                "transform_op": "aggregate",
                "transform_category": "base",
                "group_by_enabled": True,
                "group_by_columns": ["year"],
                "aggregations": [
                    {"function": "SUM", "column": "total_amount", "alias": "total"}
                ],
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 700, "y": 200},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_src,
                "target": n_agg,
                "join_type": "left",
            },
            {
                "id": _edge_id(),
                "source": n_agg,
                "target": n_res,
                "join_type": "output",
            },
        ],
    }


def canvas_transform_filter_null(
    *, connection_id: str, table: str = "customers", column: str = "age"
) -> dict:
    """Canvas: source + transform ``filter`` IS NULL su una colonna."""
    n_src = _node_id("src")
    n_tr = _node_id("tr")
    n_res = _node_id("res")
    return {
        "nodes": [
            {
                "id": n_src,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "connection_id": connection_id,
                "table_name": table,
                "alias": table,
            },
            {
                "id": n_tr,
                "type": "transform",
                "position": {"x": 400, "y": 200},
                "transform_op": "filter",
                "transform_category": "base",
                "config": {
                    "filters": [{"column": column, "operator": "IS NULL"}]
                },
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 700, "y": 200},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_src,
                "target": n_tr,
                "join_type": "left",
            },
            {
                "id": _edge_id(),
                "source": n_tr,
                "target": n_res,
                "join_type": "output",
            },
        ],
    }


def canvas_pagination_offset(
    *, connection_id: str, table: str = "events_log", limit: int = 1000
) -> dict:
    """Canvas con limit + sort. Pattern paginazione cursor-less."""
    n_src = _node_id("src")
    n_res = _node_id("res")
    return {
        "nodes": [
            {
                "id": n_src,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "connection_id": connection_id,
                "table_name": table,
                "alias": table,
                "limit": limit,
            },
            {
                "id": n_res,
                "type": "result",
                "position": {"x": 400, "y": 200},
            },
        ],
        "edges": [
            {
                "id": _edge_id(),
                "source": n_src,
                "target": n_res,
                "join_type": "output",
            }
        ],
    }


# ── Connection scenarios (payload pronti per POST /api/connections) ──

def all_db_connections() -> list[dict]:
    """Lista di payload per creare le 4 connection DB cross-dialect.

    Ogni payload è valido per ``POST /api/connections`` (ConnectionCreate
    con type ∈ {postgres, mysql, mssql, oracle, sqlite}).
    """
    return [
        {
            "name": "e2e-postgres",
            "description": "e2e PostgreSQL fixture",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        },
        {
            "name": "e2e-mysql",
            "description": "e2e MySQL fixture",
            "type": "mysql",
            "host": "plenora-e2e-mysql",
            "port": 3306,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        },
        {
            "name": "e2e-mssql",
            "description": "e2e SQL Server fixture",
            "type": "mssql",
            "host": "plenora-e2e-sqlserver",
            "port": 1433,
            "database": "plenora_test",
            "username": "sa",
            "password": "PlenoraTest!2026",
        },
        {
            "name": "e2e-oracle",
            "description": "e2e Oracle fixture",
            "type": "oracle",
            "host": "plenora-e2e-oracle",
            "port": 1521,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        },
    ]


def all_auth_api_connections(
    *, fake_rest_url: str = "http://plenora-e2e-fake-rest:8080"
) -> list[dict]:
    """Lista di payload per creare api_connections con tutti gli stili auth.

    Coverage: api_key (header + query), bearer, basic, oauth2_cc,
    oauth2_password, arcgis_token. Ognuno è un body free-form valido
    per ``POST /api/api-connections``.
    """
    return [
        {
            "name": "e2e-rest-apikey-header",
            "description": "api_key in header",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "api_key",
            "api_key": "PLENORA_TEST_KEY",
            "key_location": "header",
            "key_name": "X-API-Key",
        },
        {
            "name": "e2e-rest-apikey-query",
            "description": "api_key in query string",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "api_key",
            "api_key": "PLENORA_TEST_KEY",
            "key_location": "query",
            "key_name": "api_key",
        },
        {
            "name": "e2e-rest-bearer",
            "description": "Bearer static token",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "bearer",
            "token": "PLENORA_BEARER_TOKEN",
        },
        {
            "name": "e2e-rest-basic",
            "description": "HTTP Basic",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "basic",
            "username": "tester",
            "password": "tester_pwd",
        },
        {
            "name": "e2e-rest-oauth2-cc",
            "description": "OAuth2 client_credentials",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "oauth2_client_credentials",
            "token_url": f"{fake_rest_url}/auth/oauth2/token",
            "client_id": "plenora",
            "client_secret": "plenora_secret",
        },
        {
            "name": "e2e-rest-oauth2-pwd",
            "description": "OAuth2 resource_owner_password",
            "connection_type": "rest",
            "base_url": fake_rest_url,
            "auth_type": "oauth2_password",
            "token_url": f"{fake_rest_url}/auth/oauth2/token",
            "client_id": "plenora",
            "client_secret": "plenora_secret",
            "username": "tester",
            "password": "tester_pwd",
        },
        {
            "name": "e2e-arcgis-token",
            "description": "ArcGIS generateToken",
            "connection_type": "arcgis",
            "base_url": fake_rest_url,
            "auth_type": "arcgis_token",
            "token_url": f"{fake_rest_url}/auth/arcgis/generateToken",
            "username": "tester",
            "password": "tester_pass",
        },
    ]


def all_file_datasources() -> list[dict]:
    """Lista di payload per le fixture FTP/SFTP/local datasource."""
    return [
        {
            "name": "e2e-ftp",
            "description": "FTP fixture",
            "type": "ftp",
            "host": "plenora-e2e-ftp",
            "port": 21,
            "username": "tester",
            "password": "plenora_ftp_pwd",
            "root_path": "/fixtures",
            "passive": True,
            "tls": False,
        },
        {
            "name": "e2e-sftp",
            "description": "SFTP fixture",
            "type": "sftp",
            "host": "plenora-e2e-sftp",
            "port": 22,
            "username": "tester",
            "password": "plenora_sftp_pwd",
            "root_path": "/fixtures",
            "host_key_policy": "auto_add",
        },
    ]


# ── Project payload factories ─────────────────────────────────────────

def project_estrai_payload(*, name: str, default_connection_id: str) -> dict:
    """Body per ``POST /api/projects`` con project_type=estrai."""
    return {
        "name": name,
        "description": "e2e estrai project (factory)",
        "project_type": "estrai",
        "default_connection_id": default_connection_id,
    }


def project_arricchisci_payload(*, name: str, default_connection_id: str) -> dict:
    """Body per ``POST /api/projects`` con project_type=arricchisci."""
    return {
        "name": name,
        "description": "e2e arricchisci project (factory)",
        "project_type": "arricchisci",
        "default_connection_id": default_connection_id,
    }


def project_manipola_payload(*, name: str, default_connection_id: str) -> dict:
    """Body per ``POST /api/projects`` con project_type=manipola."""
    return {
        "name": name,
        "description": "e2e manipola project (factory)",
        "project_type": "manipola",
        "default_connection_id": default_connection_id,
    }


# ── Dataset / reference scenarios ─────────────────────────────────────

def expected_customers_count(
    *, include_edge: bool = True, include_null: bool = True
) -> int:
    """Quante righe di ``customers`` nel seed (parametrico).

    Conteggio derivato da ``sql/postgres/01_seed.sql`` e
    ``count_per_table.json`` (totale 22 = 12 base + 8 edge + 2 null).
    """
    base = 12
    edge = 8 if include_edge else 0
    null = 2 if include_null else 0
    return base + edge + null


def expected_table_counts() -> dict[str, int]:
    """Dict ``{table_name: row_count}`` da ``count_per_table.json``.

    Fallback hard-coded se il file non esiste (utile per smoke offline).
    """
    rows = _load_expected("count_per_table")
    if rows is None:
        return {
            "cities": 18,
            "customers": 22,
            "employees": 17,
            "events_log": 10000,
            "order_items": 35,
            "orders": 21,
            "products": 13,
            "regions": 4,
            "routes": 5,
        }
    return {row["table_name"]: row["n"] for row in rows}


def expected_cities_within_lombardia() -> list[int]:
    """Lista degli id cities che cadono nella boundary di Lombardia (LOM).

    Derivato da ``cities_within_lombardia_boundary.json``.
    """
    rows = _load_expected("cities_within_lombardia_boundary")
    if rows is None:
        return [1, 2, 3, 10, 11]
    return [row["id"] for row in rows]


def expected_cities_within_campania() -> list[int]:
    """Id cities dentro Campania (CAM).

    Derivato da ``cities_within_campania_boundary.json``.
    """
    rows = _load_expected("cities_within_campania_boundary")
    if rows is None:
        return [7, 8, 9, 12, 13]
    return [row["id"] for row in rows]


def expected_customers_per_region() -> dict[str, int]:
    """Dict ``{region_code: n_customers}`` da ``customers_per_region.json``."""
    rows = _load_expected("customers_per_region")
    if rows is None:
        return {"CAM": 3, "LAZ": 6, "LOM": 11}
    return {row["region_code"]: row["n_customers"] for row in rows}


def expected_employees_max_depth() -> int:
    """Profondità massima della catena manager_id in ``employees`` (5 livelli)."""
    rows = _load_expected("employees_5_levels_deep")
    if rows is None:
        return 5
    return max(row["depth"] for row in rows)


def expected_customers_without_orders_count() -> int:
    """Numero di customers senza ordini (LEFT JOIN exclusion)."""
    rows = _load_expected("customers_without_orders")
    if rows is None:
        return 12
    return len(rows)


def expected_customers_with_null_age_count() -> int:
    """Customers con ``age IS NULL`` (edge dataset)."""
    rows = _load_expected("customers_with_null_age")
    if rows is None:
        return 1
    return len(rows)


# ── Geometry fixtures (WKT pronte per filtri spatial) ─────────────────

def lombardia_polygon_wkt() -> str:
    """Polygon WKT che contiene tutte le città della Lombardia nel seed.

    Coordinate approssimate: bounding box che racchiude Milano, Bergamo,
    Brescia, Milano Centro, Mêntina (lat 45.0-46.0, lon 8.5-10.5).
    """
    return (
        "POLYGON((8.5 45.0, 10.5 45.0, 10.5 46.0, 8.5 46.0, 8.5 45.0))"
    )


def campania_polygon_wkt() -> str:
    """Polygon WKT che racchiude le città della Campania nel seed."""
    return (
        "POLYGON((14.0 40.5, 15.0 40.5, 15.0 41.2, 14.0 41.2, 14.0 40.5))"
    )


def sample_point_wkt(*, lon: float = 9.19, lat: float = 45.4642) -> str:
    """Point WKT (default: Milano centro)."""
    return f"POINT({lon} {lat})"


# ── Public registry ───────────────────────────────────────────────────
#
# Esposto per introspection nei test smoke: lista deterministica delle
# factory disponibili. Aggiornare quando si aggiungono factory.

CANVAS_FACTORIES: tuple[str, ...] = (
    "canvas_empty",
    "canvas_estrai_postgres_customers",
    "canvas_estrai_with_filter",
    "canvas_join_customers_cities_regions",
    "canvas_spatial_within",
    "canvas_estrai_arricchisci_carica",
    "canvas_csv_to_postgres",
    "canvas_geojson_with_geo_filter",
    "canvas_aggregate_sum_per_region",
    "canvas_transform_filter_null",
    "canvas_pagination_offset",
)

CONNECTION_FACTORIES: tuple[str, ...] = (
    "all_db_connections",
    "all_auth_api_connections",
    "all_file_datasources",
)

PROJECT_FACTORIES: tuple[str, ...] = (
    "project_estrai_payload",
    "project_arricchisci_payload",
    "project_manipola_payload",
)

DATASET_FACTORIES: tuple[str, ...] = (
    "expected_customers_count",
    "expected_table_counts",
    "expected_cities_within_lombardia",
    "expected_cities_within_campania",
    "expected_customers_per_region",
    "expected_employees_max_depth",
    "expected_customers_without_orders_count",
    "expected_customers_with_null_age_count",
)

GEOMETRY_FACTORIES: tuple[str, ...] = (
    "lombardia_polygon_wkt",
    "campania_polygon_wkt",
    "sample_point_wkt",
)

ALL_FACTORIES: tuple[str, ...] = (
    CANVAS_FACTORIES
    + CONNECTION_FACTORIES
    + PROJECT_FACTORIES
    + DATASET_FACTORIES
    + GEOMETRY_FACTORIES
)
