# Plenora E2E — pytest runner

Harness pytest per eseguire i test catalogati in `test-e2e/test-e2e.md`.

## Layout

```
runner/
├── README.md            # questo file
├── pytest.ini           # config pytest, marker, opzioni
├── requirements.txt     # dipendenze runtime (httpx, sqlalchemy, ecc.)
├── conftest.py          # fixture session-scope condivise
└── tests/
    ├── __init__.py
    └── test_prep_smoke.py    # PREP-001 — stack docker pronto
```

I test successivi vivranno in `tests/test_<area>_*.py` (es.
`test_ext_db.py`, `test_man_join.py`, ecc.).

## Prerequisiti

- Lo **stack docker** in `test-e2e/docker/` deve essere up. Vedi
  `test-e2e/docker/README.md` per i profili.
- L'**app plenora** (FastAPI backend) deve girare e rispondere su
  `${PLENORA_API_URL}` (default `http://localhost:8000`).
- Python 3.11+ con dipendenze:
  ```fish
  python3 -m venv .venv
  source .venv/bin/activate.fish
  pip install -r requirements.txt
  ```

## Contract test (pre-Fase 1)

Prima della Fase 1 (smoke interattivi), esegui i contract test:

```fish
pytest -m contract -v
```

Se uno fallisce, il payload prodotto da `runner/builders/` o
`runner/factories.py` non combacia col contratto plenora. Aggiorna
prima i layer di costruzione, poi prosegui con la Fase 1.

I 6 contract test (CT-001..CT-006) sono shallow per design: validano
solo la shape, non la logica. Sono i primi a rompere se plenora cambia
un endpoint critico.

## Esecuzione

I test usano marker per tag area e profilo richiesto. Esempi:

```fish
# tutti i test smoke (PREP)
pytest tests/test_prep_smoke.py

# solo i test che richiedono profilo "core"
pytest -m "profile_core"

# un singolo test by id (es. PREP-001)
pytest -k prep_001 -s

# verbose + log live (utile in fase manuale interattiva)
pytest -v -s -ra tests/test_prep_smoke.py
```

## Variabili d'ambiente

Tutte hanno default sensibili; override solo se serve.

| Var | Default | Uso |
|-----|---------|-----|
| `PLENORA_API_URL` | `http://localhost:5004` | URL base dell'API plenora (porta del backend, vedi root `docker-compose.yml`) |
| `PLENORA_E2E_PROFILE` | `core` | profilo docker compose richiesto (`core`/`db`/`db-heavy`/`cloud`/`files`/`full`) |
| `PLENORA_E2E_USER` | `tester@plenora.test` | utente test usato per il login |
| `PLENORA_E2E_PASSWORD` | `plenora_test_pwd` | password utente test |
| `PLENORA_E2E_COMPOSE_FILE` | `<repo>/test-e2e/docker/docker-compose.yml` | compose master |
| `PLENORA_E2E_TIMEOUT` | `30` | timeout HTTP in secondi |
| `PLENORA_E2E_VERIFY_DOCKER` | `1` | se `0`, salta la verifica `docker compose ps` |

## Convenzioni

- Ogni test ha un docstring con l'**ID dal catalogo** e una frase di
  cosa verifica (es. `"""PREP-001 — Stack docker pronto."""`).
- I test sono nominati `test_<id_lower>_<slug>` (es.
  `test_prep_001_stack_healthy`).
- Marker `pytest.mark.profile_<name>` indica il profilo minimo richiesto.
  Esempio: `@pytest.mark.profile_core`, `@pytest.mark.profile_db`.
- Marker `pytest.mark.requires_app` indica che il test richiede
  l'app plenora oltre al docker stack.
- Le **fixture** in `conftest.py` sono session-scope e teardown-aware
  (cleanup gruppo, connection, ecc.).

## Cross-cutting assertions

Invece di test isolati per concern (un solo `AUDIT-001`, un solo
`REQID-001`, un solo `LOG-004`), ogni test eredita asserzioni globali
(request_id, PII) via fixture autouse, e puo' chiamare helper
specifici per concern aggiuntivi (audit log, idempotency,
rate-limit, security headers, permission boundary).

Il modulo e' `runner/cross_cutting.py`. Filosofia: ogni POST scrive
audit log -> tutti i test che fanno POST verificano che l'audit log
sia scritto, NON un singolo test dedicato.

### Asserzioni applicate automaticamente (autouse)

| Check | Trigger | Test esenti |
|---|---|---|
| `X-Request-ID` echo + max 64 char | ogni 2xx response che passa per `http_client` | `@pytest.mark.no_global_assertions` |
| No PII/secret in chiaro nel body | ogni response con content-type testuale | smoke (no_app) — la fixture si auto-spegne se il test non usa `http_client` |

Per disattivare i check globali su un test specifico (es. test che
verifica intenzionalmente l'echo di un valore mock):

```python
@pytest.mark.no_global_assertions
def test_xxx_pii_intentional(...): ...
```

### Helper opt-in

Da chiamare esplicitamente nei test che fanno azioni write o che
toccano un concern specifico:

```python
from runner.cross_cutting import (
    assert_audit_logged,
    assert_idempotent_post,
    assert_security_headers,
    assert_rate_limit_headers_present,
    assert_403_for_role,
    assert_resource_deleted,
    assert_request_id_propagated,
    assert_no_pii_in_response,
    assert_no_pii_in_logs,
    poll_until,
)

def test_prep_004_create_postgres_connection(http_client, auth_headers, group_id):
    """PREP-004 + cross-cutting audit + idempotency."""
    payload = {...}
    r = http_client.post("/api/connections", json=payload, headers=auth_headers)
    assert r.status_code == 201

    # cross-cutting opt-in (riusabili in qualunque test write)
    assert_audit_logged(
        http_client, auth_headers,
        action="connection.create", resource_id=r.json()["id"],
    )
    # request_id propagation gia' assertito dalla fixture autouse
```

NB: gli helper che dipendono da endpoint plenora non ancora esposti
(es. lettura `audit_log`) skippano via `pytest.skip` con messaggio
"endpoint non esposto" — non producono falsi fail.

### Attivare la fixture autouse

La fixture `_global_response_audit` vive in `runner/cross_cutting.py`.
Per attivarla globalmente importarla nel `conftest.py`:

```python
from runner.cross_cutting import _global_response_audit  # noqa: F401
```

In alternativa, importare le sole funzioni helper nei singoli test
che le vogliono usare (gli helper non richiedono la fixture autouse
per funzionare).

## Test data factories & parametrize helper

`runner/factories.py` espone scenari plenora pronti (canvas completi,
liste connection, dataset di reference). Costruiti sopra i `builders/`
quando disponibili (`BUILDERS_AVAILABLE`); altrimenti producono dict
inline che combaciano con i model in `app/core/projects/canvas_models.py`.

Categorie di factory:

- **Canvas**: `canvas_empty`, `canvas_estrai_postgres_customers`,
  `canvas_estrai_with_filter`, `canvas_join_customers_cities_regions`,
  `canvas_spatial_within`, `canvas_estrai_arricchisci_carica`,
  `canvas_csv_to_postgres`, `canvas_geojson_with_geo_filter`,
  `canvas_aggregate_sum_per_region`, `canvas_transform_filter_null`,
  `canvas_pagination_offset`.
- **Connections / Datasources**: `all_db_connections`,
  `all_auth_api_connections`, `all_file_datasources`.
- **Project**: `project_estrai_payload`, `project_arricchisci_payload`,
  `project_manipola_payload`.
- **Dataset reference** (derivati da `sql/expected/queries/*.json`):
  `expected_customers_count`, `expected_table_counts`,
  `expected_cities_within_lombardia`, `expected_cities_within_campania`,
  `expected_customers_per_region`, `expected_employees_max_depth`,
  `expected_customers_without_orders_count`,
  `expected_customers_with_null_age_count`.
- **Geometry**: `lombardia_polygon_wkt`, `campania_polygon_wkt`,
  `sample_point_wkt`.

`runner/parametrize.py` espone helper per replicare lo stesso test su
più dimensioni con un decorator unico:

| Helper | Dimensione | Valori |
|--------|------------|--------|
| `parametrize_dialects` | DB dialects | postgres / mysql / mssql / oracle / sqlite |
| `parametrize_auth_styles` | REST auth | api_key_header/query, bearer, basic, oauth2_cc, oauth2_password, arcgis_token |
| `parametrize_pagination_styles` | Paginazione REST | offset, page_size, cursor, link_header, no_meta |
| `parametrize_geometry_types` | Tipi GeoJSON | Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon, GeometryCollection |
| `parametrize_file_formats` | Formati file | tabular (csv/xlsx/xls/json/ndjson/parquet) + spatial (geojson/shp/gpkg) |
| `parametrize_write_modes` | Modalità `carica` | create, replace, append, upsert, update, truncate_insert |
| `parametrize_join_types` | JOIN types | inner, left, right, outer, cross |
| `parametrize_project_types` | Project types | estrai, manipola, arricchisci |
| `parametrize_spatial_predicates` | Predicati spaziali | ST_Within, ST_Intersects, ST_Contains, ST_DWithin, ST_Touches, ST_Overlaps |

Ogni helper applica il marker `profile_*` corretto per ogni param, così
`pytest -m profile_core` salta automaticamente i casi heavy/db.

Le fixture parametrizzate (`db_connection_for_dialect`,
`api_conn_for_auth_style`, `datasource_for_format`) vivono in
`runner/parametrized_fixtures.py` e si importano nel test module che
le usa (pytest le raccoglie via import — niente modifica al
`conftest.py` globale).

Esempio:

```python
from runner.factories import canvas_join_customers_cities_regions
from runner.parametrize import parametrize_dialects
from runner.parametrized_fixtures import db_connection_for_dialect  # noqa: F401

@parametrize_dialects(only_light=True)
def test_join_cross_dialect(
    dialect, db_connection_for_dialect, http_client, auth_headers, project_id
):
    canvas = canvas_join_customers_cities_regions(
        connection_id=db_connection_for_dialect
    )
    r = http_client.put(
        f"/api/projects/{project_id}/canvas",
        json=canvas, headers=auth_headers,
    )
    assert r.status_code in (200, 201)
```

Smoke test pure-Python (no infra) in
`runner/tests/test_factories_smoke.py`.

## Flusso operativo (vedi `test-e2e.md` Roadmap)

1. **Fase 1 — interattivo**: ~20 test smoke. Loop: scrivo test → tu
   lanci → io leggo asserzioni → tu rivedi visivamente sul frontend →
   marchio `DONE` nel catalogo.
2. **Fase 2 — batch**: ~225 test rimanenti, in pytest senza intervento
   visivo per ogni test.
3. **Fase 3 — CI**: profilo `core` per ogni PR, `full` notturno.
