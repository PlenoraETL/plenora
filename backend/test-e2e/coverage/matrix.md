# Plenora E2E - Coverage Matrix

Sorgente catalogo: **yaml**  |  Test totali: **364**

Legenda: `[x]` >=1 test DONE  |  `[.]` solo TODO/WIP  |  `[~]` solo SKIP  |  `[ ]` nessun test catalogato

## Executive summary

- Celle totali: **471**
- Coperte (>=1 test DONE): **0** (0.0%)
- Catalogate (>=1 test TODO/WIP): **56** (11.9%)
- Solo catalogate (no DONE): **56**
- Scoperte (0 test): **415**

**Top 5 gap (celle 0-test in matrici critiche):**

- `auth` :: arcgis x api_key_header
- `auth` :: arcgis x api_key_query
- `auth` :: arcgis x basic
- `auth` :: arcgis x bearer
- `auth` :: arcgis x iam

## Extraction coverage (source x dataset_block)

_Per ogni sorgente, quali tipi di dato estraiamo: base (happy path), edge (Unicode/formule/BOM), null (NULL handling), volume (>1k rows), streaming (chunk/line-by-line)._

| row \ col | base | edge | null | volume | streaming |
|---|---|---|---|---|---|
| `postgres` | [.] 13 | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 |
| `mysql` | [.] 5 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sqlserver` | [.] 1 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 |
| `oracle` | [.] 6 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sqlite` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `csv` | [.] 3 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 |
| `xlsx` | [.] 2 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 |
| `xls` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `json` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 |
| `ndjson` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 |
| `geojson` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `shp` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `gpkg` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `parquet` | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `ftp` | [.] 3 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sftp` | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sharepoint` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_api_key` | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_oauth2_cc` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_oauth2_pwd` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_basic` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_arcgis` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest_bearer` | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `s3` | [.] 3 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `azure_blob` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |

### Detail — extraction

- **postgres x base**: `ADM-004` (TODO), `EXT-001` (TODO), `EXT-005` (TODO), `EXT-007` (TODO), `FLOW-001` (TODO), `INF-004` (TODO), `INF-007` (TODO), `INTM-001` (TODO), `LOAD-001` (TODO), `LOAD-013` (TODO), `PREP-001` (TODO), `PREP-004` (TODO), `RES-001` (TODO)
- **postgres x null**: `INT-005` (TODO)
- **mysql x base**: `EXT-007` (TODO), `FLOW-001` (TODO), `LOAD-002` (TODO), `LOAD-011` (TODO), `PREP-005` (TODO)
- **sqlserver x base**: `PREP-006` (TODO)
- **sqlserver x edge**: `EXT-008` (TODO)
- **oracle x base**: `EXT-009` (TODO), `LOAD-003` (TODO), `LOAD-008` (TODO), `LOAD-010` (TODO), `LOAD-012` (TODO), `PREP-007` (TODO)
- **csv x base**: `EXT-011` (TODO), `EXT-028` (TODO), `EXT-030` (TODO)
- **csv x edge**: `EXT-010` (TODO)
- **xlsx x base**: `EXT-012` (TODO), `EXT-029` (TODO)
- **xlsx x edge**: `EXT-013` (TODO)
- **json x base**: `EXT-014` (TODO)
- **json x streaming**: `EXT-015` (TODO)
- **ndjson x streaming**: `EXT-015` (TODO)
- **geojson x base**: `EXT-016` (TODO)
- **shp x base**: `EXT-017` (TODO)
- **gpkg x base**: `EXT-018` (TODO)
- **parquet x base**: `EXT-019` (TODO), `EXT-020` (TODO)
- **ftp x base**: `EXT-028` (TODO), `EXT-029` (TODO), `PREP-014` (TODO)
- **sftp x base**: `EXT-029` (TODO), `PREP-015` (TODO)
- **rest_api_key x base**: `EXT-021` (TODO), `PREP-010` (TODO)
- **rest_oauth2_cc x base**: `PREP-011` (TODO)
- **rest_arcgis x base**: `PREP-012` (TODO)
- **rest_bearer x base**: `EXT-022` (TODO)
- **s3 x base**: `ADM-004` (TODO), `EXT-030` (TODO), `PREP-017` (TODO)

## Load coverage (source x target)

_Per ogni coppia (sorgente canvas, destinazione di scrittura): DB->DB cross-dialect, DB->file, file->DB, DB->REST sink, ecc._

| row \ col | postgres | mysql | sqlserver | oracle | sqlite | csv | xlsx | json | geojson | shp | parquet | rest_sink | s3 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `postgres` | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 2 | [ ] 0 |
| `mysql` | [ ] 0 | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sqlserver` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `oracle` | [ ] 0 | [ ] 0 | [ ] 0 | [.] 4 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sqlite` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `csv` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `xlsx` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `json` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `geojson` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `parquet` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `rest` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `s3` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 2 |

### Detail — load

- **postgres x postgres**: `LOAD-001` (TODO), `LOAD-013` (TODO)
- **postgres x json**: `LOAD-013` (TODO)
- **postgres x rest_sink**: `LOAD-005` (TODO), `LOAD-006` (TODO)
- **mysql x mysql**: `LOAD-002` (TODO), `LOAD-011` (TODO)
- **oracle x oracle**: `LOAD-003` (TODO), `LOAD-008` (TODO), `LOAD-010` (TODO), `LOAD-012` (TODO)
- **csv x csv**: `LOAD-004` (TODO)
- **s3 x s3**: `LOAD-007` (TODO), `LOAD-009` (TODO)

## Auth coverage (source_protocol x auth_pattern)

_Per ogni protocollo che richiede autenticazione, quali pattern abbiamo testato. Le celle vuote per coppie 'impossibili' (es. ftp x oauth2_cc) sono gap noti, non bug._

| row \ col | no_auth | api_key_query | api_key_header | bearer | basic | oauth2_cc | oauth2_password | arcgis_token | ssh_key | iam |
|---|---|---|---|---|---|---|---|---|---|---|
| `rest` | [ ] 0 | [.] 1 | [.] 2 | [.] 1 | [ ] 0 | [.] 4 | [.] 1 | [.] 2 | [ ] 0 | [ ] 0 |
| `arcgis` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 2 | [ ] 0 | [.] 2 | [ ] 0 | [ ] 0 |
| `ftp` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sftp` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sharepoint` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `s3` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `azure_blob` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |

### Detail — auth

- **rest x api_key_query**: `EXT-021` (TODO)
- **rest x api_key_header**: `EXT-021` (TODO), `PREP-010` (TODO)
- **rest x bearer**: `EXT-022` (TODO)
- **rest x oauth2_cc**: `ARC-002` (TODO), `ARC-006` (TODO), `ENR-001` (TODO), `PREP-011` (TODO)
- **rest x oauth2_password**: `ENR-008` (TODO)
- **rest x arcgis_token**: `ARC-001` (TODO), `PREP-012` (TODO)
- **arcgis x oauth2_cc**: `ARC-002` (TODO), `ARC-006` (TODO)
- **arcgis x arcgis_token**: `ARC-001` (TODO), `PREP-012` (TODO)

## Pagination coverage (REST endpoint shape x pagination_style)

_Per ogni shape della response REST, quali stili di paginazione supportiamo end-to-end. 'none' = endpoint non paginato._

| row \ col | none | offset | page_size | cursor | link_header | no_meta |
|---|---|---|---|---|---|---|
| `object_root` | [ ] 0 | [.] 4 | [.] 1 | [.] 1 | [.] 1 | [ ] 0 |
| `array_root` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `nested_path` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `feature_collection` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `arcgis_query` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |

### Detail — pagination

- **object_root x offset**: `EXT-023` (TODO), `LOC-001` (TODO), `LOC-003` (TODO), `PERF-003` (TODO)
- **object_root x page_size**: `API-006` (TODO)
- **object_root x cursor**: `EXT-024` (TODO)
- **object_root x link_header**: `EXT-025` (TODO)

## Error path coverage (source x http_status_class)

_Per ogni sorgente, quali classi di errore esercitiamo nei test (auth scaduta, throttling, payload corrotto, network timeout)._

| row \ col | 401 | 403 | 404 | 422 | 429 | 500 | 503 | malformed_json | timeout | redirect_loop |
|---|---|---|---|---|---|---|---|---|---|---|
| `rest` | [.] 9 | [.] 14 | [.] 11 | [.] 14 | [.] 9 | [.] 15 | [.] 5 | [.] 1 | [.] 2 | [ ] 0 |
| `arcgis` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `postgres` | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 |
| `mysql` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sqlserver` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `oracle` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [.] 1 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `ftp` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `sftp` | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |
| `s3` | [ ] 0 | [ ] 0 | [.] 2 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 | [ ] 0 |

### Detail — error_paths

- **rest x 401**: `AUTH-004` (TODO), `ENR-003` (TODO), `HOOK-002` (TODO), `HOOK-003` (TODO), `MFA-003` (TODO), `SEC-005` (TODO), `SP-002` (TODO), `SP-003` (TODO), `SP-004` (TODO)
- **rest x 403**: `API-007` (TODO), `API-008` (TODO), `ESEG-002` (TODO), `ESEG-004` (TODO), `INTM-009` (TODO), `PERM-001` (TODO), `PERM-002` (TODO), `PERM-003` (TODO), `PERM-006` (TODO), `SHR-001` (TODO), `SHR-006` (TODO), `TENANT-002` (TODO), `TKT-007` (TODO), `TKT-010` (TODO)
- **rest x 404**: `ESEG-004` (TODO), `ESEG-005` (TODO), `HOOK-005` (TODO), `IDEM-005` (TODO), `SCH-006` (TODO), `TASK-004` (TODO), `TENANT-002` (TODO), `TENANT-004` (TODO), `TKT-007` (TODO), `TKT-011` (TODO), `UNDO-004` (TODO)
- **rest x 422**: `API-002` (TODO), `API-015` (TODO), `INT-001` (TODO), `INT-002` (TODO), `INTM-003` (TODO), `INTM-004` (TODO), `INTM-005` (TODO), `INTM-008` (TODO), `LOG-005` (TODO), `SCH-005` (TODO), `SIZE-006` (TODO), `SSRF-001` (TODO), `SSRF-002` (TODO), `SSRF-003` (TODO)
- **rest x 429**: `AUTH-003` (TODO), `ENR-002` (TODO), `PERF-002` (TODO), `RATE-001` (TODO), `RATE-002` (TODO), `RATE-004` (TODO), `RATE-005` (TODO), `SEC-003` (TODO), `SP-005` (TODO)
- **rest x 500**: `API-009` (TODO), `BCK-007` (TODO), `CORS-005` (META), `CORS-006` (TODO), `PERF-001` (TODO), `PERF-004` (TODO), `SCH-007` (TODO), `SIZE-009` (TODO), `SP-002` (TODO), `SP-003` (TODO), `SSRF-003` (TODO), `SYS-009` (TODO), `TASK-003` (TODO), `UNDO-006` (TODO), `UNDO-007` (TODO)
- **rest x 503**: `INF-006` (TODO), `INF-008` (TODO), `INF-013` (TODO), `RES-005` (TODO), `SIZE-008` (TODO)
- **rest x malformed_json**: `ENR-004` (TODO)
- **rest x timeout**: `ENR-005` (TODO), `RES-002` (TODO)
- **postgres x 403**: `ADM-004` (TODO)
- **postgres x 503**: `INF-007` (TODO)
- **oracle x 500**: `LOAD-010` (TODO)
- **s3 x 404**: `IMDF-004` (TODO), `IMDF-005` (TODO)
