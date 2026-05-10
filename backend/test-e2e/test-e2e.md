# Plenora — Test E2E

> Catalogo dei test end-to-end. **Generato automaticamente**
> da `test-e2e/catalog/*.yaml` via `test-e2e/catalog/render.py`.
> NON modificare a mano: editare i YAML e rieseguire il render.

Ogni voce è uno scenario eseguibile sullo stack `test-e2e/docker/`.
La granularità è 'un canvas plenora → un'asserzione'.

## Convenzioni

- **ID test**: `<area>-<NNN>` (es. `EXT-001`, `LOAD-014`).
- **Stato**: `TODO` (da scrivere) · `WIP` (in stesura) · `DONE` (passa) · `SKIP` (rinviato).
- **Prereq**: profilo docker compose (`core`, `db`, `cloud`, `files`, `db-heavy`, `full`)
  e/o test che devono essere DONE prima.
- **Asserzione**: cosa il test verifica (output finale, error code, stato DB).
- **Meta**: granularity (smoke/integration/scenario), priority (must/nice/optional/meta), tags.

## Distribuzione priorità

| priority | count |     % |
|----------|------:|------:|
| must     |    50 | 13.5% |
| nice     |   227 | 61.4% |
| optional |    79 | 21.4% |
| meta     |    14 |  3.8% |
| total    |   370 | 100.0% |

> Definizioni: **must** = gate merge (smoke/regression core); **nice** = CI nightly (valore alto, non bloccante); **optional** = edge case secondari (scrivere solo se i sopra sono fatti); **meta** = policy / cross-cutting (non test eseguibile autonomo).

## Statistiche (auto)

- **Totale test**: 370

### Per stato
- `TODO`: 356 (96.2%)
- `WIP`: 0 (0.0%)
- `DONE`: 0 (0.0%)
- `SKIP`: 0 (0.0%)
- `META`: 14 (3.8%)

### Per priorità
- `must`: 50 (13.5%)
- `nice`: 227 (61.4%)
- `optional`: 79 (21.4%)
- `meta`: 14 (3.8%)

### Per area

| Area | Count |
|------|------:|
| `prep` | 23 |
| `api` | 15 |
| `ext` | 30 |
| `man` | 11 |
| `enr` | 8 |
| `load` | 15 |
| `arc` | 8 |
| `sch` | 8 |
| `auth` | 4 |
| `inf` | 15 |
| `sec` | 7 |
| `ssrf` | 3 |
| `sp` | 6 |
| `sftp` | 5 |
| `cloud` | 4 |
| `qstr` | 2 |
| `stg` | 2 |
| `mfa` | 3 |
| `pwd` | 3 |
| `audit` | 3 |
| `adm` | 4 |
| `conc` | 6 |
| `res` | 6 |
| `int` | 6 |
| `bck` | 7 |
| `tenant` | 6 |
| `perm` | 6 |
| `mig` | 5 |
| `perf` | 5 |
| `size` | 11 |
| `loc` | 5 |
| `undo` | 7 |
| `flow` | 5 |
| `rate` | 5 |
| `hook` | 5 |
| `cors` | 6 |
| `log` | 5 |
| `idem` | 6 |
| `beat` | 6 |
| `catch` | 5 |
| `recon` | 6 |
| `ret` | 4 |
| `clean` | 3 |
| `intm` | 10 |
| `shr` | 6 |
| `sys` | 9 |
| `task` | 5 |
| `tkt` | 11 |
| `files` | 2 |
| `imdf` | 5 |
| `reqid` | 4 |
| `eseg` | 7 |
| `contract` | 6 |

---

## PREP — Preparatori (building block)

### PREP-001 — Stack docker pronto
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Passi**: 1) `docker compose up -d` 2) attendi healthcheck 3) ping `fake-rest:8080/health` + `pg_isready` postgres + `redis-cli ping`
- **Asserzione**: tutti i container `(healthy)` entro 90s
- **Meta**: granularity=integration · priority=must · tags=setup,infra

### PREP-002 — Login utente test
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Passi**: 1) `POST /auth/login` con `tester@plenora.test` / pwd 2) salva JWT
- **Asserzione**: 200 + `access_token` + `refresh_token`
- **Meta**: granularity=integration · priority=must · tags=setup

### PREP-003 — Crea gruppo di test
- **Stato**: TODO
- **Prereq**: PREP-002
- **Passi**: `POST /groups` con `name=e2e-<uuid>`
- **Asserzione**: 201 + `group_id`. Salva per il teardown.
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-004 — Crea connection PostgreSQL
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /connections` con `type=postgres, host=plenora-e2e-postgres, port=5432, database=plenora_test, user=plenora, password=plenora_test_pwd`
- **Asserzione**: 201 + `connection_id`. Password cifrata at-rest.
- **Meta**: granularity=smoke · priority=must · tags=setup,postgres

### PREP-005 — Crea connection MySQL
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `db`
- **Passi**: `POST /connections` con `type=mysql, host=plenora-e2e-mysql, port=3306, database=plenora_test`
- **Asserzione**: 201 + `connection_id`
- **Meta**: granularity=smoke · priority=must · tags=setup,mysql

### PREP-006 — Crea connection SQL Server
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `db`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,mssql

### PREP-007 — Crea connection Oracle
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `db-heavy`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,oracle

### PREP-008 — Test connection (ping DB)
- **Stato**: TODO
- **Prereq**: PREP-004
- **Passi**: `POST /connections/{id}/test`
- **Asserzione**: 200 + `{"ok": true, "latency_ms": <int>}`
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-009 — Schema introspection
- **Stato**: TODO
- **Prereq**: PREP-004
- **Passi**: `GET /connections/{id}/schemas` → lista schemi → `GET .../schemas/plenora/tables` → lista tabelle
- **Asserzione**: vede regions, cities, customers, products, orders, order_items, employees, events_log, routes (9 tabelle)
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-010 — Crea api_connection REST (api_key)
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /api_connections` con `base_url=http://plenora-e2e-fake-rest:8080, auth_type=api_key, api_key=PLENORA_TEST_KEY, key_location=header(X-API-Key)`
- **Asserzione**: 201 + secret cifrato
- **Meta**: granularity=smoke · priority=must · tags=setup,rest

### PREP-011 — Crea api_connection REST (OAuth2 client_credentials)
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /api_connections` con `auth_type=oauth2_client_credentials, token_url=.../auth/oauth2/token, client_id=plenora, client_secret=plenora_secret`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,oauth2,rest

### PREP-012 — Crea api_connection ArcGIS (token)
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /api_connections` con `auth_type=arcgis_token, token_url=.../auth/arcgis/generateToken, username=tester, password=tester_pass`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,arcgis,rest,auth

### PREP-013 — Test api_connection (ping REST)
- **Stato**: TODO
- **Prereq**: PREP-010
- **Passi**: `POST /api_connections/{id}/test`
- **Asserzione**: 200, auth negotiated
- **Meta**: granularity=smoke · priority=must · tags=setup,rest

### PREP-014 — Crea datasource FTP
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `files`
- **Passi**: `POST /datasources` con `type=ftp, host=plenora-e2e-ftp, port=21, username=tester, password=plenora_ftp_pwd, base_path=/fixtures`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,ftp

### PREP-015 — Crea datasource SFTP
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `files`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,sftp

### PREP-016 — List file datasource
- **Stato**: TODO
- **Prereq**: PREP-014 + PREP-015
- **Passi**: `GET /datasources/{id}/files?path=/fixtures/csv`
- **Asserzione**: lista file con metadata (size, mtime)
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-017 — Crea connection cloud S3 (MinIO)
- **Stato**: TODO
- **Prereq**: PREP-003 + profilo `cloud`
- **Passi**: `POST /connections` con `type=s3, endpoint=http://plenora-e2e-minio:9000, access_key=plenora_minio, secret_key=plenora_minio_pwd, bucket=plenora-test`
- **Asserzione**: 201
- **Meta**: granularity=smoke · priority=must · tags=setup,s3

### PREP-018 — Crea progetto vuoto
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /projects` con `name=e2e-test, group_id=<da PREP-003>`
- **Asserzione**: 201 + `project_id`
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-019 — Crea canvas vuoto
- **Stato**: TODO
- **Prereq**: PREP-018
- **Passi**: `POST /projects/{id}/canvases` con `name=canvas1, definition={}`
- **Asserzione**: 201 + `canvas_id`
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-020 — Salva canvas con nodi (estrai semplice)
- **Stato**: TODO
- **Prereq**: PREP-019 + PREP-004
- **Passi**: `PUT /canvases/{id}` con definition contenente 1 nodo "estrai customers"
- **Asserzione**: 200, definition persistita
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-021 — Esegui canvas (run on-demand)
- **Stato**: TODO
- **Prereq**: PREP-020
- **Passi**: `POST /canvases/{id}/runs` → poll `GET /runs/{run_id}` fino a status=success
- **Asserzione**: run completato, `result_id` presente
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-022 — Recupera result preview
- **Stato**: TODO
- **Prereq**: PREP-021
- **Passi**: `GET /results/{result_id}/preview?limit=50`
- **Asserzione**: 200 + righe + schema
- **Meta**: granularity=smoke · priority=must · tags=setup

### PREP-023 — Cleanup gruppo (cascade)
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `DELETE /groups/{group_id}`
- **Asserzione**: 200 + report con counts (schedules, projects, datasources, connections, file_sources, users, results, intermediate_runs, log_runs). DB target verificato vuoto.
- **Meta**: granularity=smoke · priority=must · tags=setup,cleanup

---

## API — CRUD endpoint plenora (isolati)

### API-001 — Crea connection con credenziali errate → connection-string-test fallisce
- **Stato**: TODO
- **Prereq**: PREP-002
- **Asserzione**: 201 sulla creazione, ma `POST /connections/{id}/test` ritorna 200 + `{"ok": false, "error": "..."}`
- **Meta**: granularity=smoke · priority=must · tags=api

### API-002 — Crea connection senza campi obbligatori → 422
- **Stato**: TODO
- **Asserzione**: 422 con `detail` strutturato (validation errors)
- **Meta**: granularity=smoke · priority=must · tags=api

### API-003 — Crea connection duplicata (stesso name/group) → 409
- **Stato**: TODO
- **Asserzione**: 409 conflict
- **Meta**: granularity=smoke · priority=nice · tags=api

### API-004 — Update connection ruota password
- **Stato**: TODO
- **Asserzione**: nuova password usata, vecchia non più valida
- **Meta**: granularity=smoke · priority=nice · tags=api,password

### API-005 — Delete connection con datasource collegato → 409 o cascade?
- **Stato**: TODO
- **Asserzione**: comportamento documentato (409 se referenced)
- **Meta**: granularity=smoke · priority=nice · tags=api,cleanup

### API-006 — List connections per gruppo (paginazione)
- **Stato**: TODO
- **Asserzione**: page+size rispettati, total corretto
- **Meta**: granularity=smoke · priority=nice · tags=api,pagination

### API-007 — Read-only utente non può creare connection → 403
- **Stato**: TODO
- **Asserzione**: 403
- **Meta**: granularity=smoke · priority=nice · tags=api

### API-008 — Cross-group access negato → 403
- **Stato**: TODO
- **Asserzione**: utente di group A non vede risorse di group B
- **Meta**: granularity=smoke · priority=nice · tags=api,tenancy

### API-009 — Esegui canvas non valido (riferimento a connection cancellata) → errore controllato
- **Stato**: TODO
- **Asserzione**: run status=failed, error message strutturato, no 500
- **Meta**: granularity=smoke · priority=must · tags=api

### API-010 — Schedule CRUD (create, update, disable, delete)
- **Stato**: TODO
- **Asserzione**: ogni operazione ritorna lo stato atteso
- **Meta**: granularity=smoke · priority=nice · tags=api,cleanup

### API-011 — Idempotency-Key su run on-demand
- **Stato**: TODO
- **Passi**: 2× `POST /canvases/{id}/runs` con stessa Idempotency-Key
- **Asserzione**: secondo POST ritorna lo stesso run_id, non ne crea uno nuovo
- **Meta**: granularity=smoke · priority=nice · tags=api,idempotency

### API-012 — Audit log: ogni write API genera entry
- **Stato**: TODO
- **Asserzione**: entry con `actor`, `action`, `resource`, `timestamp`
- **Meta**: granularity=smoke · priority=nice · tags=api,audit

### API-013 — `/projects/{id}/validate-all` aggrega errori cross-node
- **Stato**: TODO
- **Riferimento**: `app/core/projects/router.py`
- **Passi**: 1) crea progetto con 3 canvas, ognuno con almeno un nodo invalido (es. connection cancellata, formula syntax error, riferimento canvas mancante) 2) `POST /projects/{id}/validate-all`
- **Asserzione**: 200 con body `{"valid": false, "errors": [{"canvas_id": ..., "node_id": ..., "error_code": ..., "message": ...}, ...]}`; tutti gli errori dei 3 canvas presenti, no early-exit al primo
- **Meta**: granularity=integration · priority=nice · tags=api

### API-014 — DELETE project con dipendenze → 409 con shape `{kind,id,name,project_type}`
- **Stato**: TODO
- **Riferimento**: `app/core/projects/router.py`
- **Passi**: 1) crea progetto referenziato da schedule attivo 2) `DELETE /projects/{id}`
- **Asserzione**: 409 con body `{"detail": {"error_code": "project_has_dependencies", "dependencies": [{"kind": "schedule", "id": <int>, "name": <str>, "project_type": <str>}, ...]}}`; ogni elemento dependencies ha esattamente le 4 chiavi `kind`, `id`, `name`, `project_type`
- **Meta**: granularity=integration · priority=must · tags=api,cleanup

### API-015 — Validation handler scarta `ctx`+`input` (no PII echo) e supporta dual-shape
- **Stato**: TODO
- **Riferimento**: `handler `RequestValidationError` globale`
- **Passi**: `POST /connections` con body contenente password e campi mancanti
- **Asserzione**: 422; response body NON contiene chiavi `ctx` né `input` (no echo del valore inviato → no PII leak); shape conforme a entrambe le varianti `{detail: [...]}` e `{error: ..., message: ..., details: [...]}`; password originale assente in toto
- **Meta**: granularity=smoke · priority=nice · tags=api

---

## EXT — Estrai

### EXT-001 — Estrai tutto da PostgreSQL/PostGIS
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `sql/postgres/01_seed.sql` (22 customers + 18 cities + 21 orders)
- **Passi**: 1) crea connection postgres → plenora-e2e-postgres:5432 2) canvas estrai `customers` 3) preview
- **Asserzione**: 22 righe, schema esatto, geometry decoded correttamente
- **Meta**: granularity=integration · priority=must · tags=estrai,postgres,spatial

### EXT-002 — Estrai con WHERE su DECIMAL
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `orders` con `total_amount > 100`
- **Asserzione**: filtri applicati al boundary SQL (no fetch full table)
- **Meta**: granularity=smoke · priority=nice · tags=estrai

### EXT-003 — Estrai LEFT JOIN cities/regions con region senza cities
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: regions edge `VEN` senza cities
- **Asserzione**: 4 regions presenti, VEN ha NULL su city columns
- **Meta**: granularity=smoke · priority=nice · tags=estrai,join

### EXT-004 — Estrai CTE ricorsiva employees (5 livelli)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `employees` con catena manager_id
- **Asserzione**: tutti i livelli (helper, intern compresi)
- **Meta**: granularity=smoke · priority=nice · tags=estrai,security

### EXT-005 — Estrai PostGIS ST_Within(point, polygon)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: cities + regions.boundary
- **Asserzione**: città con ST_Within = true matchano la regione corretta
- **Meta**: granularity=smoke · priority=nice · tags=estrai,spatial

### EXT-006 — Estrai 10k rows con paginazione
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `events_log` (10000 righe)
- **Asserzione**: tutti i 10000 letti, ordine deterministico per id
- **Meta**: granularity=smoke · priority=nice · tags=estrai,pagination

### EXT-007 — Estrai cross-dialect: stesso canvas, MySQL invece di Postgres
- **Stato**: TODO
- **Prereq**: profilo `db`
- **Dataset**: `sql/mysql/01_seed.sql`
- **Asserzione**: stesso output di EXT-001
- **Meta**: granularity=smoke · priority=nice · tags=estrai,postgres,mysql

### EXT-008 — Estrai SQL Server con caratteri speciali
- **Stato**: TODO
- **Prereq**: profilo `db`
- **Dataset**: customers edge (`O'Brien`, `北京 用户`, emoji)
- **Asserzione**: caratteri preservati senza mojibake
- **Meta**: granularity=smoke · priority=nice · tags=estrai,mssql

### EXT-009 — Estrai Oracle con SDO_GEOMETRY POINT
- **Stato**: TODO
- **Prereq**: profilo `db-heavy`
- **Dataset**: cities Oracle
- **Asserzione**: SDO decoded a POINT lat/lng corretto
- **Meta**: granularity=smoke · priority=optional · tags=estrai,oracle

### EXT-010 — Estrai CSV (UTF-8 BOM)
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/csv/customers_utf8_bom.csv`
- **Asserzione**: BOM stripped, header riconosciuto
- **Meta**: granularity=smoke · priority=nice · tags=estrai,csv

### EXT-011 — Estrai CSV semicolon delimiter (locale IT)
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/csv/customers_semicolon.csv`
- **Asserzione**: delimiter auto-detected o configurato
- **Meta**: granularity=smoke · priority=nice · tags=estrai,csv,locale

### EXT-012 — Estrai XLSX multi-sheet
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/xlsx/multi_sheet.xlsx`
- **Asserzione**: utente seleziona sheet, plenora legge solo quella
- **Meta**: granularity=smoke · priority=nice · tags=estrai,xlsx

### EXT-013 — Estrai XLSX con formule
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/xlsx/with_formulas.xlsx`
- **Asserzione**: il valore calcolato (non `=SUM(...)`) viene letto
- **Meta**: granularity=smoke · priority=nice · tags=estrai,xlsx

### EXT-014 — Estrai JSON nested deep
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/json/nested.json`
- **Asserzione**: json_path su `address.city` estrae correttamente
- **Meta**: granularity=smoke · priority=nice · tags=estrai,json

### EXT-015 — Estrai NDJSON line-by-line
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/json/customers.ndjson`
- **Asserzione**: 12 righe, una per linea
- **Meta**: granularity=smoke · priority=nice · tags=estrai,json

### EXT-016 — Estrai GeoJSON FeatureCollection
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/geojson/cities_points.geojson`
- **Asserzione**: 9 features, properties + geometry decodificati
- **Meta**: granularity=smoke · priority=nice · tags=estrai,spatial

### EXT-017 — Estrai Shapefile zip
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/shp/cities_points.zip`
- **Asserzione**: dbf attribute + shp geometry letti, EPSG:4326
- **Meta**: granularity=smoke · priority=optional · tags=estrai,spatial

### EXT-018 — Estrai GeoPackage multi-layer
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/gpkg/multi_layer.gpkg`
- **Asserzione**: utente sceglie layer `cities` o `customers`
- **Meta**: granularity=smoke · priority=optional · tags=estrai,spatial

### EXT-019 — Estrai Parquet
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/parquet/customers.parquet`
- **Asserzione**: schema decimal/int32/string preservato
- **Meta**: granularity=smoke · priority=nice · tags=estrai,parquet

### EXT-020 — Estrai Parquet nested struct/list
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `files/parquet/nested.parquet`
- **Asserzione**: struct flattened o accessibile via path
- **Meta**: granularity=smoke · priority=optional · tags=estrai,parquet

### EXT-021 — Estrai REST con api_key in query
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/object_root?api_key=PLENORA_TEST_KEY`
- **Asserzione**: 12 customer letti
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest

### EXT-022 — Estrai REST con bearer token
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/auth/bearer`
- **Asserzione**: header Authorization inviato, 200 OK
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest,auth

### EXT-023 — Estrai REST con paginazione offset
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/page/offset`
- **Asserzione**: tutte le pagine consumate fino a `offset >= total`
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest,pagination

### EXT-024 — Estrai REST con cursor pagination
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/page/cursor`
- **Asserzione**: cursor opaco gestito, has_more rispettato
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest,pagination

### EXT-025 — Estrai REST con Link header (GitHub-style)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/page/link_header`
- **Asserzione**: parsing `rel="next"` corretto
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest

### EXT-026 — Estrai REST root array (no wrapper)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/array_root`
- **Asserzione**: array root parsed senza json_path
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest

### EXT-027 — Estrai REST con json_path nested
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/nested_deep`, json_path `response.items.records`
- **Asserzione**: 12 righe estratte dal nested
- **Meta**: granularity=smoke · priority=nice · tags=estrai,rest

### EXT-028 — Estrai FTP file CSV
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `ftp://tester@ftp:21/fixtures/csv/customers_basic.csv`
- **Asserzione**: download + parse csv
- **Meta**: granularity=smoke · priority=nice · tags=estrai,ftp,csv

### EXT-029 — Estrai SFTP file XLSX
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Dataset**: `sftp://tester@sftp:22/fixtures/xlsx/multi_sheet.xlsx`
- **Asserzione**: SSH session, file letto
- **Meta**: granularity=smoke · priority=nice · tags=estrai,sftp,xlsx

### EXT-030 — Estrai S3/MinIO bucket
- **Stato**: TODO
- **Prereq**: profilo `cloud`
- **Dataset**: `s3://plenora-test/fixtures/csv/customers_basic.csv`
- **Asserzione**: boto3 list+get, parse csv
- **Meta**: granularity=smoke · priority=nice · tags=estrai,s3

---

## MAN — Manipola

### MAN-001 — Filter su NULL handling
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: customers con `email IS NULL`
- **Asserzione**: 1 riga matcha (id 22)
- **Meta**: granularity=smoke · priority=nice · tags=manipola

### MAN-002 — Inner join customers/cities/regions (3 tabelle)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: vedi `expected/README.md` sezione 1
- **Meta**: granularity=smoke · priority=must · tags=manipola,join

### MAN-003 — Left join che preserva customers senza city_id
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: customer id=20 (city_id NULL) presente con city columns NULL
- **Meta**: granularity=smoke · priority=nice · tags=manipola,join

### MAN-004 — Group by + aggregate (customers per region)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: vedi `expected/README.md` sezione 2
- **Meta**: granularity=smoke · priority=nice · tags=manipola,aggregate

### MAN-005 — Formula: line_total = qty * unit_price con NULL
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: order_items con qty=0 + qty=1000
- **Asserzione**: 0 e 9999.99 calcolati correttamente
- **Meta**: granularity=smoke · priority=nice · tags=manipola,formula

### MAN-006 — Geo ST_Distance customer→city
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: distanza in km, sortable
- **Meta**: granularity=smoke · priority=nice · tags=manipola,spatial

### MAN-007 — Geo ST_Within point in polygon
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: cities + regions.boundary
- **Asserzione**: classification per regione corretta
- **Meta**: granularity=smoke · priority=nice · tags=manipola,spatial

### MAN-008 — Pivot/unpivot
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: TBD
- **Meta**: granularity=smoke · priority=nice · tags=manipola,pivot

### MAN-009 — Cross-join guard (cap protezione OOM)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: events_log (10k) × customers (12) = 120k → guard
- **Asserzione**: errore custom, non OOM
- **Meta**: granularity=smoke · priority=nice · tags=manipola,limits,join

### MAN-010 — CRS reprojection (UTM 32632 ↔ WGS84)
- **Stato**: TODO
- **Riferimento**: `app/core/manipola/transforms/spatial.py:357-379,96,151`
- **Dataset**: geometrie sorgente in EPSG:32632 (da aggiungere al seed)
- **Asserzione**: reproject corretto, distanze preservate
- **Meta**: granularity=smoke · priority=nice · tags=manipola,spatial

### MAN-011 — WKT round-trip per tutti i 7 tipi geometrici
- **Stato**: TODO
- **Riferimento**: `app/core/arricchisci/executor.py:248-249`, `app/shared/geo_utils.py:28`
- **Asserzione**: Tipi: POINT, LINESTRING, POLYGON, MULTIPOINT, MULTILINESTRING, MULTIPOLYGON, GEOMETRYCOLLECTION
  ogni tipo serializzato e re-deserializzato senza loss
- **Meta**: granularity=smoke · priority=nice · tags=manipola,spatial

---

## ENR — Arricchisci

### ENR-001 — Lookup OAuth2 client_credentials
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/auth/oauth2/protected`
- **Asserzione**: token negotiated, riusato per richieste successive
- **Meta**: granularity=smoke · priority=must · tags=arricchisci,oauth2

### ENR-002 — Retry su 429 con Retry-After
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/throttle/strict?limit=5`
- **Asserzione**: backoff rispettato, no flooding
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci,rate-limit

### ENR-003 — Errore 401 → fallback policy
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: canvas marcato fallito, log strutturato
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci

### ENR-004 — Risposta malformed JSON
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/error/malformed_json`
- **Asserzione**: errore di parsing intercettato, no crash
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci,json

### ENR-005 — Timeout request
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/slow/delay/30`
- **Asserzione**: timeout configurato (es. 10s) → errore controllato
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci

### ENR-006 — Empty response handling
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/empty_array`
- **Asserzione**: 0 enrichment, no crash, log info
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci

### ENR-007 — External artifact processor (file-to-file)
- **Stato**: TODO
- **Riferimento**: `app/shared/resources/external_artifact_processor.py:34`, `app/core/arricchisci/api_connection_rest_file.py:430`
- **Dataset**: fake-rest endpoint multipart-in / binary-out (da aggiungere se non c'è)
- **Asserzione**: input file → output file roundtrip via REST
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci

### ENR-008 — OAuth2 password grant
- **Stato**: TODO
- **Riferimento**: `app/shared/connections/oauth.py:48,67-69`
- **Dataset**: fake-rest `/auth/oauth2/token` con `grant_type=password` (da aggiungere)
- **Asserzione**: token ricevuto, response shape standard
- **Meta**: granularity=smoke · priority=nice · tags=arricchisci,oauth2,password

---

## LOAD — Carica

### LOAD-001 — Scrivi tabella su Postgres target
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: schema generato, righe scritte, conteggio match
- **Meta**: granularity=smoke · priority=must · tags=carica,postgres

### LOAD-002 — Scrivi su MySQL (cross-dialect)
- **Stato**: TODO
- **Prereq**: profilo `db`
- **Asserzione**: tipi mappati corretti (TEXT vs VARCHAR ecc.)
- **Meta**: granularity=smoke · priority=nice · tags=carica,mysql

### LOAD-003 — Scrivi su Oracle con auto-promote VARCHAR2(4000)→CLOB
- **Stato**: TODO
- **Prereq**: profilo `db-heavy`
- **Dataset**: stringa > 4000 char in input
- **Asserzione**: colonna creata come CLOB
- **Meta**: granularity=smoke · priority=optional · tags=carica,oracle

### LOAD-004 — Scrivi CSV file di output
- **Stato**: TODO
- **Prereq**: profilo `files`
- **Asserzione**: file scritto su SFTP, content correct
- **Meta**: granularity=smoke · priority=nice · tags=carica,csv

### LOAD-005 — Scrivi REST sink POST batch
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/sink/batch`
- **Asserzione**: batch_records_key idempotente, 202 ricevuto
- **Meta**: granularity=smoke · priority=nice · tags=carica,rest

### LOAD-006 — Scrivi REST sink PUT/PATCH per id
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/data/sink/by_id/{id}`
- **Asserzione**: update richiamato 1× per record
- **Meta**: granularity=smoke · priority=nice · tags=carica,rest

### LOAD-007 — Scrivi su S3/MinIO (object storage)
- **Stato**: TODO
- **Prereq**: profilo `cloud`
- **Asserzione**: object presente, ETag verificato
- **Meta**: granularity=smoke · priority=nice · tags=carica,s3

### LOAD-008 — Truncate-insert via DBMS_REDEFINITION (Oracle)
- **Stato**: TODO
- **Prereq**: profilo `db-heavy`
- **Asserzione**: privilegio EXECUTE, redef table strategy
- **Meta**: granularity=smoke · priority=optional · tags=carica,oracle

### LOAD-009 — Storage policy hybrid: spillover oltre soglia
- **Stato**: TODO
- **Prereq**: profilo `cloud`
- **Dataset**: result > `RESULT_DB_HARD_CAP_BYTES`
- **Asserzione**: payload finisce su minio, non su DB
- **Meta**: granularity=smoke · priority=nice · tags=carica

### LOAD-010 — Oracle CLOB auto-promote per stringhe > 4000
- **Stato**: TODO
- **Riferimento**: `app/core/carica/ddl_generator.py:296-305`
- **Dataset**: input con stringa 5000 caratteri
- **Asserzione**: colonna creata come CLOB, valore preservato
- **Meta**: granularity=smoke · priority=optional · tags=carica,oracle

### LOAD-011 — MySQL 8.0 SRID syntax `/*!80003 SRID 4326 */`
- **Stato**: TODO
- **Riferimento**: `app/core/carica/ddl_generator.py:253`
- **Asserzione**: DDL generato include comment SRID, SELECT geometry retorna SRID atteso
- **Meta**: granularity=smoke · priority=optional · tags=carica,mysql,spatial

### LOAD-012 — Oracle truncate-insert via DBMS_REDEFINITION
- **Stato**: TODO
- **Riferimento**: `app/core/carica/writer_oracle_redef.py`
- **Asserzione**: redef completata, righe sostituite atomicamente
- **Meta**: granularity=smoke · priority=optional · tags=carica,oracle

### LOAD-013 — PostgreSQL JSONB roundtrip
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/node_snapshots_db.py`, `app/core/files/db.py`
- **Asserzione**: dict Python → `::jsonb` cast nell'INSERT, deserialize automatico in lettura
- **Meta**: granularity=smoke · priority=nice · tags=carica,postgres

### LOAD-014 — Auto-mapping column-type dispatch + `suggest_match_columns`
- **Stato**: TODO
- **Riferimento**: `app/core/carica/auto_mapping.py`, `app/core/carica/match_suggester.py`
- **Passi**: 1) source con colonne `customer_id BIGINT, email VARCHAR(255), created_at TIMESTAMP, geom GEOMETRY` 2) `POST /carica/auto-map` con target table 3) `POST /carica/suggest-match-columns`
- **Asserzione**: auto-map dispatcha al type-handler corretto (BIGINT→bigint, VARCHAR→varchar(N), TIMESTAMP→timestamptz, GEOMETRY→geometry(SRID)); `suggest_match_columns` ritorna lista candidati con score numerico (0.0–1.0), top match = colonne con stesso nome+type; almeno una match con score >= 0.9
- **Meta**: granularity=integration · priority=nice · tags=carica,spatial

### LOAD-015 — Check-conflicts su `source_connection_id`+`source_table`
- **Stato**: TODO
- **Riferimento**: `app/core/carica/conflict_checker.py`
- **Passi**: 1) crea due nodi carica che scrivono sulla stessa target con stesso `source_connection_id` + `source_table` 2) `POST /carica/check-conflicts`
- **Asserzione**: 200 con `conflicts: [{"source_connection_id": ..., "source_table": ..., "node_ids": [N1, N2], "kind": "duplicate_source"}]`; coppia (source_connection_id, source_table) usata come chiave
- **Meta**: granularity=integration · priority=nice · tags=carica

---

## ARC — ArcGIS

### ARC-001 — generateToken (username/password)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/auth/arcgis/generateToken`
- **Asserzione**: token ottenuto, riusato
- **Meta**: granularity=smoke · priority=nice · tags=arcgis,auth,password

### ARC-002 — OAuth2 client_credentials per ArcGIS
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Dataset**: `fake-rest:8080/sharing/rest/oauth2/token`
- **Asserzione**: ARCGIS_OAUTH_TOKEN ottenuto
- **Meta**: granularity=smoke · priority=nice · tags=arcgis,oauth2

### ARC-003 — createService + applyEdits
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: service creato, features added/updated/deleted
- **Meta**: granularity=smoke · priority=nice · tags=arcgis

### ARC-004 — Query FeatureServer con returnCountOnly
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: count = 5
- **Meta**: granularity=smoke · priority=nice · tags=arcgis

### ARC-005 — Token invalido → 200 + body error (pattern ArcGIS)
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: plenora rileva error code 498 nel body, non lo confonde con 200 success
- **Meta**: granularity=smoke · priority=nice · tags=arcgis,auth

### ARC-006 — OAuth2 client_credentials ArcGIS (oauth2/token)
- **Stato**: TODO
- **Riferimento**: `app/core/features/arcgis_auth.py:267,273`
- **Dataset**: `fake-rest:8080/sharing/rest/oauth2/token`
- **Asserzione**: token ricevuto, riusato per richieste protette
- **Meta**: granularity=smoke · priority=nice · tags=arcgis,oauth2,auth

### ARC-007 — createFolder / isServiceNameAvailable / findServices
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/arcgis/service_discovery.py:404,461,503`
- **Asserzione**: workflow di discovery completo
- **Meta**: granularity=smoke · priority=nice · tags=arcgis

### ARC-008 — IFC bundle in IMDF loader
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/loader.py:173-174`
- **Asserzione**: `.ifc` accettato e processato
- **Meta**: granularity=smoke · priority=nice · tags=arcgis,imdf

---

## SCH — Scheduler

### SCH-001 — Cron schedule esegue canvas
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: run inserito in log_runs, status `success`
- **Meta**: granularity=smoke · priority=must · tags=scheduler

### SCH-002 — Manual trigger via API
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: run on-demand parte, completa
- **Meta**: granularity=smoke · priority=nice · tags=scheduler

### SCH-003 — Schedule disabilitato non parte
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: nessun run inserito
- **Meta**: granularity=smoke · priority=nice · tags=scheduler

### SCH-004 — Beat scheduler leader election (2 worker → 1 lock)
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py`, `app/core/scheduler/scheduler_lock.py`
- **Passi**: 2 instanze beat in concorrenza
- **Asserzione**: solo una acquisisce il lock; l'altra attende
- **Meta**: granularity=smoke · priority=nice · tags=scheduler,celery

### SCH-005 — Cron expression invalida → 422
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/router.py:158`
- **Asserzione**: validation error con detail su croniter
- **Meta**: granularity=smoke · priority=nice · tags=scheduler

### SCH-006 — Download per-schedule scoping (autorizzazione strict)
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/router.py`
- **Passi**: 1) schedule X di gruppo A produce result file 2) utente di gruppo B chiama `GET /schedules/{X}/download/{run_id}` 3) utente di gruppo A chiama lo stesso endpoint
- **Asserzione**: utente B → 404 (no leak esistenza); utente A → 200 con `Content-Disposition: attachment` e payload corretto; audit_log entry per accesso B `denied`
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### SCH-007 — Run-now status flow (queued → running → success)
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/router.py`
- **Passi**: 1) `POST /schedules/{id}/run-now` 2) poll `GET /schedules/{id}/runs/latest` ogni 500ms
- **Asserzione**: prima risposta del poll ritorna `status=queued`; transition osservata a `running`; transition finale a `success` entro N secondi; ogni transition ha `updated_at` monotono crescente
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### SCH-008 — Toggle schedule orphan → 400
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/router.py`
- **Passi**: 1) crea schedule 2) elimina canvas/project di riferimento via DB diretto (orphan state) 3) `PATCH /schedules/{id}/toggle` con `enabled=true`
- **Asserzione**: 400 con `error_code=schedule_orphan`; messaggio include `canvas_id` o `project_id` mancante; schedule `enabled` invariato
- **Meta**: granularity=integration · priority=nice · tags=scheduler

---

## AUTH — Auth & lifecycle

### AUTH-001 — Group cleanup cascade su delete utente
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: schedules, projects, datasources, connections, file_sources tutti rimossi
- **Meta**: granularity=smoke · priority=must · tags=auth,cleanup

### AUTH-002 — MFA TOTP enrollment
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: secret generato, code valida apre sessione
- **Meta**: granularity=smoke · priority=nice · tags=auth,mfa

### AUTH-003 — Login con password sbagliata → rate limit
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: dopo N tentativi, 429
- **Meta**: granularity=smoke · priority=nice · tags=auth,rate-limit,password

### AUTH-004 — JWT scaduto → 401
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Asserzione**: re-login richiesto
- **Meta**: granularity=smoke · priority=must · tags=auth

---

## INF — Infra / smoke

### INF-001 — `docker compose up -d` healthcheck OK
- **Stato**: TODO
- **Prereq**: profilo `full`
- **Asserzione**: tutti i container `(healthy)` entro 3 min
- **Meta**: granularity=smoke · priority=must · tags=infra

### INF-002 — Init seed eseguito una sola volta (idempotenza)
- **Stato**: TODO
- **Prereq**: profilo `db`
- **Asserzione**: secondo `up` non duplica righe
- **Meta**: granularity=smoke · priority=must · tags=infra,idempotency

### INF-003 — `down -v` + `up` ricarica seed
- **Stato**: TODO
- **Prereq**: profilo `db`
- **Asserzione**: dataset rigenerato
- **Meta**: granularity=smoke · priority=must · tags=infra

### INF-004 — Network plenora-e2e: container raggiungibili tra loro
- **Stato**: TODO
- **Prereq**: profilo `full`
- **Asserzione**: fake-rest può chiamare postgres? (no, ma plenora app sì)
- **Meta**: granularity=smoke · priority=must · tags=infra

### INF-005 — `/health` process-only sempre 200
- **Stato**: TODO
- **Riferimento**: `app/main.py`
- **Passi**: `GET /health` con DB stoppato, redis stoppato
- **Asserzione**: 200 con body `{"status": "ok"}`; nessuna check di dipendenza esterna; tempo risposta < 50ms
- **Meta**: granularity=smoke · priority=must · tags=infra,health

### INF-006 — `/ready` 8 check distinti, fail-OPEN vs fail-CLOSED
- **Stato**: TODO
- **Riferimento**: `app/main.py`
- **Passi**: `GET /ready` in stato healthy, poi con DB down, poi con redis down
- **Asserzione**: response include 8 check `{db, redis, celery_broker, celery_workers, object_storage, scheduler_beat, migrations, encryption_key}`; check critici (db, migrations) fail-CLOSED → 503; check non-critici (redis, scheduler_beat) fail-OPEN → 200 con `warnings`
- **Meta**: granularity=smoke · priority=must · tags=infra,health

### INF-007 — `/ready` DB down → 503
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: stop postgres; `GET /ready`
- **Asserzione**: 503 con `checks.db = {ok: false, error: ...}`; altri check riportati anche se non critici
- **Meta**: granularity=smoke · priority=must · tags=infra,health

### INF-008 — `/ready` migrations check (alembic head)
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: rollback `alembic downgrade -1`; `GET /ready`
- **Asserzione**: 503 con `checks.migrations = {ok: false, current: <rev-1>, expected: <head>}`
- **Meta**: granularity=smoke · priority=nice · tags=infra,health,migration

### INF-009 — `/ready` redis down → 200 con warning (fail-OPEN)
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: stop redis; `GET /ready`
- **Asserzione**: 200; `checks.redis = {ok: false}`; `warnings` array non vuoto include `redis`; app risponde su altri endpoint con fallback in-memory
- **Meta**: granularity=smoke · priority=nice · tags=infra,redis,health

### INF-010 — `/ready` celery_workers down → 200 con warning
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: stop tutti i celery worker; `GET /ready`
- **Asserzione**: 200 con `checks.celery_workers = {ok: false, count: 0}`; warning emesso
- **Meta**: granularity=smoke · priority=nice · tags=infra,celery,health

### INF-011 — `/ready` scheduler_beat down → 200 con warning
- **Stato**: TODO
- **Riferimento**: `INF-006, BEAT-006`
- **Passi**: stop beat; `GET /ready`
- **Asserzione**: 200 con `checks.scheduler_beat = {ok: false}`; warning include `beat heartbeat stale`
- **Meta**: granularity=smoke · priority=nice · tags=infra,scheduler,health

### INF-012 — `/ready` object_storage down → 200 con warning
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: stop minio; `GET /ready`
- **Asserzione**: 200 con `checks.object_storage = {ok: false}`; warning emesso
- **Meta**: granularity=smoke · priority=nice · tags=infra,health

### INF-013 — `/ready` encryption_key missing → 503 (fail-CLOSED critico)
- **Stato**: TODO
- **Riferimento**: `INF-006`
- **Passi**: avvio app senza `PLENORA_ENCRYPTION_KEY` o invalida; `GET /ready`
- **Asserzione**: 503 con `checks.encryption_key = {ok: false}`; app boot rifiutato o ready negato
- **Meta**: granularity=smoke · priority=must · tags=infra,health

### INF-014 — Bootstrap order: import `from app.core.*` segue `_bootstrap()`
- **Stato**: TODO
- **Riferimento**: `app/main.py:_bootstrap`, `app/core/__init__.py`
- **Passi**: ispezionare ordine import in `app/main.py` con AST; verifica che chiamata `_bootstrap()` precede ogni `from app.core.* import ...` non lazy
- **Asserzione**: AST analysis conferma `_bootstrap()` è invocato prima dell'import di moduli core; runtime: log al boot mostra `bootstrap_complete` precede log `core_module_loaded`
- **Meta**: granularity=smoke · priority=nice · tags=infra

### INF-015 — Lifespan teardown chiude PG pool (idempotente multi-run)
- **Stato**: TODO
- **Riferimento**: `app/main.py`
- **Passi**: 1) avvio app, conta connessioni in `pg_stat_activity` 2) shutdown graceful (SIGTERM) 3) ripeti boot+shutdown 3 volte
- **Asserzione**: dopo ogni shutdown, connessioni del pool plenora=0; nessun warning `pool not closed`; teardown idempotente (secondo close non solleva)
- **Meta**: granularity=integration · priority=nice · tags=infra,idempotency

---

## SEC — Security gates

### SEC-001 — AntiSSRF: URL privato bloccato
- **Stato**: TODO
- **Prereq**: PREP-002
- **Riferimento**: `app/core/security/outbound_url.py:34-88`
- **Passi**: crea api_connection con `base_url=http://127.0.0.1/`, `http://169.254.169.254/`, `http://10.0.0.1/`, `http://[::1]/`
- **Asserzione**: ognuna rifiutata con errore `outbound_url_blocked` o equivalente
- **Meta**: granularity=smoke · priority=must · tags=security

### SEC-002 — AntiSSRF: DNS rebinding (host pubblico → IP privato)
- **Stato**: TODO
- **Prereq**: PREP-002
- **Asserzione**: risoluzione DNS verificata prima della richiesta; se risolve a IP privato → blocco
- **Meta**: granularity=smoke · priority=nice · tags=security

### SEC-003 — Login rate limit
- **Stato**: TODO
- **Prereq**: PREP-001
- **Riferimento**: `app/shared/login_rate_limiter.py`
- **Passi**: 10× `POST /auth/login` con password sbagliata
- **Asserzione**: dopo soglia → 429 con header `Retry-After`
- **Meta**: granularity=smoke · priority=nice · tags=security,rate-limit

### SEC-004 — Login rate limit fallback in-memory (Redis OFF)
- **Stato**: TODO
- **Riferimento**: `app/core/auth/rate_limit_fallback.py:39`
- **Asserzione**: rate-limit comunque applicato via in-memory backend
- **Meta**: granularity=smoke · priority=nice · tags=security,rate-limit,redis

### SEC-005 — Token blacklist post-logout
- **Stato**: TODO
- **Prereq**: PREP-002
- **Riferimento**: `app/shared/token_blacklist.py`
- **Passi**: 1) login → JWT 2) logout 3) richiesta con JWT vecchio
- **Asserzione**: 401 (token blacklisted)
- **Meta**: granularity=integration · priority=must · tags=security,auth

### SEC-006 — Secret redaction in audit log + error response
- **Stato**: META
- **Riferimento**: `app/shared/error_sanitizer.py`, `test-e2e/runner/cross_cutting.py`
- **Asserzione**: password / api_key / secret mai presenti in log o response body.
  [META / cross-cutting-policy] Sostituito da
  `assert_no_pii_in_response` (autouse): verifica pattern
  `password|api_key|client_secret|access_token|...` su tutte le
  response (incl. 4xx/5xx) e accetta solo valori redacted. Per i
  log container usare `assert_no_pii_in_logs`.
- **Meta**: granularity=smoke · priority=meta · tags=security,audit,cross-cutting

### SEC-007 — Cross-group authorization
- **Stato**: TODO
- **Asserzione**: utente di group A non legge/scrive risorse di group B
- **Meta**: granularity=smoke · priority=nice · tags=security,tenancy

---

## SSRF — ssrf

### SSRF-001 — API connections test `_validate_external_url` blocca AWS metadata IP
- **Stato**: TODO
- **Riferimento**: `app/core/api_connections/router.py:_validate_external_url`, `app/core/security/outbound_url.py`
- **Passi**: `POST /api_connections/test` con `base_url=http://169.254.169.254/latest/meta-data/`
- **Asserzione**: 422 con `error_code=outbound_url_blocked`; richiesta HTTP NON inviata (mocked egress assente da log)
- **Meta**: granularity=smoke · priority=must · tags=security,ssrf

### SSRF-002 — `_validate_external_url` blocca localhost/loopback varianti
- **Stato**: TODO
- **Riferimento**: `app/core/api_connections/router.py:_validate_external_url`
- **Passi**: per ognuno di `http://localhost`, `http://127.0.0.1`, `http://[::1]`, `http://0.0.0.0` chiamare `POST /api_connections/test`
- **Asserzione**: tutte 4 → 422 con `error_code=outbound_url_blocked`; nessuna connessione tentata
- **Meta**: granularity=smoke · priority=must · tags=security,ssrf

### SSRF-003 — `CredentialUnavailableError` durante validate-external-url → 422
- **Stato**: TODO
- **Riferimento**: `app/core/api_connections/router.py:_validate_external_url`
- **Passi**: `POST /api_connections/test` per cloud secret backend con credenziali mancanti (es. SSM con AWS creds vuote)
- **Asserzione**: 422 (NON 500) con `error_code=cloud_credentials_unavailable`; messaggio non contiene stack trace; audit log registra il tentativo
- **Meta**: granularity=smoke · priority=must · tags=security,ssrf

---

## SP — SharePoint *(richiede estensione fake-rest)*

### SP-001 — SharePoint OK: list files in folder
- **Stato**: TODO
- **Riferimento**: `app/core/datasources/sharepoint.py:25-65`
- **Dataset**: fake-rest `/sharepoint/_api/web/lists/getbytitle/` (da aggiungere)
- **Asserzione**: 200, lista file con metadata
- **Meta**: granularity=smoke · priority=nice · tags=sharepoint

### SP-002 — SharePoint AADSTS50034 (utente non esiste)
- **Stato**: TODO
- **Asserzione**: 401 con body `{"error": "invalid_grant", "error_description": "AADSTS50034..."}`
- **Meta**: granularity=smoke · priority=optional · tags=sharepoint,locale

### SP-003 — SharePoint AADSTS50053 (account locked)
- **Stato**: TODO
- **Asserzione**: 401 + AADSTS50053; plenora gestisce e marca canvas failed
- **Meta**: granularity=smoke · priority=optional · tags=sharepoint,locale

### SP-004 — SharePoint AADSTS900023 (invalid_client)
- **Stato**: TODO
- **Asserzione**: 401 + invalid_client; secret rotation suggerita nel log
- **Meta**: granularity=smoke · priority=optional · tags=sharepoint,locale

### SP-005 — SharePoint throttling 429
- **Stato**: TODO
- **Asserzione**: 429 con `Retry-After`; plenora fa backoff
- **Meta**: granularity=smoke · priority=optional · tags=sharepoint,rate-limit

### SP-006 — SharePoint 5xx transient → retry
- **Stato**: TODO
- **Asserzione**: dopo retry positivo, run completa
- **Meta**: granularity=smoke · priority=optional · tags=sharepoint

---

## SFTP — SFTP key-based auth *(richiede modifica container)*

### SFTP-001 — Auth con private key (no passphrase)
- **Stato**: TODO
- **Riferimento**: `app/core/datasources/sftp.py:83-95`
- **Dataset**: `test-e2e/docker/sftp/keys/id_rsa_test`
- **Asserzione**: connessione stabilita, file letto
- **Meta**: granularity=smoke · priority=nice · tags=sftp

### SFTP-002 — Auth con private key + passphrase
- **Stato**: TODO
- **Asserzione**: passphrase sbloccata correttamente
- **Meta**: granularity=smoke · priority=optional · tags=sftp

### SFTP-003 — Auth fallisce: chiave non autorizzata
- **Stato**: TODO
- **Asserzione**: errore controllato, log strutturato
- **Meta**: granularity=smoke · priority=optional · tags=sftp

### SFTP-004 — Host key verification (RejectPolicy)
- **Stato**: TODO
- **Riferimento**: `paramiko.RejectPolicy`
- **Asserzione**: known_hosts mismatch → connessione rifiutata
- **Meta**: granularity=smoke · priority=optional · tags=sftp

### SFTP-005 — Host key verification (AutoAddPolicy disabilitata in prod)
- **Stato**: TODO
- **Asserzione**: configurazione produzione rifiuta nuovi host
- **Meta**: granularity=smoke · priority=optional · tags=sftp

---

## CLOUD — Cloud secrets / SSM *(richiede LocalStack)*

### CLOUD-001 — Get secrets from SSM (LocalStack)
- **Stato**: TODO
- **Riferimento**: `app/infrastructure/storage/cloud_backends.py:171-198`
- **Asserzione**: secret recuperato, decrittato
- **Meta**: granularity=smoke · priority=nice · tags=cloud-secrets

### CLOUD-002 — SSM credenziali expired (`AWS_CREDENTIALS_EXPIRED`)
- **Stato**: TODO
- **Asserzione**: errore mappato correttamente
- **Meta**: granularity=smoke · priority=optional · tags=cloud-secrets

### CLOUD-003 — SSM CRT missing (`AWS_CRT_MISSING`)
- **Stato**: TODO
- **Asserzione**: warning + fallback documentato
- **Meta**: granularity=smoke · priority=optional · tags=cloud-secrets

### CLOUD-004 — Bucket multipli: BACKUP_BUCKET ≠ LOG_ARCHIVE_BUCKET
- **Stato**: TODO
- **Riferimento**: `cloud_backends.py:929-939`
- **Asserzione**: scritture su bucket dedicato, IAM-isolation rispettata
- **Meta**: granularity=smoke · priority=optional · tags=cloud-secrets

---

## QSTR — Query streaming

### QSTR-001 — Streaming `fetchmany(CHUNK_SIZE)` su result grande
- **Stato**: TODO
- **Riferimento**: `app/db/query_streaming.py:35-110`
- **Dataset**: events_log 10000 righe
- **Asserzione**: memoria stabile, no OOM, tutte le righe processate
- **Meta**: granularity=smoke · priority=nice · tags=streaming

### QSTR-002 — Cap MAX_STREAMING_ROWS rispettato
- **Stato**: TODO
- **Dataset**: query che eccede il cap
- **Asserzione**: errore controllato, status 413 o equivalente
- **Meta**: granularity=smoke · priority=nice · tags=streaming,limits

---

## STG — Storage reconciler

### STG-001 — Reconcile object storage vs DB lifecycle
- **Stato**: TODO
- **Riferimento**: `app/config.py:280-328`
- **Passi**: 1) crea result con object su MinIO 2) elimina record DB 3) trigger reconcile
- **Asserzione**: object orfano rimosso da MinIO
- **Meta**: granularity=integration · priority=nice · tags=reconciler

### STG-002 — Reconcile retry su transient error
- **Stato**: TODO
- **Asserzione**: retry policy applicata, no reconcile-storm
- **Meta**: granularity=smoke · priority=nice · tags=reconciler

---

## MFA — Multi-factor *(estensione)*

### MFA-001 — Enrollment: QR code PNG generato
- **Stato**: TODO
- **Riferimento**: `app/core/auth/mfa.py:import qrcode`
- **Passi**: `POST /auth/mfa/enroll`
- **Asserzione**: response include PNG base64 ben formato (parse-back con PIL → 200x200+)
- **Meta**: granularity=smoke · priority=nice · tags=mfa

### MFA-002 — TOTP verify entro 30s window
- **Stato**: TODO
- **Asserzione**: code valido apre sessione
- **Meta**: granularity=smoke · priority=optional · tags=mfa

### MFA-003 — TOTP code scaduto → 401
- **Stato**: TODO
- **Asserzione**: errore controllato, contatore tentativi incrementa
- **Meta**: granularity=smoke · priority=optional · tags=mfa

---

## PWD — Password hashing

### PWD-001 — Login utente con hash bcrypt
- **Stato**: TODO
- **Riferimento**: `app/shared/password_hashing.py`
- **Dataset**: utente seed con `password_hash` bcrypt
- **Asserzione**: 200, sessione aperta
- **Meta**: granularity=smoke · priority=nice · tags=password

### PWD-002 — Login utente con hash argon2
- **Stato**: TODO
- **Dataset**: utente seed con `password_hash` argon2
- **Asserzione**: 200, sessione aperta
- **Meta**: granularity=smoke · priority=nice · tags=password

### PWD-003 — Migration bcrypt → argon2 al primo login post-upgrade
- **Stato**: TODO
- **Asserzione**: hash riscritto come argon2, vecchio bcrypt invalidato
- **Meta**: granularity=smoke · priority=nice · tags=password,migration

---

## AUDIT — Audit log

### AUDIT-001 — `@audit_action` decorator scrive entry
- **Stato**: META
- **Riferimento**: `app/shared/audit_action.py`, `test-e2e/runner/cross_cutting.py`
- **Passi**: trigger azione sensibile (es. delete connection)
- **Asserzione**: row in `audit_log` con actor, action, resource, timestamp.
  [META / cross-cutting-policy] Sostituito da `assert_audit_logged`
  in `runner/cross_cutting.py`, chiamato da ogni test write
  (PREP-004..PREP-021, ADM-*, IDEM-*). Non eseguito come test
  pytest dedicato: l'asserzione e' esercitata implicitamente da
  tutti i test che fanno azioni audit-able.
- **Meta**: granularity=smoke · priority=meta · tags=audit,cross-cutting

### AUDIT-002 — Retention `AUDIT_RETENTION_DAYS=365`
- **Stato**: TODO
- **Asserzione**: entry > 365gg eliminate dal task di pulizia
- **Meta**: granularity=smoke · priority=nice · tags=audit,retention

### AUDIT-003 — Cron lock acquisito da una sola istanza beat
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/scheduler_lock.py`
- **Asserzione**: 2 worker concorrenti → 1 acquisisce, 1 attende
- **Meta**: granularity=smoke · priority=nice · tags=audit,scheduler

---

## ADM — Admin / backup

### ADM-001 — Trigger backup `pg_dump`
- **Stato**: TODO
- **Riferimento**: `scripts/backup_db.py`, `app/core/admin_backups/service.py:380`
- **Asserzione**: dump generato, dimensione > 0, restore-test su DB sandbox
- **Meta**: granularity=smoke · priority=nice · tags=admin,backup

### ADM-002 — Lista backup su bucket dedicato `BACKUP_BUCKET`
- **Stato**: TODO
- **Prereq**: profilo `cloud`
- **Riferimento**: `app/core/admin_backups/service.py:76-86`
- **Asserzione**: list paginato, metadata size+modified
- **Meta**: granularity=smoke · priority=nice · tags=admin,backup

### ADM-003 — Log archive su `LOG_ARCHIVE_BUCKET` (gzip)
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/log_archive.py:272,350`
- **Asserzione**: dopo retention → log gzippato in bucket dedicato
- **Meta**: granularity=smoke · priority=nice · tags=admin,retention

### ADM-004 — `/api/projects/admin/cleanup-orphans` superadmin, dry_run default, cross-DB
- **Stato**: TODO
- **Riferimento**: `app/core/projects/admin_router.py`
- **Passi**: 1) admin (non super) `POST /api/projects/admin/cleanup-orphans` → 403 2) superadmin senza query → 200 con `{"dry_run": true, ...}` 3) superadmin con `?dry_run=false` → 200 con report rimozione cross-DB (postgres + minio)
- **Asserzione**: dry_run=true di default (no DELETE eseguito); dry_run=false elimina e response include `removed_counts` per ogni tabella + `removed_objects` per minio; admin non-super → 403 `error_code=insufficient_role`
- **Meta**: granularity=integration · priority=nice · tags=admin,rbac,cleanup

---

## CONC — Concorrenza / race conditions

### CONC-001 — Due utenti POST canvas con stesso name/group → 1 success + 1 conflict
- **Stato**: TODO
- **Prereq**: PREP-018
- **Riferimento**: `app/core/canvases/router.py`
- **Passi**: 1) due richieste `POST /projects/{id}/canvases` simultanee con `name=race-canvas` 2) raccogli risposte
- **Asserzione**: una risposta 201 con `canvas_id`, una 409 conflict; in DB esiste 1 sola riga
- **Meta**: granularity=integration · priority=optional · tags=concurrency

### CONC-002 — Due update simultanei stesso canvas → optimistic lock version mismatch
- **Stato**: TODO
- **Prereq**: PREP-019
- **Passi**: 1) GET canvas → version=N 2) due `PUT /canvases/{id}` con if-match version=N
- **Asserzione**: prima PUT 200 + version=N+1, seconda PUT 409 con `error_code=version_mismatch`
- **Meta**: granularity=integration · priority=optional · tags=concurrency

### CONC-003 — Due run on-demand stesso canvas con stessa Idempotency-Key → 1 run creato
- **Stato**: TODO
- **Prereq**: PREP-020
- **Riferimento**: `API-011 (idempotency baseline`
- **Passi**: 2× `POST /canvases/{id}/runs` con header `Idempotency-Key: race-key-001` in parallelo
- **Asserzione**: entrambe le risposte ritornano stesso `run_id`; in `log_runs` esiste 1 sola entry
- **Meta**: granularity=smoke · priority=optional · tags=concurrency,idempotency

### CONC-004 — Lock cleanup quando worker celery crash a metà run
- **Stato**: TODO
- **Prereq**: PREP-021
- **Riferimento**: `app/core/scheduler/scheduler_lock.py`
- **Passi**: 1) avvia run lungo 2) `docker kill` worker celery durante esecuzione 3) attendi TTL
- **Asserzione**: lock rilasciato dopo TTL, run marcato `failed` con `error_code=worker_crashed`, no riga "running" stale
- **Meta**: granularity=integration · priority=optional · tags=concurrency,celery,cleanup

### CONC-005 — Lock TTL scaduto rilasciato e riacquisibile
- **Stato**: TODO
- **Prereq**: PREP-002
- **Riferimento**: `app/core/scheduler/scheduler_lock.py`
- **Passi**: 1) acquisisci lock con TTL=2s 2) attendi 3s 3) un secondo processo tenta acquire
- **Asserzione**: secondo acquire OK; primo holder al successivo refresh riceve `lock_lost`
- **Meta**: granularity=integration · priority=optional · tags=concurrency

### CONC-006 — Schedule cron tick sovrapposto: prevenzione doppia esecuzione
- **Stato**: TODO
- **Prereq**: SCH-004
- **Riferimento**: `workers/beat_scheduler.py`
- **Passi**: schedule con run lungo 2× tick interval
- **Asserzione**: secondo tick rileva run in corso, salta con log `skipped_overlapping_run`
- **Meta**: granularity=smoke · priority=optional · tags=concurrency,scheduler

---

## RES — Resilienza / partial failure

### RES-001 — DB target offline durante carica → run failed, no orphan rows
- **Stato**: TODO
- **Prereq**: LOAD-001
- **Passi**: 1) avvia canvas con load Postgres 2) `docker stop` postgres target a metà 3) attendi run end
- **Asserzione**: run `status=failed`, `error_code=db_unavailable`, conteggio righe target = 0 (rollback completo)
- **Meta**: granularity=integration · priority=nice · tags=resilience

### RES-002 — REST endpoint timeout in arricchisci → retry policy applicata
- **Stato**: TODO
- **Prereq**: ENR-005
- **Riferimento**: `app/core/arricchisci/executor.py`
- **Dataset**: `fake-rest:8080/slow/delay/30` con timeout=10s, retry=3
- **Asserzione**: 3 tentativi loggati con backoff esponenziale, run marcato `failed` con `error_code=timeout`
- **Meta**: granularity=smoke · priority=nice · tags=resilience,rest

### RES-003 — Worker celery killed a metà run → cleanup e marcatura failed
- **Stato**: TODO
- **Prereq**: PREP-021
- **Riferimento**: `app/core/scheduler/scheduler_lock.py`
- **Passi**: 1) start run 2) `docker kill -SIGKILL` celery worker 3) attendi watchdog
- **Asserzione**: dopo TTL run `status=failed`, intermediate snapshot puliti, no lock orfani
- **Meta**: granularity=integration · priority=nice · tags=resilience,celery,cleanup

### RES-004 — Scheduler beat down per 1h → recovery, run schedulati ancora attivi
- **Stato**: TODO
- **Prereq**: SCH-001
- **Passi**: 1) crea schedule cron `*/5 * * * *` 2) stop beat 1h 3) restart beat
- **Asserzione**: schedule riprende ai tick successivi, run mancati documentati come `skipped` (no flood backfill)
- **Meta**: granularity=integration · priority=nice · tags=resilience,scheduler

### RES-005 — DB connection pool esaurito → 503 graceful
- **Stato**: TODO
- **Prereq**: PREP-002
- **Riferimento**: `app/db/`
- **Passi**: saturare pool con N richieste lunghe (N = pool_size + overflow + 1)
- **Asserzione**: ultima richiesta 503 con `error_code=pool_exhausted`, no traceback nudo, app rimane viva
- **Meta**: granularity=smoke · priority=nice · tags=resilience

### RES-006 — Storage policy hybrid: minio offline → fallback DB
- **Stato**: TODO
- **Prereq**: LOAD-009
- **Riferimento**: `app/infrastructure/storage/`
- **Passi**: 1) policy `hybrid` 2) `docker stop` minio 3) esegui canvas con result < hard_cap
- **Asserzione**: result salvato su DB, log `warning=object_storage_unavailable_fallback_db`, run success
- **Meta**: granularity=integration · priority=nice · tags=resilience,s3

---

## INT — Data integrity

### INT-001 — INSERT con FK violation → 422, rollback
- **Stato**: TODO
- **Prereq**: PREP-004
- **Passi**: tentare INSERT in `customers` con `city_id` non esistente
- **Asserzione**: 422 `error_code=foreign_key_violation`, count customers invariato
- **Meta**: granularity=smoke · priority=nice · tags=integrity

### INT-002 — UPDATE con CHECK constraint violation → errore mappato
- **Stato**: TODO
- **Prereq**: PREP-004
- **Dataset**: `orders.total_amount > 0` come CHECK
- **Asserzione**: UPDATE con total_amount=-10 → 422 `error_code=check_constraint_violation`, riga invariata
- **Meta**: granularity=smoke · priority=nice · tags=integrity

### INT-003 — DELETE cascade rispetta FK su catena 14 tabelle
- **Stato**: TODO
- **Prereq**: PREP-023
- **Riferimento**: `delete_users_for_group`
- **Asserzione**: dopo cascade, conteggio = 0 in tutte le 14 tabelle child documentate; no FK orphan rilevato da query di sanità
- **Meta**: granularity=smoke · priority=nice · tags=integrity,cleanup

### INT-004 — Transaction rollback su errore parziale (carica fallisce metà → 0 righe scritte)
- **Stato**: TODO
- **Prereq**: LOAD-001
- **Passi**: 1) inject errore al record N/2 (es. constraint violation) 2) attendi run
- **Asserzione**: target table count = 0 (no commit parziale), run `status=failed`
- **Meta**: granularity=integration · priority=nice · tags=integrity

### INT-005 — UNIQUE constraint su email customers preservato post-NULL row
- **Stato**: TODO
- **Prereq**: PREP-004
- **Dataset**: customer id=22 con email NULL
- **Asserzione**: INSERT secondo customer email=NULL OK (NULL non viola UNIQUE in postgres); INSERT con email duplicata non-NULL → 409
- **Meta**: granularity=smoke · priority=nice · tags=integrity

### INT-006 — Decimal precision preservata round-trip (no float drift)
- **Stato**: TODO
- **Prereq**: EXT-002
- **Dataset**: `orders.total_amount` con valori `9999.99`, `0.01`, `123.456`
- **Asserzione**: estrai → carica → estrai stesso valore byte-per-byte, no `9999.989999...`
- **Meta**: granularity=smoke · priority=nice · tags=integrity

---

## BCK — Backup / restore

### BCK-001 — Trigger backup → file gzip su BACKUP_BUCKET, dimensione > 0
- **Stato**: TODO
- **Prereq**: ADM-001
- **Riferimento**: `scripts/backup_db.py`, `app/core/admin_backups/service.py`
- **Asserzione**: object presente in `BACKUP_BUCKET`, content-type `application/gzip`, size > 1KB
- **Meta**: granularity=smoke · priority=nice · tags=backup

### BCK-002 — Restore in DB sandbox → schema + dati identici, conteggi match
- **Stato**: TODO
- **Prereq**: BCK-001
- **Passi**: 1) crea DB sandbox 2) `pg_restore` del backup 3) confronta count per ogni tabella
- **Asserzione**: count(tabella) source == count(tabella) sandbox per tutte le tabelle plenora
- **Meta**: granularity=integration · priority=nice · tags=backup,soft-delete

### BCK-003 — Backup parziale per gruppo
- **Stato**: TODO
- **Prereq**: ADM-001
- **Passi**: trigger backup con filtro `group_id=A`
- **Asserzione**: backup contiene solo entità gruppo A; entità gruppo B assenti dal dump
- **Meta**: granularity=smoke · priority=nice · tags=backup

### BCK-004 — Verifica integrità: `pg_restore --list` su backup
- **Stato**: TODO
- **Prereq**: BCK-001
- **Passi**: `pg_restore --list backup.gz`
- **Asserzione**: TOC parsing OK, no errori, lista oggetti = N atteso
- **Meta**: granularity=smoke · priority=nice · tags=backup,soft-delete

### BCK-005 — Retention: backup vecchi rimossi dopo N giorni
- **Stato**: TODO
- **Prereq**: ADM-001
- **Riferimento**: `app/core/admin_backups/service.py`
- **Passi**: 1) crea backup con `created_at` simulato a -N-1gg 2) trigger retention task
- **Asserzione**: object rimosso dal bucket; entry log `backup_retention_deleted`
- **Meta**: granularity=integration · priority=nice · tags=backup,retention

### BCK-006 — Backup durante write → consistenza preservata (snapshot/MVCC)
- **Stato**: TODO
- **Prereq**: BCK-001
- **Passi**: 1) start lungo INSERT batch 2) trigger backup a metà 3) restore in sandbox
- **Asserzione**: sandbox contiene snapshot consistente (count = valore al momento di start backup), no row parzialmente inserito
- **Meta**: granularity=integration · priority=nice · tags=backup

### BCK-007 — Trigger backup rifiuta se già in corso; `audit_action` richiede `current_user` kwarg
- **Stato**: TODO
- **Riferimento**: `app/core/admin_backups/router.py`, `app/shared/audit_action.py`
- **Passi**: 1) `POST /admin/backups/trigger` 2) immediatamente prima del completamento `POST /admin/backups/trigger` di nuovo 3) inspeziona audit_log
- **Asserzione**: prima 202; seconda 409 con `error_code=backup_already_running`; ogni entry in `audit_log` per backup contiene `actor=<current_user.email>` (kwarg `current_user` propagato dal decorator `audit_action`); chiamata interna senza `current_user` → 500 con messaggio `audit_action requires current_user kwarg`
- **Meta**: granularity=integration · priority=nice · tags=backup,security

---

## TENANT — Multi-tenancy isolation

### TENANT-001 — Gruppo A crea connection, gruppo B non la vede in list
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: 1) user A crea connection 2) user B `GET /connections`
- **Asserzione**: response B non contiene `connection_id` di A, total = solo count B
- **Meta**: granularity=integration · priority=nice · tags=tenancy

### TENANT-002 — Gruppo A non può GET/PUT/DELETE risorsa di gruppo B → 403/404
- **Stato**: TODO
- **Prereq**: TENANT-001
- **Passi**: user A tenta `GET/PUT/DELETE /connections/{id_di_B}`
- **Asserzione**: 404 (tenant-blind: non rivela esistenza) o 403 documentato; risorsa B invariata
- **Meta**: granularity=smoke · priority=optional · tags=tenancy,cleanup

### TENANT-003 — Schema introspection di DB condiviso con permessi distinti per gruppo
- **Stato**: TODO
- **Prereq**: PREP-009
- **Passi**: gruppo A vede schema `plenora`, gruppo B vede solo schema `plenora_b`
- **Asserzione**: list tables filtrato per ruolo DB del gruppo, no leak cross-schema
- **Meta**: granularity=smoke · priority=optional · tags=tenancy,rbac

### TENANT-004 — Risultati di run di gruppo A non leggibili da gruppo B
- **Stato**: TODO
- **Prereq**: PREP-022
- **Passi**: user B `GET /results/{result_id_A}`
- **Asserzione**: 404, `audit_log` registra tentativo con `actor=B, resource=result_A, action=denied`
- **Meta**: granularity=smoke · priority=optional · tags=tenancy

### TENANT-005 — Cleanup gruppo A non tocca dati gruppo B
- **Stato**: TODO
- **Prereq**: PREP-023 + TENANT-001
- **Passi**: 1) popola entrambi i gruppi 2) `DELETE /groups/{A}`
- **Asserzione**: gruppo B count entità invariato per tutte le 14 tabelle child
- **Meta**: granularity=integration · priority=optional · tags=tenancy,cleanup

### TENANT-006 — Quota disco per gruppo: superamento → 413
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: gruppo A con `quota_bytes=10MB`; tentativo upload file 20MB
- **Asserzione**: 413 `error_code=group_quota_exceeded`, file non scritto
- **Meta**: granularity=smoke · priority=optional · tags=tenancy,limits

---

## PERM — Permission matrix

### PERM-001 — Viewer vede progetti gruppo, non li edita
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `GET /projects` OK; `POST/PUT/DELETE` sui progetti
- **Asserzione**: GET 200; mutations 403 con `error_code=insufficient_role`
- **Meta**: granularity=smoke · priority=nice · tags=rbac

### PERM-002 — Editor crea canvas, non gestisce gruppo
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: `POST /canvases` OK; `POST /groups/{id}/users` 403
- **Asserzione**: editor può modificare risorse del gruppo ma non gestire membership
- **Meta**: granularity=smoke · priority=nice · tags=rbac

### PERM-003 — Admin gestisce gruppo, non gestisce sistema
- **Stato**: TODO
- **Prereq**: PREP-003
- **Passi**: admin invita user al gruppo OK; `POST /admin/system/*` 403
- **Asserzione**: admin scope = gruppo, no escalation a system endpoint
- **Meta**: granularity=smoke · priority=nice · tags=rbac

### PERM-004 — SuperAdmin attraversa tutti i gruppi
- **Stato**: TODO
- **Passi**: superadmin `GET /connections` con cross-group flag
- **Asserzione**: response include risorse di tutti i gruppi; audit log entry per accesso cross-group
- **Meta**: granularity=smoke · priority=nice · tags=rbac

### PERM-005 — Role escalation tentata via direct DB write → audit trigger
- **Stato**: TODO
- **Prereq**: PREP-002
- **Passi**: tentare UPDATE diretto su `users.role` via SQL injection o endpoint vulnerabile
- **Asserzione**: tentativo bloccato a livello applicativo; se DB write avviene, trigger inserisce entry `audit_log.action=role_escalation_detected`
- **Meta**: granularity=smoke · priority=nice · tags=rbac,audit

### PERM-006 — Cambio ruolo durante sessione attiva → enforcement immediato
- **Stato**: TODO
- **Prereq**: PERM-001
- **Passi**: 1) viewer ottiene JWT 2) admin promuove viewer→editor 3) viewer riusa stesso JWT per `POST /canvases`
- **Asserzione**: o JWT contiene claim ruolo (allora 403 finché refresh) o enforcement DB-side (allora 200 immediato); comportamento documentato
- **Meta**: granularity=integration · priority=nice · tags=rbac,auth

---

## MIG — Migration / backward compat

### MIG-001 — Canvas salvato in schema v1.0 → loaded e parsed graceful in v1.5
- **Stato**: TODO
- **Prereq**: PREP-019
- **Passi**: insert diretto canvas con `definition.schema_version=1.0` + nodi old-style
- **Asserzione**: GET canvas response include `schema_version=1.5` e nodi migrati on-the-fly, run executes OK
- **Meta**: granularity=smoke · priority=optional · tags=migration

### MIG-002 — Connection con campo deprecato → warning log, no crash
- **Stato**: TODO
- **Prereq**: PREP-004
- **Passi**: insert connection con campo legacy es. `ssl_mode` deprecato
- **Asserzione**: connection caricata, log `warning=deprecated_field_ssl_mode_use_sslmode`, no exception
- **Meta**: granularity=smoke · priority=optional · tags=migration

### MIG-003 — Migration alembic up/down idempotenti (applicato 2× → ok)
- **Stato**: TODO
- **Prereq**: PREP-001
- **Passi**: 1) `alembic upgrade head` 2) `alembic upgrade head` di nuovo 3) `alembic downgrade -1` 4) `alembic upgrade +1`
- **Asserzione**: nessuna step fallisce, schema finale identico a baseline
- **Meta**: granularity=integration · priority=optional · tags=migration,idempotency

### MIG-004 — Field aggiunto in schema canvas: vecchi canvas leggono default
- **Stato**: TODO
- **Prereq**: MIG-001
- **Passi**: aggiungi field `retry_policy` in nuova versione; carica canvas vecchio
- **Asserzione**: GET response include `retry_policy=<default>`, no validation error
- **Meta**: granularity=smoke · priority=optional · tags=migration

### MIG-005 — Field rimosso: vecchi canvas con quel field → ignorato, no error
- **Stato**: TODO
- **Prereq**: MIG-001
- **Passi**: load canvas con campo `legacy_attribute` non più nello schema
- **Asserzione**: campo rimosso silenziosamente da response, log `info=stripped_unknown_field`
- **Meta**: granularity=smoke · priority=optional · tags=migration

---

## PERF — Performance / soglie

### PERF-001 — Estrai 10k events_log → < 30s, peak memoria < 500MB
- **Stato**: TODO
- **Prereq**: EXT-006
- **Riferimento**: `app/db/query_streaming.py`
- **Asserzione**: wall-time < 30s; RSS peak worker < 500MB (misurato via cgroup o `psutil`)
- **Meta**: granularity=smoke · priority=optional · tags=performance

### PERF-002 — Carica batch 50k records via REST sink → throughput > 1000 rec/s
- **Stato**: TODO
- **Prereq**: LOAD-005
- **Asserzione**: tempo totale < 50s; rate logged > 1000 rec/s; no 429 dal sink
- **Meta**: granularity=smoke · priority=optional · tags=performance,rest

### PERF-003 — API GET /canvases con 1000 canvas in gruppo → < 1s con paginazione
- **Stato**: TODO
- **Prereq**: PREP-018
- **Passi**: `GET /canvases?page=1&size=50`
- **Asserzione**: response < 1s p95; payload size sano; query DB usa LIMIT/OFFSET o keyset
- **Meta**: granularity=smoke · priority=optional · tags=performance,pagination

### PERF-004 — Schema introspection su DB con 500 tabelle → < 5s
- **Stato**: TODO
- **Prereq**: PREP-009
- **Asserzione**: `GET /connections/{id}/schemas/.../tables` ritorna in < 5s; cache hit-rate > 0 al secondo invocato
- **Meta**: granularity=smoke · priority=optional · tags=performance

### PERF-005 — Search canvas full-text su 10k canvas → < 2s
- **Stato**: TODO
- **Passi**: `GET /canvases?q=customers`
- **Asserzione**: response < 2s; uso indice `pg_trgm` o `tsvector` confermato in EXPLAIN
- **Meta**: granularity=smoke · priority=optional · tags=performance

---

## SIZE — Edge case dimensioni

### SIZE-001 — File CSV 100MB → streaming letto, no OOM
- **Stato**: TODO
- **Prereq**: EXT-010
- **Asserzione**: parsing completo, RSS worker < 300MB picco (no full-load in memoria)
- **Meta**: granularity=smoke · priority=optional · tags=limits,csv,streaming

### SIZE-002 — Query result 1M righe → streaming fetchmany cap rispettato
- **Stato**: TODO
- **Prereq**: QSTR-001
- **Riferimento**: `app/db/query_streaming.py:35-110`
- **Asserzione**: `fetchmany(CHUNK_SIZE)` invocato; memoria stabile; cap MAX_STREAMING_ROWS rispettato
- **Meta**: granularity=smoke · priority=optional · tags=limits,streaming

### SIZE-003 — Batch sink 50k records → split in chunk se cap configurato
- **Stato**: TODO
- **Prereq**: LOAD-005
- **Asserzione**: 50 POST eseguiti, ognuno con 1000 record; response 202 per ogni batch
- **Meta**: granularity=smoke · priority=optional · tags=limits

### SIZE-004 — File upload > MAX_UPLOAD_BYTES → 413
- **Stato**: TODO
- **Prereq**: PREP-014
- **Riferimento**: `app/config.py`
- **Passi**: upload file di dimensione > limit
- **Asserzione**: 413 `error_code=upload_too_large`, file non scritto
- **Meta**: granularity=smoke · priority=optional · tags=limits

### SIZE-005 — Result oggetto > RESULT_DB_HARD_CAP_BYTES → spillover su minio
- **Stato**: TODO
- **Prereq**: LOAD-009
- **Riferimento**: `app/config.py`
- **Asserzione**: result row contiene riferimento a object_storage; payload non in DB
- **Meta**: granularity=smoke · priority=optional · tags=limits,s3

### SIZE-006 — Canvas JSON > limite → 413 o validazione
- **Stato**: TODO
- **Prereq**: PREP-019
- **Passi**: `PUT /canvases/{id}` con definition > 5MB
- **Asserzione**: 413 o 422 con `error_code=canvas_definition_too_large`
- **Meta**: granularity=smoke · priority=optional · tags=limits,json

### SIZE-007 — `MemoryLimitExceeded` mappato a 413 input cap (upload)
- **Stato**: TODO
- **Riferimento**: `handler `MemoryLimitExceeded` → response 413`
- **Passi**: upload file a endpoint con cap input < dimensione
- **Asserzione**: 413 con `error_code=memory_limit_exceeded` e `cause=input_cap`
- **Meta**: granularity=smoke · priority=optional · tags=limits

### SIZE-008 — `MemoryLimitExceeded` mappato a 503 worker_rss
- **Stato**: TODO
- **Riferimento**: `worker memory guard`
- **Passi**: trigger run con working set previsto > `WORKER_RSS_CAP_BYTES`
- **Asserzione**: 503 con `error_code=memory_limit_exceeded` e `cause=worker_rss`; run marked `failed`
- **Meta**: granularity=smoke · priority=optional · tags=limits,celery

### SIZE-009 — `MemoryLimitExceeded` mappato a stage row-count cap (estrai)
- **Stato**: TODO
- **Riferimento**: `row-count guard nei nodi estrai/manipola`
- **Passi**: canvas con `MAX_STAGE_ROWS=1000`, source con 5000 righe
- **Asserzione**: run failed con `error_code=memory_limit_exceeded`, `cause=stage_row_count`, `stage=<node_id>`, `limit=1000`, `observed>=1000`
- **Meta**: granularity=smoke · priority=optional · tags=limits

### SIZE-010 — `MemoryLimitExceeded` mappato a stage row-count cap (manipola/carica)
- **Stato**: TODO
- **Riferimento**: `SIZE-009`
- **Passi**: canvas con join che esplode oltre cap stage; manipola+carica
- **Asserzione**: errore identico a SIZE-009 ma `stage` punta al nodo manipola/carica responsabile
- **Meta**: granularity=smoke · priority=optional · tags=limits

### SIZE-011 — Preview limit hard cap 10k righe
- **Stato**: TODO
- **Riferimento**: `app/core/results/router.py`
- **Passi**: `GET /results/{id}/preview?limit=99999`
- **Asserzione**: 200 con `len(rows) <= 10000`; response include `truncated: true` quando full > 10000; richiesta con `?limit=20000` ritorna esattamente 10000 righe
- **Meta**: granularity=smoke · priority=optional · tags=limits

---

## LOC — Locale / timezone

### LOC-001 — Timestamp tz-aware vs naive: handling documentato
- **Stato**: TODO
- **Prereq**: EXT-001
- **Dataset**: rows con `created_at` `2024-01-01T00:00:00+02:00` e `2024-01-01T00:00:00`
- **Asserzione**: tz-aware preservato con offset; naive convertito a UTC con flag `was_naive=true` o errore esplicito
- **Meta**: granularity=smoke · priority=optional · tags=locale

### LOC-002 — Decimal con separator "," (locale IT) in CSV → parser configurabile
- **Stato**: TODO
- **Prereq**: EXT-011
- **Dataset**: CSV con `1.234,56` (IT) e `1,234.56` (US)
- **Asserzione**: opzione `decimal_separator` rispettata; parsed value = 1234.56 in entrambi i casi
- **Meta**: granularity=smoke · priority=optional · tags=locale,csv

### LOC-003 — Encoding cliente Windows-1252 → conversione lossless o errore esplicito
- **Stato**: TODO
- **Prereq**: EXT-010
- **Dataset**: CSV con caratteri `€ è à ò` in cp1252
- **Asserzione**: con `encoding=cp1252` parsing OK; con default UTF-8 errore `error_code=invalid_encoding` con byte offset
- **Meta**: granularity=smoke · priority=optional · tags=locale

### LOC-004 — Date format MM/DD/YYYY (US) vs DD/MM/YYYY (EU) → parser configurabile
- **Stato**: TODO
- **Prereq**: EXT-010
- **Dataset**: CSV con `01/02/2024`
- **Asserzione**: con `date_format=DD/MM/YYYY` parsed = 1 feb 2024; con `MM/DD/YYYY` = 2 jan 2024
- **Meta**: granularity=smoke · priority=optional · tags=locale

### LOC-005 — DST switch durante run notturno → idempotenza preservata
- **Stato**: TODO
- **Prereq**: SCH-001
- **Passi**: schedule cron `30 2 * * *` la notte cambio ora (es. 26 ottobre)
- **Asserzione**: run scattato 1 volta (non 0, non 2); `next_run_at` calcolato in UTC; log indica `dst_transition_handled`
- **Meta**: granularity=smoke · priority=optional · tags=locale,idempotency

---

## UNDO — Soft delete / undo

### UNDO-001 — DELETE soft di canvas → flag deleted_at, non visibile in list di default
- **Stato**: TODO
- **Prereq**: PREP-019
- **Passi**: `DELETE /canvases/{id}` poi `GET /canvases`
- **Asserzione**: 204; row in DB con `deleted_at` non NULL; list non include il canvas
- **Meta**: granularity=smoke · priority=nice · tags=soft-delete,cleanup

### UNDO-002 — GET con ?include_deleted=true → ricomparso
- **Stato**: TODO
- **Prereq**: UNDO-001
- **Passi**: `GET /canvases?include_deleted=true`
- **Asserzione**: response include canvas con flag `deleted=true`, `deleted_at` popolato
- **Meta**: granularity=smoke · priority=nice · tags=soft-delete

### UNDO-003 — Restore canvas cancellato → torna in list normale, version incrementata
- **Stato**: TODO
- **Prereq**: UNDO-001
- **Passi**: `POST /canvases/{id}/restore`
- **Asserzione**: `deleted_at=NULL`, `version=N+1`; `GET /canvases` lo include
- **Meta**: granularity=smoke · priority=nice · tags=soft-delete,backup

### UNDO-004 — Hard delete dopo retention N giorni → rimosso definitivamente
- **Stato**: TODO
- **Prereq**: UNDO-001
- **Passi**: 1) soft delete con `deleted_at` simulato a -N-1gg 2) trigger retention task
- **Asserzione**: row fisicamente rimossa; `GET /canvases/{id}?include_deleted=true` → 404
- **Meta**: granularity=integration · priority=nice · tags=soft-delete,cleanup,retention

### UNDO-005 — Cascade soft-delete su gruppo → tutti i child marked deleted
- **Stato**: TODO
- **Prereq**: PREP-023
- **Passi**: soft `DELETE /groups/{id}`
- **Asserzione**: gruppo + projects + canvases + connections tutti con `deleted_at` set; restore gruppo ripristina cascade
- **Meta**: granularity=smoke · priority=nice · tags=soft-delete,cleanup

### UNDO-006 — `/canvas/raw` recovery endpoint bypass `CanvasState` validation
- **Stato**: TODO
- **Riferimento**: `app/core/canvases/router.py`
- **Passi**: 1) corrompi canvas con definition non conforme allo schema (insert diretto DB) 2) `GET /canvases/{id}` 3) `GET /canvases/{id}/raw`
- **Asserzione**: GET classico ritorna 500 con custom handler (vedi UNDO-007); `/raw` ritorna 200 con definition raw (string JSON come archiviato), no validation Pydantic applicata; payload include flag `raw=true`
- **Meta**: granularity=integration · priority=nice · tags=soft-delete

### UNDO-007 — Canvas corrupted handler (custom 500 con `project_id`+`reason`)
- **Stato**: TODO
- **Riferimento**: `app/core/canvases/router.py`
- **Passi**: GET canvas corrotto (vedi UNDO-006 step 1)
- **Asserzione**: 500 con body `{"error_code": "canvas_corrupted", "project_id": <int>, "canvas_id": <int>, "reason": <str>, "recovery_url": "/canvases/{id}/raw"}`; reason è messaggio human-readable senza traceback
- **Meta**: granularity=smoke · priority=nice · tags=soft-delete

---

## FLOW — Cross-feature flow (e2e completi)

### FLOW-001 — Schedule cron → run → estrai postgres → arricchisci REST OAuth2 → carica MySQL → notifica → audit
- **Stato**: TODO
- **Prereq**: SCH-001 + EXT-001 + ENR-001 + LOAD-002 + AUDIT-001
- **Asserzione**: in ordine: log_runs status=success, audit_log entry, target MySQL count match, notification_sent=true; result_id accessible via `GET /results/{id}`
- **Meta**: granularity=scenario · priority=must · tags=scenario,postgres,mysql,oauth2,rest,scheduler,audit

### FLOW-002 — Flow recovery: REST esterno fail al 3° tentativo → retry → success al 4°
- **Stato**: TODO
- **Prereq**: ENR-002 + RES-002
- **Dataset**: fake-rest endpoint con failure-counter (fail 3× then success)
- **Asserzione**: 4 chiamate loggate, run completed; result corretto; audit retry annotated
- **Meta**: granularity=scenario · priority=optional · tags=scenario,rest

### FLOW-003 — Flow batch: 100 canvas schedulati nello stesso minuto → tutti completati senza throttling reciproco
- **Stato**: TODO
- **Prereq**: SCH-001
- **Asserzione**: 100 run inseriti entro 60s di tick; completion p95 < 5min; no run con `error_code=worker_pool_full`
- **Meta**: granularity=scenario · priority=optional · tags=scenario

### FLOW-004 — Flow cancel: utente cancella run a metà → cleanup completo, no orphan
- **Stato**: TODO
- **Prereq**: PREP-021
- **Passi**: 1) start run lungo 2) `POST /runs/{id}/cancel`
- **Asserzione**: run `status=cancelled`; intermediate snapshots eliminati; lock rilasciato; nessuna riga parziale su target
- **Meta**: granularity=scenario · priority=optional · tags=scenario,cleanup

### FLOW-005 — Flow nested: canvas A → intermediate → canvas B legge intermediate di A → output combinato
- **Stato**: TODO
- **Prereq**: PREP-020
- **Riferimento**: `app/core/intermediate/`
- **Asserzione**: canvas B legge da `intermediate_runs` riferimento di A; output combinato matcha expected; entrambi run `success` linkati via `parent_run_id`
- **Meta**: granularity=scenario · priority=optional · tags=scenario,cache

---

## RATE — Rate limit API

### RATE-001 — POST /canvases/{id}/runs ripetuto N volte in 1s → 429 dopo soglia
- **Stato**: TODO
- **Prereq**: PREP-021
- **Riferimento**: `app/shared/login_rate_limiter.py`
- **Passi**: 50× POST in 1 secondo
- **Asserzione**: prime N risposte 202; dalla N+1 → 429 con `Retry-After` header
- **Meta**: granularity=smoke · priority=nice · tags=rate-limit

### RATE-002 — GET /schemas (introspection) ripetuto → rate limit per gruppo, non globale
- **Stato**: TODO
- **Prereq**: PREP-009
- **Passi**: gruppo A satura limit; gruppo B fa GET
- **Asserzione**: gruppo A 429; gruppo B 200 (limit isolato per tenant)
- **Meta**: granularity=smoke · priority=nice · tags=rate-limit

### RATE-003 — Response include header X-RateLimit-Remaining, X-RateLimit-Reset
- **Stato**: META
- **Prereq**: RATE-001
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Asserzione**: ogni response API include `X-RateLimit-Limit`,
  `X-RateLimit-Remaining`, `X-RateLimit-Reset` con valori coerenti.
  [META / cross-cutting-policy] Sostituito da
  `assert_rate_limit_headers_present` (helper opt-in chiamabile da
  qualunque test su endpoint con rate-limit configurato).
- **Meta**: granularity=smoke · priority=meta · tags=rate-limit,cross-cutting

### RATE-004 — Burst > steady-state ammesso temporaneamente
- **Stato**: TODO
- **Prereq**: RATE-001
- **Passi**: burst di 20 richieste in 1s con rate steady=10/s, burst=20
- **Asserzione**: tutte le 20 servite; richiesta 21 in stessa finestra → 429
- **Meta**: granularity=smoke · priority=nice · tags=rate-limit

### RATE-005 — Whitelist IP/utente bypass rate limit
- **Stato**: TODO
- **Prereq**: RATE-001
- **Asserzione**: utente whitelist può fare 1000 richieste/s senza 429; header `X-RateLimit-Bypass=true`
- **Meta**: granularity=smoke · priority=nice · tags=rate-limit

---

## HOOK — Webhook in entrata

### HOOK-001 — POST /webhooks/{id} con payload firmato HMAC valido → 200
- **Stato**: TODO
- **Prereq**: PREP-002
- **Passi**: POST con header `X-Signature: sha256=<hmac(secret, body)>` + `X-Timestamp`
- **Asserzione**: 200, evento processato, entry in `webhook_events`
- **Meta**: granularity=smoke · priority=nice · tags=webhook,rest

### HOOK-002 — Firma HMAC invalida → 401
- **Stato**: TODO
- **Prereq**: HOOK-001
- **Passi**: stesso payload, `X-Signature` con secret sbagliato
- **Asserzione**: 401 `error_code=invalid_signature`, no entry in `webhook_events`
- **Meta**: granularity=smoke · priority=nice · tags=webhook

### HOOK-003 — Replay attack (timestamp vecchio) → 401
- **Stato**: TODO
- **Prereq**: HOOK-001
- **Passi**: payload con `X-Timestamp` di 10 min fa
- **Asserzione**: 401 `error_code=stale_timestamp`, finestra accept = 5 min
- **Meta**: granularity=smoke · priority=nice · tags=webhook

### HOOK-004 — Payload > MAX → 413
- **Stato**: TODO
- **Prereq**: HOOK-001
- **Passi**: payload > MAX_WEBHOOK_BYTES (es. 1MB)
- **Asserzione**: 413, no parsing del body, log `error_code=webhook_payload_too_large`
- **Meta**: granularity=smoke · priority=nice · tags=webhook,limits

### HOOK-005 — Endpoint disabilitato → 404 (anche con firma valida)
- **Stato**: TODO
- **Prereq**: HOOK-001
- **Passi**: webhook con `enabled=false`, POST con firma corretta
- **Asserzione**: 404 (no leak: non rivela che esiste); audit entry per tentativo
- **Meta**: granularity=smoke · priority=nice · tags=webhook

---

## CORS — CORS / security headers

### CORS-001 — Response API include X-Content-Type-Options: nosniff
- **Stato**: META
- **Prereq**: PREP-002
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Asserzione**: ogni response API ha header `X-Content-Type-Options: nosniff`.
  [META / cross-cutting-policy] Sostituito da
  `assert_security_headers` (helper opt-in) chiamabile in qualunque
  test che voglia il check strict. Non viene applicato in autouse
  perche' molti endpoint JSON-only escludono headers browser-only.
- **Meta**: granularity=smoke · priority=meta · tags=security,cross-cutting

### CORS-002 — Response API include X-Frame-Options: DENY
- **Stato**: META
- **Prereq**: PREP-002
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Asserzione**: ogni response API ha header `X-Frame-Options: DENY`.
  [META / cross-cutting-policy] Sostituito da
  `assert_security_headers` (helper opt-in, accetta DENY o
  SAMEORIGIN).
- **Meta**: granularity=smoke · priority=meta · tags=security,cross-cutting

### CORS-003 — CSP header presente su frontend assets
- **Stato**: TODO
- **Prereq**: PREP-001
- **Passi**: `GET /` index html
- **Asserzione**: header `Content-Security-Policy` con direttive `default-src 'self'`, no `unsafe-inline` su script-src in prod
- **Meta**: granularity=smoke · priority=optional · tags=security

### CORS-004 — CORS preflight OPTIONS → solo origin whitelisted accettati
- **Stato**: TODO
- **Prereq**: PREP-002
- **Passi**: OPTIONS con `Origin: https://malicious.example.com`
- **Asserzione**: response NO `Access-Control-Allow-Origin` per origin non whitelisted; whitelist accettata con echo origin
- **Meta**: granularity=smoke · priority=optional · tags=security

### CORS-005 — API credenziali (cookie/JWT) NON loggate in error response
- **Stato**: META
- **Prereq**: SEC-006
- **Riferimento**: `app/shared/error_sanitizer.py`, `test-e2e/runner/cross_cutting.py`
- **Passi**: trigger 500 con request che include `Authorization: Bearer <jwt>`
- **Asserzione**: error response body + log non contengono il JWT, pattern
  `Bearer <REDACTED>`.
  [META / cross-cutting-policy] Coperto da autouse
  `assert_no_pii_in_response`: il pattern `Bearer <token>` con valori
  non redacted viene rilevato e fa fallire il test.
- **Meta**: granularity=smoke · priority=meta · tags=security,auth,cross-cutting

### CORS-006 — Exception handler globali iniettano CORS solo se origin in whitelist
- **Stato**: TODO
- **Riferimento**: `app/main.py`
- **Passi**: 1) `POST /connections` (trigger 500) con `Origin: https://app.plenora.example` (whitelisted) 2) ripeti con `Origin: https://malicious.example.com` (non whitelist)
- **Asserzione**: case 1: response 500 ha header `Access-Control-Allow-Origin: https://app.plenora.example`; case 2: header CORS assente (no echo); l'iniezione CORS avviene anche su path di errore (handlers globali) ma rispetta la whitelist; nessuna wildcard `*` mai presente
- **Meta**: granularity=integration · priority=optional · tags=security

---

## LOG — Logging strutturato

### LOG-001 — Ogni request ha request_id propagato in tutti i log
- **Stato**: META
- **Prereq**: PREP-002
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: 1) request con header `X-Request-ID: req-test-001` 2) raccogli log app
- **Asserzione**: tutti i log emessi durante quella request hanno field
  `request_id=req-test-001`.
  [META / cross-cutting-policy] Echo response coperto da autouse
  `_global_response_audit`. La propagazione interna ai log
  applicativi richiede ispezione log container e resta verificabile
  come test specifico se la pipeline CI raccoglie i log; la verifica
  del solo header echo e' sufficiente per garantire il contratto
  del middleware.
- **Meta**: granularity=integration · priority=meta · tags=logging,request-id,cross-cutting

### LOG-002 — Run ha run_id linkabile a tutti i log dei suoi step
- **Stato**: TODO
- **Prereq**: PREP-021
- **Asserzione**: filtrando log per `run_id=<id>` si ottengono entry di estrai/manipola/carica nello stesso run; ogni step ha anche `step_id`
- **Meta**: granularity=smoke · priority=optional · tags=logging

### LOG-003 — Log level configurabile via ENV
- **Stato**: TODO
- **Prereq**: PREP-001
- **Passi**: 1) `LOG_LEVEL=DEBUG` restart 2) verifica log debug presenti 3) `LOG_LEVEL=WARNING` 4) verifica solo warning+
- **Asserzione**: livello rispettato runtime, no log INFO con level=WARNING
- **Meta**: granularity=integration · priority=optional · tags=logging

### LOG-004 — PII (password, api_key, email cliente) NON in log
- **Stato**: META
- **Prereq**: SEC-006
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: trigger flow con password/api_key in payload, errore noto
- **Asserzione**: `grep -E "(password|api_key|secret_key)" log` ritorna 0
  occorrenze con valori non redacted.
  [META / cross-cutting-policy] Sostituito da
  `assert_no_pii_in_response` (autouse, su ogni response) e
  `assert_no_pii_in_logs` (helper opt-in per blob di log raccolti
  dal container backend).
- **Meta**: granularity=smoke · priority=meta · tags=logging,password,cross-cutting

### LOG-005 — Errore strutturato JSON con error_code, message, context
- **Stato**: TODO
- **Prereq**: PREP-002
- **Passi**: trigger 422
- **Asserzione**: log error entry parsa come JSON e include keys: `timestamp`, `level`, `error_code`, `message`, `context`, `request_id`
- **Meta**: granularity=smoke · priority=optional · tags=logging,json

---

## IDEM — Idempotenza generale

### IDEM-001 — POST /connections con Idempotency-Key duplicato → stesso connection_id
- **Stato**: META
- **Prereq**: PREP-003
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: 2× `POST /connections` con header `Idempotency-Key: idem-conn-001`
- **Asserzione**: entrambe 201 con stesso `connection_id`; in DB esiste 1 sola riga.
  [META / cross-cutting-policy] Sostituito da `assert_idempotent_post`
  chiamato da PREP-004 (e da qualunque altro test write su
  /connections).
- **Meta**: granularity=smoke · priority=meta · tags=idempotency,cross-cutting

### IDEM-002 — POST /api_connections con Idempotency-Key duplicato → stesso api_connection_id
- **Stato**: META
- **Prereq**: PREP-003
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: 2× `POST /api_connections` con stessa Idempotency-Key
- **Asserzione**: stesso `api_connection_id`, 1 sola riga.
  [META / cross-cutting-policy] Sostituito da `assert_idempotent_post`
  chiamato da PREP-010..PREP-012 (api_connections).
- **Meta**: granularity=smoke · priority=meta · tags=idempotency,rest,cross-cutting

### IDEM-003 — POST /datasources con Idempotency-Key duplicato → stesso datasource_id
- **Stato**: META
- **Prereq**: PREP-003
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: 2× `POST /datasources` con stessa Idempotency-Key
- **Asserzione**: stesso `datasource_id`, 1 sola riga.
  [META / cross-cutting-policy] Sostituito da `assert_idempotent_post`
  chiamato da PREP-014/PREP-015 (datasources FTP/SFTP).
- **Meta**: granularity=smoke · priority=meta · tags=idempotency,cross-cutting

### IDEM-004 — POST /canvases con Idempotency-Key duplicato → stesso canvas_id
- **Stato**: META
- **Prereq**: PREP-018
- **Riferimento**: `test-e2e/runner/cross_cutting.py`
- **Passi**: 2× `POST /projects/{id}/canvases` con stessa Idempotency-Key
- **Asserzione**: stesso `canvas_id`, 1 sola riga.
  [META / cross-cutting-policy] Sostituito da `assert_idempotent_post`
  chiamato da PREP-019 (canvas init).
- **Meta**: granularity=smoke · priority=meta · tags=idempotency,cross-cutting

### IDEM-005 — DELETE su risorsa già cancellata → 204/404 documentato e consistente
- **Stato**: TODO
- **Prereq**: PREP-019
- **Passi**: 1) `DELETE /canvases/{id}` 2) `DELETE /canvases/{id}` di nuovo
- **Asserzione**: prima 204; seconda 404 (consistente, documentato in spec API); audit log entry solo per la prima
- **Meta**: granularity=integration · priority=must · tags=idempotency,cleanup

### IDEM-006 — PUT idempotente: chiamato 2× → stesso stato finale
- **Stato**: TODO
- **Prereq**: PREP-020
- **Passi**: 2× `PUT /canvases/{id}` con identico body
- **Asserzione**: entrambe 200; canvas state identico; version incrementata massimo 1× (o documentazione esplicita del comportamento)
- **Meta**: granularity=smoke · priority=nice · tags=idempotency

---

## BEAT — Celery beat scheduler

### BEAT-001 — Leader election Redis SETNX TTL=120s
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:124-228`
- **Passi**: 1) avvia 2 istanze beat puntate allo stesso redis 2) ispeziona chiave `plenora:beat:leader` con `redis-cli`
- **Asserzione**: chiave creata con `SET NX EX 120`; valore = identità della prima istanza; TTL <=120 e >0; seconda istanza in stato follower (log `not leader, follower`)
- **Meta**: granularity=integration · priority=nice · tags=scheduler,redis

### BEAT-002 — Refresh leader on tick
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:124-228`
- **Passi**: 1) leader attivo 2) campiona TTL chiave `plenora:beat:leader` ogni 10s per 60s
- **Asserzione**: TTL refreshato a ~120s ad ogni tick; mai sceso sotto soglia critica (es. < 30s); valore della chiave invariato (stesso owner)
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### BEAT-003 — Fail-CLOSED default: 2 beat → 1 leader, no doppi enqueue
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:124-228`
- **Passi**: 1) `SCHEDULE_BEAT_LEADER_FAIL_OPEN=False` (default) 2) 2 beat concorrenti 3) crea schedule che enqueue task con marker univoco
- **Asserzione**: tick attesi N → enqueue conteggio = N (non 2N); follower NON enqueue task; log follower `skipped, not leader`
- **Meta**: granularity=integration · priority=must · tags=scheduler

### BEAT-004 — `SCHEDULE_BEAT_LEADER_FAIL_OPEN=True` fallback in-memory ammesso
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:124-228`
- **Passi**: 1) `SCHEDULE_BEAT_LEADER_FAIL_OPEN=True` 2) stop redis 3) avvio singolo beat
- **Asserzione**: beat usa lock in-memory; tick procedono; log `redis unavailable, fail-open in-memory lock`; quando redis torna, riprende SETNX
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### BEAT-005 — Telemetria transitions su Redis hash
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:124-228`
- **Passi**: 1) leader attivo 2) kill leader → second instance promote 3) ispeziona `HGETALL plenora:beat:telemetry`
- **Asserzione**: hash contiene chiavi `last_leader_acquired_at`, `last_leader_lost_at`, `transition_count` (>=1 dopo kill); transition_count incrementato a ogni cambio leader
- **Meta**: granularity=integration · priority=nice · tags=scheduler,redis

### BEAT-006 — Schedule healthcheck stato `{ok, warning, down, unknown}` threshold 180s
- **Stato**: TODO
- **Riferimento**: `app/core/scheduler/router.py:53-107`
- **Passi**: `GET /scheduler/healthcheck` con beat heartbeat di 0s, 100s, 200s, mai inviato
- **Asserzione**: 0s → `ok`; 100s → `ok`; 200s (> 180s) → `warning`; molto stale (>2× threshold) → `down`; mai → `unknown`; response include `last_heartbeat_at` e `seconds_since`
- **Meta**: granularity=smoke · priority=nice · tags=scheduler,infra

---

## CATCH — Catchup missed runs

### CATCH-001 — Detect missed run dopo crash beat
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:287-476`
- **Passi**: 1) schedule `*/5 * * * *` 2) stop beat per 11 minuti 3) restart beat
- **Asserzione**: catchup detecta 2 tick mancati (slot a -10min e -5min); emette enqueue per i missed slot; log `catchup_missed_runs detected=2 enqueued=2`
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### CATCH-002 — Runaway cap 5/10min
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:287-476`
- **Passi**: schedule `* * * * *` con beat down 60 minuti
- **Asserzione**: catchup emette al massimo 5 run nei primi 10 minuti dopo restart; log `catchup_runaway_cap_hit limit=5 window_seconds=600`; restanti 55 missed slot droppati
- **Meta**: granularity=smoke · priority=nice · tags=scheduler,limits

### CATCH-003 — Dedupe history-based (window 2h)
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:287-476`
- **Passi**: 1) catchup emette run per slot S 2) restart beat ancora 3) catchup re-evaluates slot S (entro 2h)
- **Asserzione**: secondo evaluation skip slot S grazie a entry in `schedule_catchup_history` con `slot_at=S`; log `catchup_skipped_already_emitted slot=S`
- **Meta**: granularity=integration · priority=nice · tags=scheduler

### CATCH-004 — NO last_run update on emit (loss-safe)
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:287-476`
- **Passi**: 1) snapshot `schedules.last_run_at` per schedule X 2) catchup emette missed run di X
- **Asserzione**: `last_run_at` invariato dopo emit; aggiornato SOLO al completamento del run (worker, non beat); audit row in `schedule_catchup_history` invece traccia l'emit
- **Meta**: granularity=integration · priority=nice · tags=scheduler,spatial

### CATCH-005 — Audit row in `schedule_catchup_history`
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:287-476`
- **Passi**: catchup emette missed run; query `SELECT * FROM schedule_catchup_history WHERE schedule_id=X`
- **Asserzione**: row con campi `schedule_id, slot_at, emitted_at, run_id, reason='missed_run'`; `slot_at` precede `emitted_at`; row immutabile (no update successivi)
- **Meta**: granularity=smoke · priority=nice · tags=scheduler,audit

---

## RECON — Storage reconciler beat

### RECON-001 — Gate 15min: lock acquisito, secondo skipped
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: 1) trigger reconcile beat 2) trigger secondo entro 15 min
- **Asserzione**: prima esecuzione acquisisce lock con TTL 15 min e procede; seconda log `reconcile_skipped lock_held`; nessuna doppia scansione
- **Meta**: granularity=integration · priority=nice · tags=reconciler

### RECON-002 — Operation `delete`: object orfano rimosso
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: 1) crea object S3 senza record DB 2) trigger reconcile
- **Asserzione**: object identificato come orfano; eseguito DELETE; row in `storage_reconcile_log` con `operation=delete, state=succeeded`
- **Meta**: granularity=integration · priority=nice · tags=reconciler,cleanup

### RECON-003 — Operation `upload_intent` (W2): grace period rispettato
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: 1) record DB con `upload_intent_at = now() - 5 min` (grace 30 min default) 2) trigger reconcile
- **Asserzione**: record NON marcato lost; reconciler skippa con `state=pending_grace`; dopo 31 min → marcato `lost` se ancora orfano
- **Meta**: granularity=integration · priority=nice · tags=reconciler

### RECON-004 — Max attempts → state `lost`
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: 1) record con `attempts=MAX-1` 2) trigger reconcile con object effettivamente assente
- **Asserzione**: attempts incrementato a MAX; state transizionato a `lost`; row `storage_reconcile_log` con `operation=mark_lost, state=succeeded, attempts=MAX`; future scansioni skippano (terminale)
- **Meta**: granularity=integration · priority=nice · tags=reconciler

### RECON-005 — Prune terminali > 30 giorni
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: 1) row `state=lost` o `state=succeeded` con `terminal_at = now() - 31 days` 2) trigger reconcile
- **Asserzione**: row rimossa da `storage_reconcile_log`; row `terminal_at = -29 days` preservata; log `prune_terminals removed=N retention_days=30`
- **Meta**: granularity=integration · priority=nice · tags=reconciler

### RECON-006 — Summary breakdown per (state, domain) in response
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:1136-1444`
- **Passi**: trigger reconcile via API admin
- **Asserzione**: response 200 con `{summary: {by_state_domain: {(succeeded, results): N, (failed, intermediate): M, (lost, backups): K, ...}, total: ...}}`; ogni domain (results, intermediate, backups, log_archive, eseguibili, imdf) presente
- **Meta**: granularity=smoke · priority=nice · tags=reconciler

---

## RET — Retention archive

### RET-001 — `LOG_ARCHIVE_ENABLED=true`: dopo `LOG_RETENTION_DAYS` log archiviati
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:848-1132`
- **Passi**: 1) `LOG_ARCHIVE_ENABLED=true`, `LOG_RETENTION_DAYS=30`, `LOG_ARCHIVE_BUCKET=plenora-logs` 2) inserisci log con `created_at = now() - 31 days` 3) trigger retention beat
- **Asserzione**: log eliminato da tabella DB; oggetto gzip presente in bucket `plenora-logs` con naming `log_archive_<date>.gz`; content-type `application/gzip`; row count archive == row count rimossi da DB
- **Meta**: granularity=integration · priority=nice · tags=retention

### RET-002 — `AUDIT_RETENTION_DAYS` rispettato
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:848-1132`
- **Passi**: 1) `AUDIT_RETENTION_DAYS=365` 2) audit_log entry con `created_at = now() - 366 days` e altre con -364 days 3) trigger retention
- **Asserzione**: entry > 365gg eliminate; entry < 365gg preservate; log `audit_retention_cleaned removed=N retained=M`
- **Meta**: granularity=integration · priority=nice · tags=retention

### RET-003 — `SET LOCAL plenora.retention_cleanup=true` bypassa trigger DB
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:848-1132`
- **Passi**: 1) tabelle con trigger BEFORE DELETE che rifiuta delete normali 2) retention task esegue DELETE in transazione con `SET LOCAL plenora.retention_cleanup=true`
- **Asserzione**: DELETE riuscito (trigger bypassato grazie a session var); DELETE fuori da retention task (senza set var) → bloccato dal trigger; nessun delete cross-talk
- **Meta**: granularity=integration · priority=nice · tags=retention

### RET-004 — Observability circolare: INSERT su `logs` durante retention → no infinite loop
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:848-1132`
- **Passi**: 1) abilita logging nelle azioni di retention (cleanup INSERT su `logs` ad ogni step) 2) trigger retention con backlog grande
- **Asserzione**: retention task termina in tempo finito (no spin); INSERT log durante retention NON re-triggera retention immediato (es. flag `in_retention_cycle` o tabella `logs_retention_pending`); count log generati < threshold di sicurezza (es. < 100 per cleanup cycle)
- **Meta**: granularity=integration · priority=nice · tags=retention

---

## CLEAN — Beat cleanup jobs

### CLEAN-001 — Eseguibili outputs > 2h rimossi
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:646-801`
- **Passi**: 1) crea file in `eseguibili_outputs/` con mtime di -3h 2) trigger beat cleanup
- **Asserzione**: file rimosso; file con mtime di -1h preservato; log `cleanup_eseguibili_outputs removed=1 kept=1`
- **Meta**: granularity=integration · priority=nice · tags=cleanup,eseguibili

### CLEAN-002 — IMDF bundles TTL 48h
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:646-801`
- **Passi**: 1) crea bundle in `imdf_bundles/` con mtime -49h 2) altro con mtime -47h 3) trigger cleanup
- **Asserzione**: bundle -49h rimosso; -47h preservato; log `cleanup_imdf_bundles ttl_hours=48`
- **Meta**: granularity=integration · priority=nice · tags=cleanup,imdf

### CLEAN-003 — `DATA_DIR/temp` orfani > 24h con `eseguibili_uploads/*` recursive
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:646-801`
- **Passi**: 1) crea albero `DATA_DIR/temp/eseguibili_uploads/<task>/file.dat` con mtime -25h 2) altro con mtime -10h 3) trigger cleanup
- **Asserzione**: cartella -25h rimossa ricorsivamente (file + parent dir vuota); -10h preservata; rimozione esplora ricorsivamente `eseguibili_uploads/*`
- **Meta**: granularity=integration · priority=nice · tags=cleanup,eseguibili

---

## INTM — Intermediate cache layer

### INTM-001 — Compare L2 branch `full_pg`
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:347-595`
- **Passi**: `POST /intermediate/compare` con `mode=full_pg`, due run_id snapshot completo Postgres
- **Asserzione**: 200 con `{equal: bool, diff_summary: {...}, hash_a, hash_b}`; usa branch `full_pg` (storage diff via SQL EXCEPT/INTERSECT); response include row counts
- **Meta**: granularity=smoke · priority=nice · tags=cache

### INTM-002 — Compare L2 branch `preview_only`
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:347-595`
- **Passi**: `POST /intermediate/compare` con `mode=preview_only`
- **Asserzione**: 200; diff calcolato sui soli preview rows (1000 righe max); response include `preview_size` e flag `is_partial=true`
- **Meta**: granularity=smoke · priority=nice · tags=cache

### INTM-003 — Compare L2 branch `metadata_only` → 422
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:347-595`
- **Passi**: `POST /intermediate/compare` con `mode=metadata_only`
- **Asserzione**: 422 con `error_code=metadata_only_compare_unsupported`; messaggio indica modi validi; **NON** è 501 (è scelta semantica: input rifiutato)
- **Meta**: granularity=smoke · priority=nice · tags=cache

### INTM-004 — Compare L2 branch `full_artifact` → 501
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:347-595`
- **Passi**: `POST /intermediate/compare` con `mode=full_artifact`
- **Asserzione**: 501 con `error_code=full_artifact_compare_not_implemented`; **NON** è 422 (è feature non ancora disponibile)
- **Meta**: granularity=smoke · priority=nice · tags=cache

### INTM-005 — Compare L2 mismatch → 422
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:347-595`
- **Passi**: `POST /intermediate/compare` con due snapshot di tipologia diversa (es. uno full_pg, uno preview)
- **Asserzione**: 422 con `error_code=intermediate_mode_mismatch`; messaggio indica i due mode incompatibili
- **Meta**: granularity=smoke · priority=nice · tags=cache

### INTM-006 — Pin/unpin retention FIFO
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:35-51`
- **Passi**: 1) crea N+1 snapshot (N = retention cap) 2) pin il primo 3) crea altri snapshot
- **Asserzione**: snapshot pinnato preservato; non-pinnati sotto cap rimossi in ordine FIFO (oldest first); unpinning + nuovo snapshot → entra in coda eviction normalmente
- **Meta**: granularity=integration · priority=nice · tags=cache,retention

### INTM-007 — Node data NaN/Inf → null sanitization (response only, hash invariato)
- **Stato**: TODO
- **Riferimento**: `app/core/intermediate/router.py:598`
- **Passi**: 1) snapshot con valori NaN/Inf in colonna numerica 2) `GET /intermediate/{id}/preview`
- **Asserzione**: response JSON valido con `null` al posto di NaN/Inf; hash dello snapshot computato sui valori originali (non sanitized) → hash invariato pre/post sanitize; log `sanitized_non_finite_values count=N`
- **Meta**: granularity=integration · priority=nice · tags=cache

### INTM-008 — `intermediate_mode` query param 4-literal Pydantic, 422 invalido
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:216-249`
- **Passi**: 1) `POST /runs?intermediate_mode=full_pg` (valido) 2) `?intermediate_mode=invalid_xyz`
- **Asserzione**: caso 1: 202; caso 2: 422 con detail Pydantic literal (4 valori ammessi: `full_pg|preview_only|metadata_only|full_artifact`)
- **Meta**: granularity=integration · priority=nice · tags=cache

### INTM-009 — `intermediate_mode` 403 non-superadmin
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:216-249`
- **Passi**: utente non-superadmin `POST /runs?intermediate_mode=preview_only`
- **Asserzione**: 403 con `error_code=intermediate_mode_override_requires_superadmin`; per superadmin stesso payload → 202
- **Meta**: granularity=smoke · priority=nice · tags=cache,rbac

### INTM-010 — `intermediate_mode` override per-call della global setting
- **Stato**: TODO
- **Riferimento**: `app/core/arricchisci/router.py:788`, `app/core/orchestrator/router.py:216-249`
- **Passi**: 1) `INTERMEDIATE_MODE_DEFAULT=metadata_only` (global) 2) superadmin `POST /runs?intermediate_mode=full_pg` 3) verifica run snapshot
- **Asserzione**: snapshot creato in mode `full_pg` (override); global setting invariato; senza query param run usa `metadata_only`
- **Meta**: granularity=integration · priority=nice · tags=cache

---

## SHR — Shares router

### SHR-001 — Set shares (superadmin only)
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:83`
- **Passi**: 1) admin (non super) `PUT /shares/{resource_id}` con `{groups: [G1, G2]}` → 403 2) superadmin stessa chiamata
- **Asserzione**: admin → 403 con `error_code=insufficient_role`; superadmin → 200; risorsa accessibile da utenti di G1, G2
- **Meta**: granularity=integration · priority=nice · tags=sharing,rbac

### SHR-002 — Add idempotente
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:102`
- **Passi**: 1) superadmin `POST /shares/{resource_id}/add` con `{group: G1}` 2) ripeti stessa add
- **Asserzione**: prima 200 con `added=true`; seconda 200 con `added=false, already_present=true`; in DB esiste 1 sola entry; nessun errore 409
- **Meta**: granularity=integration · priority=nice · tags=sharing,idempotency

### SHR-003 — Remove specifico
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:124`
- **Passi**: 1) shares = [G1, G2, G3] 2) `POST /shares/{resource_id}/remove` con `{group: G2}`
- **Asserzione**: 200; shares finali = [G1, G3]; remove di group non presente → 200 con `removed=false`
- **Meta**: granularity=integration · priority=nice · tags=sharing

### SHR-004 — `["*"]` = global share semantics
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:83`
- **Passi**: superadmin `PUT /shares/{resource_id}` con `{groups: ["*"]}`
- **Asserzione**: 200; risorsa accessibile da TUTTI i gruppi (incl. nuovi creati post-share); `GET /shares/{resource_id}` ritorna `{groups: ["*"]}`
- **Meta**: granularity=smoke · priority=nice · tags=sharing

### SHR-005 — `[]` = rimozione totale
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:83`
- **Passi**: 1) shares = [G1, G2] 2) `PUT /shares/{resource_id}` con `{groups: []}`
- **Asserzione**: 200; tutti i gruppi rimossi; risorsa visibile solo dal group_owner; `GET /shares/{resource_id}` ritorna `{groups: []}`
- **Meta**: granularity=integration · priority=nice · tags=sharing

### SHR-006 — Non-superadmin → 403
- **Stato**: TODO
- **Riferimento**: `app/core/auth/shares_router.py:83,102,124,145`
- **Passi**: utente role admin (non super) tenta tutti i 4 endpoint shares (PUT, add, remove, GET)
- **Asserzione**: PUT/add/remove → 403; GET potrebbe essere 200 (read-only documentato) o 403 (più strict); comportamento documentato e coerente
- **Meta**: granularity=smoke · priority=nice · tags=sharing,rbac

---

## SYS — Admin system-status dashboard

### SYS-001 — Backup block: ultimo backup + verify_restore status
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: `GET /admin/system-status`
- **Asserzione**: response include `backup: {last_backup_at, last_backup_size_bytes, last_verify_restore_at, last_verify_restore_status: "ok"|"failed"|"never_run"}`
- **Meta**: granularity=smoke · priority=optional · tags=admin,backup,soft-delete

### SYS-002 — Schedules failing 7gg block: count + lista
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: 1) crea 3 schedule con run failed in ultimi 7gg 2) `GET /admin/system-status`
- **Asserzione**: response include `failing_schedules: {count: 3, items: [{schedule_id, name, last_failure_at, failure_count_7d}]}`; only ultimi 7 giorni
- **Meta**: granularity=integration · priority=optional · tags=admin

### SYS-003 — Workers Celery online block (heartbeat)
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: `GET /admin/system-status` con N worker online
- **Asserzione**: response include `celery_workers: {online_count: N, workers: [{name, last_heartbeat_at, queues: [...], active_tasks: M}]}`; killare un worker → online_count decrementa
- **Meta**: granularity=smoke · priority=optional · tags=admin,scheduler,celery

### SYS-004 — Host resources block (psutil): CPU/mem 80%/92%/95% threshold
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: `GET /admin/system-status` con CPU=70/85/93/96%
- **Asserzione**: response include `host: {cpu_percent, memory_percent, status}`; status `ok` (<80%), `warning` (80-92%), `critical` (92-95%), `down` (>=95%); thresholds esatti
- **Meta**: granularity=smoke · priority=optional · tags=admin

### SYS-005 — DB pool block: usage% > 80%
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: 1) saturare pool al 50%, 81%, 95% 2) `GET /admin/system-status`
- **Asserzione**: response include `db_pool: {size, in_use, available, usage_percent, status}`; status `ok` (<80%), `warning` (>=80%); valori coerenti con `pg_stat_activity`
- **Meta**: granularity=integration · priority=optional · tags=admin

### SYS-006 — Redis memory block
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: `GET /admin/system-status`
- **Asserzione**: response include `redis: {used_memory_bytes, used_memory_human, maxmemory_bytes, usage_percent}`; valori da `INFO memory`
- **Meta**: granularity=smoke · priority=optional · tags=admin,redis

### SYS-007 — DB schema size block
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`
- **Passi**: `GET /admin/system-status`
- **Asserzione**: response include `db_schema: {total_bytes, total_human, by_table: [{table, bytes}]}`; somma by_table coerente con total
- **Meta**: granularity=smoke · priority=optional · tags=admin

### SYS-008 — JWT blacklist counters
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/service.py`, `app/shared/token_blacklist.py`
- **Passi**: 1) blacklist N token via logout 2) `GET /admin/system-status`
- **Asserzione**: response include `jwt_blacklist: {entries_count: N, expired_count, oldest_at, newest_at}`; entries_count coerente con redis/DB store
- **Meta**: granularity=integration · priority=optional · tags=admin,auth

### SYS-009 — Graceful degradation: 1 blocco fallisce → response include error per blocco, non 500 globale
- **Stato**: TODO
- **Riferimento**: `app/core/admin_system_status/router.py`
- **Passi**: stop redis (block redis fallisce); `GET /admin/system-status`
- **Asserzione**: 200 (NON 500); blocco redis ha shape `{error: "redis_unavailable", message: ...}` invece di dati; altri blocchi (backup, host, db_pool, ...) presenti e completi; tutti i blocchi tentati indipendentemente
- **Meta**: granularity=smoke · priority=optional · tags=admin

---

## TASK — Task full-cleanup 207 GDPR

### TASK-001 — 4 step idempotenti
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:593-763`
- **Passi**: `DELETE /tasks/{task_id}/full-cleanup` 2 volte di fila
- **Asserzione**: prima 200 con `{steps: {delete_result: ok, unlink_output_file: ok, delete_log_runs: ok, celery_forget: ok}}`; seconda 200 con stessi 4 step `ok` (idempotenti, non re-elimina ma neppure errore); ogni step può essere ri-eseguito senza side effect negativi
- **Meta**: granularity=smoke · priority=nice · tags=cleanup,gdpr,idempotency

### TASK-002 — `failed_steps[]` body quando uno step fallisce: 207 multi-status
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:593-763`
- **Passi**: simulare fallimento di uno step (es. minio offline durante `delete_result`); `DELETE /tasks/{task_id}/full-cleanup`
- **Asserzione**: 207 (Multi-Status); body `{steps: {...}, failed_steps: ["delete_result"], partial: true}`; gli altri 3 step comunque eseguiti
- **Meta**: granularity=smoke · priority=nice · tags=cleanup,gdpr

### TASK-003 — Path-traversal guard su `unlink_output_file` (ValueError → step skipped, no panic)
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:670-678`
- **Passi**: task con `output_file_path` che contiene `../` (es. `/data/results/../../etc/passwd`); cleanup
- **Asserzione**: step `unlink_output_file` skipped con reason `path_traversal_blocked`; ValueError catturato (no 500); altri step OK; log `unlink_output_file_skipped path_outside_allowed_root`
- **Meta**: granularity=smoke · priority=nice · tags=cleanup,gdpr

### TASK-004 — Fallback admin/superadmin se project già eliminato
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:593-763`
- **Passi**: 1) elimina project P 2) task_id T riferiva P 3) admin/superadmin `DELETE /tasks/{T}/full-cleanup`
- **Asserzione**: 200 (NON 404 per project mancante); cleanup procede sui residui (result, file, log_runs, celery) anche senza project; per utente normale → 404; per admin/superadmin → fallback con auth via group_id storico
- **Meta**: granularity=integration · priority=nice · tags=cleanup,gdpr,rbac

### TASK-005 — GDPR: tutti i path verificati, no stale references
- **Stato**: TODO
- **Riferimento**: `app/core/orchestrator/router.py:593-763`
- **Passi**: 1) crea task con result+file+log_runs+celery_state 2) `DELETE /tasks/{task_id}/full-cleanup` 3) query DB per ogni tabella che potrebbe referenziare task
- **Asserzione**: nessuna riga rimanente in `results`, nessun file su filesystem, nessuna entry `log_runs`, nessuno stato celery; query `SELECT * FROM <ogni_tabella> WHERE task_id=T` ritorna 0 righe per ognuna; nessun stale reference (verifica con FK scan + filesystem scan)
- **Meta**: granularity=integration · priority=nice · tags=cleanup,gdpr

---

## TKT — Tickets sub-system

### TKT-001 — Meta endpoint (counts per RBAC scope: superadmin/admin/user)
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:69-73`
- **Passi**: `GET /tickets/meta` come superadmin, admin, user normale
- **Asserzione**: superadmin vede counts globali (tutti gruppi); admin vede counts del proprio gruppo; user vede counts dei propri ticket; response shape `{open: N, in_progress: M, closed: K, total: T}` per ogni scope; counts coerenti con DB
- **Meta**: granularity=smoke · priority=nice · tags=tickets,rbac

### TKT-002 — CRUD ticket: create, read, update, list paginato
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:109,128,180`
- **Passi**: 1) `POST /tickets` 2) `GET /tickets/{id}` 3) `PATCH /tickets/{id}` (status) 4) `GET /tickets?page=1&size=10`
- **Asserzione**: POST 201 con `ticket_id`; GET 200 con dati; PATCH 200 con `status` aggiornato e `updated_at` nuovo; LIST 200 con `{items, total, page, size}`; paginazione rispettata
- **Meta**: granularity=integration · priority=nice · tags=tickets

### TKT-003 — Comments: create, list ordinato per timestamp
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:222`
- **Passi**: 1) `POST /tickets/{id}/comments` 3 volte 2) `GET /tickets/{id}/comments`
- **Asserzione**: ogni POST 201; LIST 200 con array ordinato `created_at ASC` (3 elementi nell'ordine di creazione); ogni comment ha `id, author, body, created_at`
- **Meta**: granularity=integration · priority=nice · tags=tickets

### TKT-004 — Attachment upload: MIME allowlist
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:230`
- **Passi**: upload allegato con MIME `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `text/plain`, `text/x-log`; poi prova `application/x-msdownload` (exe)
- **Asserzione**: 6 MIME ammessi → 201; exe → 415 con `error_code=mime_not_allowed`; allowlist esatta `{png, jpg, jpeg, gif, webp, log, txt}`
- **Meta**: granularity=smoke · priority=nice · tags=tickets

### TKT-005 — Attachment size cap: > MAX → 413
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:230`
- **Passi**: upload allegato con dimensione > `TICKET_ATTACHMENT_MAX_BYTES`
- **Asserzione**: 413 con `error_code=attachment_too_large`; file non scritto su disco/S3
- **Meta**: granularity=smoke · priority=nice · tags=tickets,limits

### TKT-006 — Soft delete: ticket marked deleted, non in list di default
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:252`
- **Passi**: 1) `DELETE /tickets/{id}` 2) `GET /tickets`
- **Asserzione**: 204; ticket ha `deleted_at IS NOT NULL`; LIST default non include ticket; `GET /tickets?include_deleted=true` lo include con flag `deleted=true`
- **Meta**: granularity=integration · priority=nice · tags=tickets,cleanup,soft-delete

### TKT-007 — Hard delete (admin only): rimosso definitivamente
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:264`
- **Passi**: 1) user normale `DELETE /tickets/{id}?hard=true` → 403 2) admin `DELETE /tickets/{id}?hard=true`
- **Asserzione**: user → 403; admin → 204; row fisicamente assente in DB; `GET /tickets/{id}?include_deleted=true` → 404
- **Meta**: granularity=integration · priority=nice · tags=tickets,rbac,cleanup

### TKT-008 — Attachment download inline (image preview)
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:343`
- **Passi**: `GET /tickets/{id}/attachments/{att_id}` per allegato png
- **Asserzione**: 200 con `Content-Type: image/png` e `Content-Disposition: inline; filename="..."`; payload binario corretto
- **Meta**: granularity=smoke · priority=nice · tags=tickets

### TKT-009 — Attachment download attachment per .log/.txt
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:382`
- **Passi**: `GET /tickets/{id}/attachments/{att_id}` per allegato .log o .txt
- **Asserzione**: 200 con `Content-Disposition: attachment; filename="..."` (NON inline); browser scarica il file invece di mostrarlo inline
- **Meta**: granularity=smoke · priority=nice · tags=tickets

### TKT-010 — Esecutore role 403 su tutti i 12 endpoint write
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:69-73,109,128,180,222,230,252,264,343,382,431`
- **Passi**: utente role `esecutore` (read-only su altri sistemi) tenta i 12 endpoint write tickets (POST ticket, PATCH ticket, DELETE ticket, POST comment, POST attachment, DELETE attachment, ecc.)
- **Asserzione**: tutti 12 endpoint → 403 con `error_code=insufficient_role`; esecutore può fare GET (lettura) ma nessuna scrittura
- **Meta**: granularity=smoke · priority=nice · tags=tickets,rbac

### TKT-011 — Cross-group ticket non visibile (RBAC)
- **Stato**: TODO
- **Riferimento**: `app/core/tickets/router.py:431`
- **Passi**: 1) user di group A crea ticket T 2) user di group B `GET /tickets/{T}` e `GET /tickets`
- **Asserzione**: GET singolo → 404 (tenant-blind); LIST → ticket T assente; user A vede T regolarmente
- **Meta**: granularity=integration · priority=nice · tags=tickets,tenancy,rbac

---

## FILES — File preview / multi-layer upload

### FILE-MULTI-001 — upload-multi-layer GPKG/GDB/SHP-zip rejects per layer
- **Stato**: TODO
- **Riferimento**: `app/core/files/router.py`
- **Passi**: upload bundle multi-layer (GPKG con 3 layer, dei quali 1 layer ha geometry corrupt); analoga per GDB e SHP-zip
- **Asserzione**: 207 con `{layers: [{name, status: "ok", row_count}, {name, status: "rejected", reason: "invalid_geometry"}, ...]}`; layer validi accettati e processati; layer rejected logged con reason precisa; bundle non rifiutato in toto (per-layer granularity)
- **Meta**: granularity=smoke · priority=nice · tags=files

### FILE-PREV-001 — preview-temp passthrough_extensions ritorna preview vuota (non 400)
- **Stato**: TODO
- **Riferimento**: `app/core/files/router.py`
- **Passi**: upload file con extension in `passthrough_extensions` (es. `.bin`, `.dat`); chiama `POST /files/preview-temp`
- **Asserzione**: 200 con `{preview: {rows: [], schema: [], passthrough: true}}`; **NON** 400 (passthrough = file non parsato ma comunque utilizzabile downstream); flag `passthrough=true` esplicito
- **Meta**: granularity=smoke · priority=nice · tags=files

---

## IMDF — IMDF publish/bundles

### IMDF-001 — Publish (full validation+upload)
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/router.py:286`
- **Passi**: `POST /imdf/publish` con bundle valido
- **Asserzione**: 200 con `{bundle_id, status: "published", uploaded: true, validation: {ok: true, warnings: []}}`; bundle presente su S3/MinIO; record DB con `state=published`
- **Meta**: granularity=smoke · priority=nice · tags=imdf

### IMDF-002 — Validate-publish (validation only, no upload)
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/router.py:286`
- **Passi**: `POST /imdf/validate-publish` con bundle
- **Asserzione**: 200 con `{validation: {ok: true|false, errors: [...], warnings: [...]}}`; nessun oggetto su S3; nessun record DB nuovo
- **Meta**: granularity=smoke · priority=nice · tags=imdf

### IMDF-003 — Bundles list paginato per group_id
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/router.py:438`
- **Passi**: `GET /imdf/bundles?page=1&size=20`
- **Asserzione**: 200 con `{items: [...], total, page, size}`; solo bundles del group_id dell'utente; paginazione rispettata; cross-group bundles assenti
- **Meta**: granularity=smoke · priority=nice · tags=imdf

### IMDF-004 — Bundles get singolo
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/router.py:438`
- **Passi**: `GET /imdf/bundles/{id}`
- **Asserzione**: 200 con metadata bundle (versione, created_at, validation summary, S3 url firmato); bundle di altro group → 404
- **Meta**: granularity=smoke · priority=nice · tags=imdf

### IMDF-005 — Bundles delete (con cleanup S3)
- **Stato**: TODO
- **Riferimento**: `app/core/imdf/router.py:628`
- **Passi**: `DELETE /imdf/bundles/{id}`
- **Asserzione**: 204; record DB rimosso (o soft-delete); oggetto S3 cancellato dal bucket; secondo DELETE → 404
- **Meta**: granularity=smoke · priority=nice · tags=imdf,s3,cleanup

---

## REQID — RequestId middleware + propagation

### REQID-001 — Header `X-Request-ID` echo in response
- **Stato**: META
- **Riferimento**: `app/core/auth/request_id_middleware.py`, `test-e2e/runner/cross_cutting.py`
- **Passi**: `GET /health` con header `X-Request-ID: rid-test-001`
- **Asserzione**: response include header `X-Request-ID: rid-test-001` (echo
  identico); ogni log emesso durante request ha field
  `request_id=rid-test-001`.
  [META / cross-cutting-policy] Sostituito dalla fixture autouse
  `_global_response_audit` in `runner/cross_cutting.py`: ogni test
  che fa richieste HTTP eredita questo check su ogni 2xx response.
- **Meta**: granularity=smoke · priority=meta · tags=request-id,cross-cutting

### REQID-002 — Trust-but-verify: truncate a 64 char
- **Stato**: META
- **Riferimento**: `app/core/auth/request_id_middleware.py`, `test-e2e/runner/cross_cutting.py`
- **Passi**: request con `X-Request-ID` di 200 caratteri (`A` ripetuto)
- **Asserzione**: response echo header con esattamente 64 caratteri (truncate); log
  `request_id_truncated original_length=200`; nessun reject (trust).
  [META / cross-cutting-policy] Sostituito da
  `assert_request_id_propagated` (verifica `len(rid) <= 64`) chiamato
  dall'autouse fixture su ogni 2xx.
- **Meta**: granularity=smoke · priority=meta · tags=request-id,cross-cutting

### REQID-003 — Propagation Celery via `before_task_publish`/`task_prerun`/`task_postrun`
- **Stato**: TODO
- **Riferimento**: `workers/celery_app.py:221-285`
- **Passi**: 1) request HTTP con `X-Request-ID: rid-celery-001` che enqueue task 2) ispeziona log worker celery
- **Asserzione**: task headers Celery contengono `request_id=rid-celery-001` (signal `before_task_publish`); worker log durante esecuzione ha `request_id=rid-celery-001` (signal `task_prerun` set context); cleanup post-task (signal `task_postrun` clear context); request_id propagato anche a sub-task
- **Meta**: granularity=integration · priority=nice · tags=request-id,celery

### REQID-004 — Fallback: se non incoming, usa `task_id` come request_id
- **Stato**: TODO
- **Riferimento**: `workers/celery_app.py:221-285`
- **Passi**: enqueue task da scheduler beat (no HTTP request → no incoming request_id)
- **Asserzione**: log worker ha `request_id=<celery_task_id>`; correlation usabile via task_id; response API che query `GET /runs/{run_id}` può linkare ai log via `task_id`
- **Meta**: granularity=smoke · priority=nice · tags=request-id,spatial

---

## ESEG — Eseguibili kiosk

### ESEG-001 — Kiosk analyze-upload + parse metadata
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:304`
- **Passi**: `POST /eseguibili/kiosk/analyze-upload` con file binario + form metadata
- **Asserzione**: 200 con `{file_id, parsed_metadata: {size, mime, extension, sha256, ...}}`; file_id univoco; metadata persisted su filesystem (no in-memory)
- **Meta**: granularity=smoke · priority=nice · tags=eseguibili

### ESEG-002 — Execute con file_id cross-user → 403
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:339-362`
- **Passi**: 1) user A `analyze-upload` → file_id F 2) user B `POST /eseguibili/kiosk/execute` con `file_id=F`
- **Asserzione**: 403 con `error_code=file_owner_mismatch`; user B non può accedere file di A
- **Meta**: granularity=integration · priority=nice · tags=eseguibili

### ESEG-003 — Upload metadata multi-worker safe (filesystem, no in-memory)
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:304`
- **Passi**: 1) worker A serve `analyze-upload` (file_id F) 2) worker B serve `execute` (file_id F dello stesso user)
- **Asserzione**: worker B trova metadata di F nonostante upload sia avvenuto su worker A; metadata storage su filesystem condiviso (es. `DATA_DIR/eseguibili_uploads/<file_id>.json`), no dict in-memory; entrambi worker leggono stesso file
- **Meta**: granularity=integration · priority=nice · tags=eseguibili,celery

### ESEG-004 — Download authz: solo task_owner accede al risultato
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:409`
- **Passi**: 1) user A esegue eseguibile → result task_id T 2) user B `GET /eseguibili/kiosk/download/{T}`
- **Asserzione**: user B → 403 (o 404 tenant-blind); user A → 200 con file binario; audit_log entry per tentativo B `denied`
- **Meta**: granularity=integration · priority=nice · tags=eseguibili

### ESEG-005 — Cleanup output post-send (single-shot)
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:482`
- **Passi**: 1) user A download result task_id T (200) 2) user A re-download stesso T
- **Asserzione**: prima 200; seconda 404 con `error_code=output_already_consumed`; file fisicamente rimosso dal filesystem dopo prima consegna (single-shot)
- **Meta**: granularity=integration · priority=nice · tags=eseguibili,cleanup

### ESEG-006 — Beat sweep orfani 2h
- **Stato**: TODO
- **Riferimento**: `workers/beat_scheduler.py:646`
- **Passi**: 1) `analyze-upload` ma mai `execute` 2) attendi >2h (o mtime simulato) 3) trigger beat
- **Asserzione**: file orfano + metadata rimossi; log `eseguibili_orphan_sweep removed=1 ttl_hours=2`
- **Meta**: granularity=integration · priority=nice · tags=eseguibili,scheduler

### ESEG-007 — `validate_no_executable` MIME-block-only: polyglot zip permesso (gap noto)
- **Stato**: TODO
- **Riferimento**: `app/core/eseguibili/router.py:339-362`
- **Passi**: 1) crea polyglot file (zip valido che è anche eseguibile valido, MIME `application/zip`) 2) upload via `analyze-upload`
- **Asserzione**: validatore controlla SOLO MIME (allowlist non-executable); polyglot zip passa il check anche se contiene magic-byte exe; **comportamento corrente documentato come gap**: response 200, file accettato; test funge da regression-marker; risoluzione futura: aggiungere magic-byte sniff oltre MIME
- **Meta**: granularity=integration · priority=nice · tags=eseguibili,rbac

---

## CONTRACT — Contract test (response shape validation, pre-Fase 1)

### CT-001 — Login response shape
- **Stato**: TODO
- **Prereq**: profilo `core`
- **Riferimento**: `app/core/auth/models.py:22`
- **Asserzione**: 200 + body con keys {access_token, token_type, user_id, username,
  group_id, role, expires_in}. access_token: str non vuoto.
  token_type: "bearer". expires_in: int positivo. Le altre str
  (user_id/username/group_id/role) sono str non vuote.
- **Meta**: granularity=smoke · priority=nice · tags=contract,auth

### CT-002 — POST /api/connections shape (postgres)
- **Stato**: TODO
- **Prereq**: CT-001 + profilo `core`
- **Riferimento**: `app/core/connections/models.py`, `test-e2e/runner/builders/connection.py`
- **Asserzione**: ConnectionBuilder.postgres() -> 200/201 + response con id riconoscibile
  (id | connection_id). Idempotenza body: stessa POST con stesso name
  ritorna 4xx (idealmente 409/422), MAI 5xx.
- **Meta**: granularity=smoke · priority=nice · tags=contract,connections,postgres

### CT-003 — POST /api/connections/{id}/test response shape
- **Stato**: TODO
- **Prereq**: CT-002 + profilo `core`
- **Riferimento**: `app/core/connections/models.py`
- **Asserzione**: 200 con body shape {success: bool, message: str, details?: dict}.
  Sia success: true che success: false sono response valide.
  NB: il legacy {ok, latency_ms} è errato — il contratto reale è
  success/message.
- **Meta**: granularity=smoke · priority=nice · tags=contract,connections

### CT-004 — POST /api/projects shape
- **Stato**: TODO
- **Prereq**: CT-002 + profilo `core`
- **Riferimento**: `app/core/projects/router.py`, `test-e2e/runner/factories.py`
- **Asserzione**: factories.project_estrai_payload() -> 200/201 con id riconoscibile
  (id | project_id). Il payload NON include group_id (deriva da JWT).
  DELETE /api/projects/{id} ritorna 200/204/202 (cleanup ok).
- **Meta**: granularity=smoke · priority=nice · tags=contract,projects

### CT-005 — PUT /api/projects/{id}/canvas shape
- **Stato**: TODO
- **Prereq**: CT-004 + profilo `core`
- **Riferimento**: `app/core/projects/router.py`, `app/core/projects/canvas_models.py`, `test-e2e/runner/builders/canvas.py`
- **Asserzione**: CanvasBuilder().with_estrai_sql(connection_id=<pg_id>, table=...).build()
  -> PUT canvas 200/201/204. GET /api/projects/{id} successivo riflette
  il canvas (presenza di nodes/edges top-level oppure di canvas /
  canvas_state come sotto-oggetto).
- **Meta**: granularity=smoke · priority=nice · tags=contract,projects,canvas

### CT-006 — POST /api/projects/{id}/execute shape + task polling
- **Stato**: TODO
- **Prereq**: CT-005 + profilo `core`
- **Riferimento**: `app/core/projects/router.py`, `app/core/tasks/router.py`
- **Asserzione**: POST /api/projects/{id}/execute -> 200/201/202 con task_id (o id) nel
  body. GET /api/tasks/{task_id} polled fino a status terminale (success
  | failure | cancelled | error | completed | failed) entro 60s. Nessuna
  asserzione su success/fail (è un contract test, non funzionale).
- **Meta**: granularity=smoke · priority=nice · tags=contract,execute,tasks

---


<!-- generated at 2026-05-10T01:48:41Z by test-e2e/catalog/render.py -->
