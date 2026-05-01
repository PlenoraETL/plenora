# Phase 0.5a — Hot-File Deep Dive (Evidence-deep edition)

Date: 2026-04-28 (rev. 2026-04-28 evidence-deep)
Status: **analysis only — no refactor opened, no test modified.**
Scope: 5 hot file (LOC + coupling) individuati nell'audit Phase 0 wide
        (`wide_audit_2026_04_28.md`).

This revision adds, per file:
- internal map with line ranges
- essential call graph (who calls what, who is called from where)
- external imports / dependencies
- points of state mutation / I/O
- regression-risk hotspots
- refactor candidate decomposed in small commits with per-commit rollback
  and per-commit test gate

The recommendation count and the ranking are unchanged from the previous
revision — only evidence/precision increased.

Read together with:
- `wide_audit_2026_04_28.md` (mappa allargata)
- `phase_0_4_test_coupling_evidence.md` (initial source-bound inventory)
- `delta_post_storage_2026_04_28.md` (storage / intermediate boundary)
- `test_coupling_roadmap_0_5d.md` (test coupling per target — companion)

---

## 0. Tabella Comparativa (riepilogo invariato)

| File | LOC | Classi | Funzioni top-level | Source-bound tests reali | Coupling esterno (call sites prod) | Rischio modifica | Raccomandazione |
| --- | ---:| ---:| ---:| ---:| ---:| --- | --- |
| `app/core/features/arcgis_client.py` | 2476 | 6 | 2 | 0 (vedi 0.5d) | 4 (features_service, imdf/uploader, imdf/publish_ops, imdf/validator) | medio | **small** |
| `app/core/manipola/transforms/spatial.py` | 1941 | 0 | 32 ops + 3 helper | 0 | 1 (transforms `__init__`) | basso | **no-op** |
| `app/services/file_service.py` | 1847 | 13 | 3 | 0 (vedi 0.5d) | 12+ | alto | **medium (post-prod)** |
| `workers/tasks/enrichment_task.py` | 1430 | 0 | 5 | 3 reali, ~59 getsource refs | 2 + trigger interno | **alto** | **medium (post-prod, dopo harness)** |
| `app/core/scheduler/execution_manager.py` | 1125 | 2 | 0 | 4 reali + 4 cross-file scanner | 2 | medio | **small** |

---

## 1. `app/core/features/arcgis_client.py` (2476 LOC)

### 1.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Module docstring + imports | 1–69 | doc + `requests`, `pandas`, `app.utils.logger.get_logger` |
| `_mask_secret(value, visible=4)` | 73–77 | helper redaction logger |
| `ArcGISConnectionInfo` (dataclass) | 85–100 | DTO connessione (server_url, auth_type, user/pwd, client_id/secret, token_url) |
| `HttpClientProtocol` | 102–106 | DI Protocol (get/post) |
| `DefaultHttpClient` | 112–126 | wrapper `requests.get/post` |
| `EsriTypeMapper` | 132–183 | static maps tipi Esri↔Python (`map_field_type`, `map_geometry_type`) |
| `EsriJsonConverter` | 190–401 | static methods: `features_to_rows`, `_geometry_to_wkt`, `geodataframe_to_esri_features`, `_shapely_to_esri` |
| **`ArcGISClient` class** | **407–2469** | **34 methods**, ~2060 LOC |
| `__init__` | 428–430 | `self._http`, `self._token_cache: Dict[str, dict]` |
| `_request_with_token` | 436–588 | **token-aware HTTP** with refresh on 401/498/499 + 429 backoff (CORE) |
| `authenticate` | 590–622 | dispatch oauth2 / token / public + cache lookup |
| `_authenticate_oauth2` | 624–665 | POST `/sharing/rest/oauth2/token` |
| `_authenticate_token` | 667–729 | classic `username/password → generateToken` (referer/requestip strategies) |
| `_resolve_token_url` | 731–783 | custom URL → online detect → discovery → server_type fallback |
| `_discover_token_url` | 785–824 | GET `/rest/info` to extract authInfo.tokenServicesUrl |
| `list_services` | 826–851 | dispatch online/server |
| `_is_arcgis_online` | 853–863 | `*.arcgis.com` test |
| `_list_services_online` | 865–957 | walks `/sharing/rest/portals/self/...` |
| `list_folders` | 959–968 | dispatch |
| `_list_folders_online` | 970–1002 | content listing |
| `_list_folders_server` | 1004–1022 | REST services folder list |
| `search_services_online` | 1024–1121 | search API |
| `_list_services_server` | 1123–1180 | REST services walk |
| `_get_rest_services_url` | 1182–1206 | base path resolution |
| `list_layers` | 1208–1281 | layers + tables of feature service |
| `_build_layer_urls` | 1283–1310 | URL constructor |
| `get_layer_schema` | 1312–1435 | full schema + sample 1 record + drawingInfo |
| `_get_feature_count` | 1437–1461 | `?returnCountOnly=true` |
| `query_features` | 1463–1589 | **paginated query** with WHERE/bbox + maxRecordCount loop |
| `_find_oid_field` | 1591–1604 | walks fields list |
| `add_features` | 1606–1627 | calls `_write_batch("addFeatures", ...)` |
| `update_features` | 1629–1649 | calls `_write_batch("updateFeatures", ...)` |
| `delete_features` | 1651–1713 | OBJECTID-based delete |
| `_write_batch` | 1715–1807 | **batch loop, per-batch token-aware refresh** (C12 fix) |
| `dataframe_to_esri_features` | 1814–1896 | DataFrame→Esri features |
| `_shapely_to_esri` | 1898–1957 | Shapely geom→Esri geom |
| `_esri_geometry_type` | 1959–1974 | maps shapely.geom_type → Esri |
| `_python_type_to_esri_field` | 1976–1988 | infers Esri field type from pandas dtype |
| `_default_drawing_info` | 1990–2050 | renderer defaults |
| `create_service` | 2052–2352 | full Feature Service creation flow |
| `test_connection` | 2354–2404 | end-to-end auth + fetch test |
| `update_renderer` | 2406–2468 | PUT renderer to admin endpoint |
| `create_client` (factory) | 2475–2476 | `def create_client(http_client=None) -> ArcGISClient` |

### 1.2 Essential call graph

Inbound (who imports `ArcGISClient` / `arcgis_client`):
- `backend/app/core/features/features_service.py` — runtime read/write
- `backend/app/core/imdf/arcgis_uploader.py` — IMDF publish
- `backend/app/core/imdf/arcgis/publish_ops.py` — publish workflow
- `backend/app/core/imdf/validator.py` — pre-publish validation
- `backend/app/core/features/__init__.py` — re-export

Internal: every public method (`list_*`, `query_features`, `add_features`,
`update_features`, `delete_features`, `create_service`, `test_connection`,
`update_renderer`) routes through `_request_with_token` (line 436), which
itself depends on `authenticate` (590), which dispatches to
`_authenticate_oauth2` / `_authenticate_token`. The token cache
`self._token_cache` is the single shared instance state.

`_write_batch` (1715) is the only loop that calls `_request_with_token`
multiple times in a tight loop — token refresh mid-batch is the C12 fix
that this loop relies on.

### 1.3 External imports / dependencies

- `requests` (HTTP), `pandas` (DataFrame conversion in `dataframe_to_esri_features`)
- `app.utils.logger.get_logger` (logger)
- `urllib.parse` (only `_authenticate_oauth2` line 631 — local import)
- `random` (only `_request_with_token` jitter, line 481 — local import)
- shapely is **not** imported at module level — used only via
  `dataframe_to_esri_features` through duck typing

No DB import, no `app.config.settings`, no filesystem I/O.

### 1.4 Stato che muta / I/O

- **Mutating state (in-memory):**
  - `self._token_cache[cache_key] = {"token", "expires"}` —
    `authenticate` (line 615), `_authenticate_oauth2` (line 652),
    `_authenticate_token` (line 710); cleanup of expired tokens line 607–609.
  - `del self._token_cache[k]` for expired tokens (line 609).
- **I/O network:**
  - `self._http.get(...)` / `self._http.post(...)` from
    `_request_with_token` (line 508/512), `_authenticate_oauth2` (643),
    `_authenticate_token` (700), and many list_*/query_* methods.
- **No filesystem, no DB, no env reads.**

### 1.5 Regression risk hotspots

| Hotspot | Lines | Why |
| --- | --- | --- |
| Token cache key format `f"{server_url}:{username or client_id}"` | 491, 612 | If renamed/changed, refresh on 498/499 could miss the cache → loop or new token per request |
| 401/498/499 retry-once flag `retry_on_401_done` | 493 | Removing or moving outside `while True` causes infinite refresh loop |
| 429 backoff with `attempt`/`backoff_429_count` | 494, ~520–550 | Removing the cap = infinite retry on persistent 429 |
| `params.pop("token")` / `data.pop("token")` defense-in-depth | 486–489 | Removing reopens token-leak vector for legacy callers |
| Cache lookup via `cached["expires"] > now` margin 60s | 614 | Reducing margin causes mid-request token expiry, expanding 401-refresh paths |
| `_write_batch` per-batch `_request_with_token` (vs once-outside) | 1761 | Pre-C12 was `authenticate(conn)` once outside loop; reverting reintroduces "token expires mid-batch → all subsequent batches fail" bug |
| `referer_headers = {"Referer": conn.server_url}` in `_write_batch` | 1738, passed via `extra_headers` | Some Esri Server versions reject Bearer without matching Referer |
| Online vs Server branch in `_authenticate_token` strategies | 678–686 | Adding/removing strategies for one without testing the other regresses one server class |

### 1.6 Refactor candidate (decomposed in small commits)

Recommendation (unchanged): **small refactor — auth submodule extraction**.

Decomposition into 4 commits (each independently revertable, each with
its own test gate):

| Commit | Change | LOC moved | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Move `ArcGISConnectionInfo`, `HttpClientProtocol`, `DefaultHttpClient`, `_mask_secret` to `arcgis_http.py`. Re-export from `arcgis_client` for compat | ~70 LOC | `git revert <c1>` — file is created new, only imports change | `tests2/security/unit/test_arcgis_token_bearer_contract.py` (import via `from app.core.features.arcgis_client import ArcGISConnectionInfo`) deve passare invariato |
| **c2** | Extract `EsriTypeMapper`, `EsriJsonConverter`, `_shapely_to_esri`, `_esri_geometry_type`, `_python_type_to_esri_field`, `_default_drawing_info`, `dataframe_to_esri_features` to `arcgis_esri_json.py` | ~600 LOC | `git revert <c2>` — pure relocation, no behavior change | nessun test diretto su questi simboli; smoke `test_arcgis_online_bearer_smoke.py` (integration, vs ArcGIS Online) per verificare path end-to-end |
| **c3** | Extract auth flow (`authenticate`, `_authenticate_oauth2`, `_authenticate_token`, `_resolve_token_url`, `_discover_token_url`, `_token_cache`) into `ArcGISAuth` class in `arcgis_auth.py`. `ArcGISClient` holds `self._auth = ArcGISAuth(...)` and `_request_with_token` calls `self._auth.token_for(conn)` | ~500 LOC | `git revert <c3>` — risk medium: shared state migration. Mitigation: keep public `client.authenticate(conn)` as thin delegate `return self._auth.token_for(conn)` |  unit test su `_authenticate_token` (manca! aggiungere PRIMA del commit con harness `MagicMock(http_client)`) + `test_arcgis_token_bearer_contract.py` invariato (test header Bearer e' sul `_request_with_token`, non sul token gen) |
| **c4** | Extract write batch loop into `arcgis_write.py` (`add_features`, `update_features`, `delete_features`, `_write_batch`). `ArcGISClient` keeps thin proxy methods | ~250 LOC | `git revert <c4>` — pure relocation | `test_write_batch_refreshes_token_mid_batch_on_498` (line 334 in token_bearer test) deve passare invariato |

After 4 commits, `arcgis_client.py` is reduced from 2476 → ~1100 LOC,
auth is testable in isolation, batch logic is testable in isolation.

**Pre-requisite (NOT a commit, but a gate):** add unit test for
`_authenticate_token` strategies before c3. Today there is **no unit
test** for the referer/requestip strategies — c3 cannot land safely
without it. ~2h test scaffolding.

### 1.7 Stima effort

c1: 1h. c2: 2h. c3 (incl. pre-req test): 4h. c4: 2h. Total: ~9h.

### 1.8 Test gate aggregate

- `test_arcgis_token_bearer_contract.py` (9 test, line 56–334) verde
  invariato dopo ogni commit
- `test_arcgis_online_bearer_smoke.py` (integration) verde con credenziali
- Pre-c3: **add** unit tests for `_authenticate_token` strategies
- Add integration smoke "create_service + add_features + delete_features
  end-to-end" — manca oggi, è il vero gap di copertura del file

---

## 2. `app/core/manipola/transforms/spatial.py` (1941 LOC)

### 2.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Module docstring + imports | 1–77 | shapely fallback flags `HAS_MAKE_VALID`, `HAS_VORONOI`, `HAS_SHAPELY2` |
| `_ensure_active_geometry(gdf)` | 80–144 | helper: garantisce `gdf.geometry` valida, normalizza |
| `_resolve_metric_crs(gdf, metric_crs)` | 146–163 | utility: UTM auto-detect o metric_crs esplicito |
| `_resolve_output_geom_crs(gdf, output_crs)` | 165–184 | utility: stabilisce CRS finale geometrie |
| **32 op_geo_* funzioni** | 186–1941 | catalogo operazioni — vedi tabella sotto |

Operazioni (line ranges + categoria semantica):

| Op | Lines | Category |
| --- | ---:| --- |
| `op_sjoin` | 186–237 | join |
| `op_geo_buffer` | 239–299 | metric |
| `op_geo_centroid` | 301–352 | construct |
| `op_geo_area` | 354–395 | metric |
| `op_geo_clip` | 397–440 | overlay |
| `op_geo_dissolve` | 442–499 | aggregate |
| `op_geo_intersection` | 501–542 | overlay |
| `op_geo_union` | 544–585 | overlay |
| `op_geo_difference` | 587–628 | overlay |
| `op_geo_simplify` | 630–667 | construct |
| `op_geo_convex_hull` | 669–704 | construct |
| `op_geo_reproject` | 706–770 | crs |
| `op_geo_envelope` | 772–807 | construct |
| `op_geo_distance` | 809–873 | metric (multi-input) |
| `op_geo_nearest` | 875–958 | join (multi-input) |
| `op_geo_length` | 960–1001 | metric |
| `op_geo_perimeter` | 1003–1045 | metric |
| `op_geo_explode` | 1047–1080 | construct |
| `op_geo_make_valid` | 1082–1135 | topology |
| `op_geo_within` | 1137–1192 | filter |
| `op_geo_point_on_surface` | 1194–1230 | construct |
| `op_geo_boundary` | 1232–1268 | construct |
| `op_geo_voronoi` | 1270–1342 | construct |
| `op_geo_to_wkt` | 1344–1396 | serialize |
| `op_geo_symmetric_difference` | 1398–1442 | overlay |
| `op_geo_count_points_in_polygons` | 1444–1538 | join |
| `op_geo_clean_topology` | 1540–1653 | topology |
| `op_geo_overlay` | 1655–1717 | overlay |
| `op_geo_bounds_extractor` | 1719–1765 | extract |
| `op_geo_vertex_count` | 1767–1820 | extract |
| `op_geo_line_builder` | 1822–1882 | construct |
| `op_geo_polygon_builder` | 1884–1941 | construct |

### 2.2 Essential call graph

Inbound: nessun chiamante diretto da codice prod. Le funzioni vengono
invocate **dynamically by name** dal registry transforms (vedi
`app/core/manipola/transforms/__init__.py` + `manipola_executor.py`
PluginExecutor).

Internal helpers: solo `_ensure_active_geometry`, `_resolve_metric_crs`,
`_resolve_output_geom_crs` sono richiamati da altre funzioni del file
(da diverse op metriche). Le 32 op sono altrimenti **indipendenti tra
loro**.

### 2.3 External imports

- `pandas`, `geopandas`, `shapely.geometry` (LineString, Polygon)
- `shapely.validation.make_valid` (con fallback runtime)
- `shapely.ops.voronoi_diagram` (con fallback runtime)
- `app.utils.logger.get_logger`
- `app.utils.geo.{to_geodataframe, ensure_same_crs, get_utm_crs_for_geometry}`

Nessun import di `app.db`, `app.config`, `app.services`. Nessun side
effect a livello modulo.

### 2.4 Stato che muta / I/O

- **Nessuna state condivisa fra le funzioni.**
- **Nessun I/O** (no FS, no DB, no rete, no logger persistente — solo
  `logger.warning/info` per debug).
- Le funzioni lavorano su **copie** dei GeoDataFrame (convenzione
  documentata nel docstring del file, riga 14).

### 2.5 Regression risk hotspots

| Hotspot | Lines | Why |
| --- | --- | --- |
| Signature `op_geo_xxx(gdf, **kwargs)` (single) vs `op_geo_xxx(gdfs, **kwargs)` (multi) | varies | Il PluginExecutor chiama per nome; cambiare arity = silent break runtime su uno specifico op |
| Shapely fallback flags `HAS_MAKE_VALID`, `HAS_VORONOI`, `HAS_SHAPELY2` | 55–71 | Rimuoverli rompe ambienti shapely 1.x; codice che dipende e' `op_geo_make_valid` (1082) e `op_geo_voronoi` (1270) |
| `_resolve_metric_crs` riusato da 6 op metriche (buffer, area, length, perimeter, distance, nearest) | 146 | Cambiare semantica = regression a 6 op contemporaneamente |
| `_ensure_active_geometry` riusato da quasi tutte le op | 80 | Stesso ragionamento |

### 2.6 Refactor candidate

Recommendation (unchanged): **no-op.**

Razionale concreto:
- 32 funzioni stateless, indipendenti, signature uniforme;
- 0 source-bound test (vedi 0.5d) → split puro non blocca tests;
- ma il file e' **un catalogo**, non un hot-path. Splittare per famiglia
  (metric/overlay/construct/topology/join/extract/serialize) aumenta il
  numero di file da navigare senza ridurre coupling reale (le funzioni
  non si chiamano fra loro, eccetto i 3 helper);
- benchmark perf gia' presenti (`test_baseline_spatial_ops`,
  `test_bench_*`) → rilevatori di regressione attivi.

**Non c'e' commit decomposition perche' non c'e' refactor approvato.**

Se in futuro si decidesse di splittare:
- c1 estrarre i 3 helper privati in `_spatial_helpers.py` (~80 LOC);
- c2 split per famiglia (5–6 file da ~300 LOC).

Effort stimato (futuro, non ora): ~6h. Payoff: navigabilita' IDE.
**Non vale il churn pre-prod.**

### 2.7 Test gate (corrente, nessun refactor)

- `test_spatial_crs_parameters_contract.py` (19 test, line 74–309) verde
- `test_spatial_error_contract.py` (7 test, line 70–235) verde
- `test_spatial_reproject_source_crs_contract.py` (5 test, line 88–180) verde
- `test_correctness_op_geo_clean_topology.py` (5 test, line 306–381) verde
- `test_correctness_op_geo_voronoi.py` (3 test, line 216–267) verde
- benchmark perf shortlist verde (no regression)

---

## 3. `app/services/file_service.py` (1847 LOC)

### 3.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Docstring + imports | 1–58 | warnings filters, logger fallback |
| Conditional model imports `app.models` | 59–76 | flag `_USE_APP_MODELS` |
| Fallback dataclass `ColumnInfo`, `FileSource` | 78–137 | per test standalone |
| **DI Protocols** | 139–185 | `LoggerProtocol`, `FileSystemProtocol`, `DataLoaderProtocol`, `TimeProviderProtocol` |
| **Default impls** | 188–365 | `NullLogger`, `DefaultTimeProvider`, `DefaultFileSystem`, `DefaultDataLoader` |
| **Pure helper classes** | 367–550 | `FormatDetector` (370), `TypeInferrer` (402), `FileIdGenerator` (520) |
| **`ColumnInfoBuilder`** | 557–620 | static `from_dataframe`, `_extract_geometry_columns` |
| `_rmtree_safe(path, retries=3)` | 629–660 | helper Windows file lock retry |
| **`FileSourceService` class** | **662–1816** | ~1100 LOC, 30+ methods |
| `__init__` | 685–702 | inietta filesystem/loader/logger/time, riferisce `FormatDetector` etc |
| `_generate_file_id`, `_detect_format`, `list_layers`, `list_sheets` | 705–732 | thin wrappers |
| **`_assert_visible(file_id, expected_group_id)`** | **741–823** | **N1 cross-tenant guard** + superadmin sentinel |
| `_load_by_format(file_path, format_type, layer, sheet)` | 825–850 | dispatch per format → DataFrame, GeoDataFrame |
| **`_dispatch_blob_storage(file_id, raw_data, filename)`** | **860–913** | **A1 policy dispatch**: db/object_storage/hybrid + threshold |
| `get_blob_bytes`, `_get_blob_from_db` | 915–972 | retrieve da DB BYTEA o S3 |
| `_temp_file_for(file_id, expected_group_id)` | 974–1044 | estrae blob su disco; SHP/GDB → tmpdir |
| CRUD lookup methods (`get_file_source`, `delete_file_source`, `list_file_sources`) | 1046–1113 | accesso DB |
| **`create_from_upload`** | **1115–1255** | **upload + parse + infer + save**: ZIP→extract→detect format→load→column infer→`_dispatch_blob_storage`→`db.save_file_source` |
| **`create_raw_from_upload`** | **1257–1332** | upload binari/DXF: MIME defense `validate_no_executable` + dispatch + save |
| `_filter_columns` | 1334–1336 | helper |
| **`create_multi_layer_from_upload`** | **1338–1509** | GPKG/GDB/XLSX multi-layer/sheet → N FileSource |
| **`load_dataframe(file_id, layer, sheet, expected_group_id)`** | **1511–1553** | N1 guard + temp extraction + load + cleanup finally |
| `update_file_source` | 1555–1600 | metadata update + re-infer columns |
| `get_layers_info`, `get_preview` | 1602–1676 | discovery + preview |
| **`preview_file_temp(file_content, filename, layer, sheet)`** | **1678–1815** | preview pre-upload con MIME defense |
| `create_file_service` factory | 1821–1830 | DI factory pubblica |
| `_create_production_service` (singleton) | 1835–1846 | + module-level `file_source_service = ...` |

### 3.2 Essential call graph

Inbound (12+):
- **Routers**: `app/core/files/router.py` (CRUD endpoints),
  `app/core/tickets/router.py` (allegati), `app/core/eseguibili/service.py`.
- **Workers**: `workers/tasks/enrichment_task.py:1282` (cleanup temp file
  source via `file_source_service.delete_file_source(fsid)`).
- **Source loaders**: `app/core/artifacts/source_loader.py`.
- **Shared**: `app/shared/path_policy.py` (cooperazione policy),
  `app/db/config_db_service.py`.

Internal:
- `_assert_visible` chiamato da: `_get_blob_from_db` (938),
  `_temp_file_for` (984), `load_dataframe` (1520), `get_layers_info`
  (1609), `get_preview` (1653). **Cinque call site interni.**
- `_dispatch_blob_storage` chiamato da: `create_from_upload` (1229),
  `create_raw_from_upload` (1318), `create_multi_layer_from_upload`
  (linea simile, dentro 1338–1509). **Tre call site interni.**
- `_temp_file_for` chiamato da: `load_dataframe` (1524),
  `get_layers_info` (1616), `get_preview` (indirettamente via
  `load_dataframe`).

### 3.3 External imports

- `pandas`, `geopandas` indiretto via `DefaultDataLoader`
- `app.models.{FileSource, FileSourceCreate, FileSourceUpdate, FileSourceList, FileSourceLayersResponse, FileSourcePreview, ColumnInfo}` (con fallback dataclass)
- `app.db.db_service` (CRUD blob)
- `app.config.settings` (TEMP_DIR, DEFAULT_GROUP)
- `app.shared.sharing.is_resource_visible` (nel `_assert_visible`)
- `app.shared.cloud_backends.get_runtime_object_storage` (S3 dispatch)
- `app.utils.mime_validator.{MimeMismatchError, validate_no_executable}` (defense in depth)
- `app.utils.zip_safe.{safe_extractall, ZipSlipError}` (ZIP slip protection)
- `app.utils.logger.get_logger`
- stdlib: `tempfile`, `zipfile`, `shutil`, `gc`, `pathlib`, `datetime`, `uuid`

### 3.4 Stato che muta / I/O

- **DB writes** (via `app.db.db_service`):
  - `db.save_file_source(...)` da `create_*` (3 entry points) e fallback in
    `update_file_source` (1589).
  - `db.update_file_source_metadata(...)` da `update_file_source` (1584).
  - `db.delete_file_source(file_id)` (riga 1068+).
- **Object storage writes** (via `cloud_backends.get_runtime_object_storage`):
  - `storage.upload_bytes(raw_data, storage_key)` da `_dispatch_blob_storage`
    (linea 902).
  - `storage.download(storage_key, tmp_path)` da `_get_blob_from_db` (linea 960).
- **Filesystem writes**:
  - `tempfile.NamedTemporaryFile(dir=settings.TEMP_DIR)` ovunque negli
    upload paths.
  - `tempfile.mkdtemp(dir=settings.TEMP_DIR)` per ZIP extraction (linea
    1151, 1399, 1016).
  - `_rmtree_safe(...)` cleanup nei finally (5+ siti).
- **DB reads**:
  - `db.get_file_source(file_id, include_data=...)` (5+ siti).
  - `db.list_file_sources(group_id)` (1081).
- **Network reads**: solo download da object storage in
  `_get_blob_from_db`.
- **Module-level state**: `file_source_service` singleton di produzione
  istanziato a fine modulo (`_create_production_service()`).

### 3.5 Regression risk hotspots

| Hotspot | Lines | Why |
| --- | --- | --- |
| `_assert_visible` precedenza sui CRUD | 938, 984, 1520, 1609, 1653 | Skip di un singolo call site = cross-tenant leak (N1 fix) |
| `_dispatch_blob_storage` policy branch order: `db` → `object_storage` → `hybrid` (default) | 881–891 | Cambiare ordine può mandare upload a S3 quando policy=db (orphan blob) |
| Orphan-guard: se `storage.upload_bytes` solleva, **rilancia prima del save_file_source** | 902–908 | Inverting → DB row con `storage_key` puntato a oggetto inesistente |
| `validate_no_executable` posto **prima** di qualsiasi I/O DB/FS | 1290 (raw), 1377 (multi_layer), 1703 (preview) | Spostarlo dopo I/O lascia trasparire payload eseguibili nel temp dir |
| `safe_extractall` (zip-slip) in tutti gli zip path | 1154, 1399, 1018 | Sostituire con `zipfile.extractall` reintroduce zip-slip |
| `_temp_file_for` cleanup nei finally con SHP/GDB → `_rmtree_safe(tmp_path.parent)` | 1540, 1626 | Per non-shp passa `_rmtree_safe(tmp_path)` su file diretto. Sbagliando il branch = leak tmpdir |
| `update_file_source` fallback path 1589: usa `existing_row.group_id` invece di "default" | 1591 | Pre-fix usava "default" → cross-tenant data move silente |
| `gc.collect()` dopo `del df, gdf` in `load_dataframe` finally | 1537 | Necessario per Windows file lock release prima di `_rmtree_safe`; rimuoverlo causa `PermissionError` su rmtree |

### 3.6 Refactor candidate (decomposed in small commits)

Recommendation (unchanged): **medium refactor — split helper classi
prima**.

Decomposition (4 commits):

| Commit | Change | LOC moved | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Move `FormatDetector`, `TypeInferrer`, `FileIdGenerator` to `file_format_helpers.py` and re-export from `file_service` for back-compat | ~150 LOC (370–550) | `git revert <c1>` — pure relocation, re-export preserves imports | `tests2/services/test_type_inferrer_date_contract.py` (11 test, line 46–151) verde con import update |
| **c2** | Move `ColumnInfoBuilder` to `file_column_builder.py` | ~70 LOC (557–620) | `git revert <c2>` | nessun test diretto; smoke `test_file_service_db_contract.py` upload roundtrip |
| **c3** | Move DI Protocols + Default impls (`FileSystemProtocol`/`DataLoaderProtocol`/`Logger`/`TimeProvider` + `Default*`) to `file_service_protocols.py` | ~230 LOC (139–365) | `git revert <c3>` — re-export | `tests2/security/unit/test_file_service_filesystem_contract.py` (3 test, line 18–37) verde |
| **c4** | Move blob storage dispatch (`_dispatch_blob_storage`, `_get_blob_from_db`, `get_blob_bytes`) to `file_service_storage.py` as standalone helpers | ~150 LOC (860–972) | `git revert <c4>` — moderate risk: 3 internal call sites | `tests2/services/test_file_service_policy.py` (6 test, line 103–231) verde + `tests2/services/test_file_service_db_contract.py` policy tests (288–462) verde |

After 4 commits, `file_service.py` ridotto da 1847 → ~1250 LOC; helper
classi diventano genuinamente unit-testable senza dover importare
l'intero service.

**Pre-requisito (NOT a commit):** `_assert_visible` chiamato da 5 call
site interni. Se c4 sposta `_get_blob_from_db` fuori, il chiamante deve
continuare a chiamare `self._assert_visible(...)`. Mitigation: passare
il guard come callable iniettato, oppure mantenere `_assert_visible`
nel service e chiamarlo nel service prima di delegare al modulo storage.
**Decisione architetturale richiesta prima di c4** (~1h design).

### 3.7 Stima effort

c1: 1h. c2: 0.5h. c3: 1h. c4 (incl. design `_assert_visible`): 3h.
Total: ~5.5h.

### 3.8 Test gate aggregate

Tutti i 7 test file esistenti devono passare invariati (eventualmente
con import update). Aggiungere:
- e2e mancante: upload CSV → `get_blob_bytes` → `delete_file_source`
  roundtrip (oggi nessun test fa il giro completo).

---

## 4. `workers/tasks/enrichment_task.py` (1430 LOC)

### 4.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Docstring + imports | 1–43 | celery, logger (open_run/close_run/append_step), `EnrichmentExecutor`, `generate_error_report` |
| **`_merge_batch_parallel_node_results`** | **46–107** | helper module-level, **fail-hard guard** RB.#14 (cardinality mismatch → RuntimeError) |
| **`execute_enrichment_task` Celery task** | **110–1268** | **monolite ~1100 LOC**, 9 parametri |
| ↳ Idempotency guard (run_id ≠ None) | 159–172 | early return `skipped_idempotent` |
| ↳ N9 user re-check | 179–187 | non-eseguibili: `recheck_user_authorized` |
| ↳ group_id resolution (project vs eseguibile) | 211–239 | `get_eseguibile` o `ProjectService` |
| ↳ `open_run` + `set_run_context` | 240–248 | logger run lifecycle |
| ↳ `ApiConnectionService()` LOCAL instance + tenant binding | 252–275 | `_current_group_id` setter PRIMA dei loader |
| ↳ Sources loading (`_load_source_project`, `_load_file_source`, `_load_static_values`) | 297–442 | dispatch loader per node type |
| ↳ Batch parallel via `compute_parallel_groups` + `ThreadPoolExecutor` | 442–710 | parallel/sequential dispatch |
| ↳ `_merge_batch_parallel_node_results` invocation | 712–723 | merge fail-hard |
| ↳ Result final extraction | 725–755 | execution_order traversal |
| ↳ EXECUTABLE file branch (`is_file_result=True`) | 757–991 | rinomina output, valida per estensione, cleanup |
| ↳ DataFrame branch (standard) | 993–1174 | save via `result_store.save_pipeline_result` o `eseguibili.storage.save_bytes` |
| ↳ `save_columns_metadata_with_retry` (B-BE-39) | 1060–1070 | propaga ColumnsMetadataSaveError → task FAILED |
| ↳ Result dict construction (con `result_id`) | 1074–1095 | shape per `/api/tasks/{id}/download` |
| ↳ Intermediate snapshot (`record_final_snapshot`) | 1109–1134 | policy-aware (preview_only default) |
| ↳ `append_step` + `close_run(success=True)` | 1136–1151 | log lifecycle close |
| ↳ `_trigger_carica_if_present` chained | 1156–1169 | duplicato dichiarato di OrchestratorService._execute_carica_if_present |
| ↳ `_cleanup_temp_file_sources` | 1171 | cleanup eseguibili |
| ↳ Global `except Exception as e` handler | 1176–1267 | `_EarlyTaskReturn` detection + close_run(success=False) + error report |
| `_cleanup_temp_file_sources(file_source_ids, task_id)` | 1274–1295 | helper module-level |
| `update_task_progress(task, progress, message, ...)` | 1298–1331 | helper module-level con kwarg `task_id` esplicito (thread-local Celery fix) |
| `_trigger_carica_if_present(project_id, canvas, output_format)` | 1348–1430 | helper module-level (duplicato dichiarato — vedi §4.5) |

### 4.2 Essential call graph

Inbound:
- `app/services/task_service.py:135–138` —
  `execute_enrichment_task.delay(...)` da router `/enrich-async`.
- `app/core/eseguibili/router.py` (publish flow) — indiretto via
  `task_service`.
- `app/core/imdf/router.py` — alcuni flussi IMDF — indiretto.

Internal (lazy imports dentro `execute_enrichment_task`):
- `app.shared.idempotency.is_already_completed` (160)
- `app.shared.async_user_recheck.recheck_user_authorized` (180)
- `app.core.eseguibili.db.get_eseguibile` (217)
- `app.services.project_service.ProjectService` (229+)
- `app.core.intermediate.db` (open/close run, write_node) (228+)
- `app.core.arricchisci.api_connector.ApiConnectionService` (258)
- `app.core.arricchisci.parallel.{compute_parallel_groups, should_execute_node}` (297)
- `app.core.arricchisci.enrichment_service.EnrichmentExecutor` + `generate_error_report` (40, top-level)
- `app.utils.dataframe.save_dataframe` / `save_dataframe_to_bytes` (1018)
- `app.utils.result_store.save_pipeline_result` (1043)
- `app.shared.columns_metadata.save_columns_metadata_with_retry` (1061)
- `app.core.intermediate.snapshot.record_final_snapshot` (1111)
- `app.core.intermediate.writer.close_run` (1129, 1187, 1205)
- `app.utils.logger.{open_run, close_run, set_run_context, append_step}`
- `app.core.eseguibili.storage.save_bytes` (1017+)

I lazy import sono **deliberati** (evitano circular import in worker
boot e isolano test che mockano singoli moduli).

### 4.3 External imports

Top-level: `os`, `time`, `json`, `threading`, `typing`, `datetime`,
`celery.current_task`, `workers.celery_app.celery_app`,
`app.config.settings`, `app.utils.logger.*`,
`app.core.arricchisci.enrichment_service.{EnrichmentExecutor, generate_error_report}`,
`app.core.arricchisci.models.{EnrichmentOptions, EnrichmentResult}`.

### 4.4 Stato che muta / I/O

- **In-memory state** locale al task: `node_results` dict, `enrichment_stats`,
  `error_report_file`, `_run_id`, `_group_id`. Nessuno stato globale modulo.
- **DB writes (intermediate)**: `open_run`, `close_run`, `append_step` →
  PostgresLogHandler tabella `logs`, `runs`, `steps`. Più
  `intermediate.writer.close_run` per `intermediate.runs`.
- **DB writes (results)**: via `result_store.save_pipeline_result` →
  tabella `results` (BYTEA o S3 dispatch).
- **DB writes (project)**: `save_columns_metadata_with_retry` →
  `projects.columns_metadata`.
- **DB reads**: `ProjectService.get_project_metadata`,
  `eseguibili.db.get_eseguibile`, `idempotency.is_already_completed`,
  `async_user_recheck.recheck_user_authorized`.
- **Object storage writes**: `eseguibili.storage.save_bytes` (ramo
  `is_eseguibile`).
- **Filesystem writes**: `save_dataframe` su tmpdir per gpkg/shp
  (linea 1022 con `TemporaryDirectory(prefix="urby_eseg_gpkg_shp_")`).
- **Network**: tutte le chiamate API enrichment (via
  `ApiConnectionService._execute_*` / `_load_*`).
- **Redis writes**: `task.update_state(state="PROGRESS", meta=...)` via
  `update_task_progress`.

### 4.5 Regression risk hotspots

| Hotspot | Lines | Why |
| --- | --- | --- |
| Idempotency guard ORDER: `if run_id is not None: is_already_completed(run_id)` PRIMA di N9 e tutto il resto | 159 | Spostarlo dopo N9 = retry esegue auth flow inutilmente; spostarlo dopo `open_run` = orphan run nei retry idempotent |
| N9 re-check skip per is_eseguibile | 179 | Eseguibili usano token separato; rimuovere lo skip = 401 spuri |
| `ApiConnectionService()` LOCAL (NON singleton) | 258–259 | Singleton condivide `_current_group_id` fra task concorrenti = race cross-tenant (RB tenant binding) |
| `_current_group_id` settato PRIMA del primo loader (verifica posizionale AST nel test #2) | 261–275 | Loader fail-closed `Contesto di sicurezza non determinabile` se group_id mancante |
| `_merge_batch_parallel_node_results` fail-hard (RB.#14) | 712–723 + helper 46–107 | Re-introdurre `try/except` ampio = silent corruption (PDF senza arricchimenti, success=True) |
| `ColumnsMetadataSaveError` propagation | 1063 | Helper propaga su non-transient → Celery FAILED. Rimuovere = task SUCCESS con metadata stale |
| `result_id` propagato in entrambi i rami (DataFrame + file_result) | 1082, 1094 (file_result branch ~770), 1141 (sample) | Senza, `/api/tasks/{id}/download` cade su 410 Gone (orchestrator/router.py:580) |
| `update_task_progress(self, ..., task_id=task_id)` esplicito | 197+ ovunque | `self.request.id` è thread-local. Nei thread spawned dal `ThreadPoolExecutor` (riga 700+) cade su None → `task_id must not be empty` |
| `_trigger_carica_if_present` duplicato di `OrchestratorService._execute_carica_if_present` | 1348+ vs `app/core/orchestrator/service.py:184` | **Bug-magnete documentato**: ogni fix dev'essere applicato in due posti |
| `is_eseguibile` branch per `_log_pid` (192) | 192 | Eseguibili NON propagano project_id ai logger (no traccia persistente in `logs.project_id`); rimuovere lo skip = trace leak |
| `_async_close` thread daemon per `intermediate.writer.close_run` | 1124–1134 | Daemon thread non aspetta join: SIGKILL del worker durante un `close_run` = run intermediate appeso |

### 4.6 Refactor candidate (decomposed in small commits)

Recommendation (unchanged): **medium refactor — extract 3 primitives +
dedup `_trigger_carica_if_present`**.

Decomposition (4 commits, **richiede prerequisito harness Celery — vedi
0.5d wave 4**):

| Commit | Change | LOC moved | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c0** (prereq) | Build harness `tests2/_helpers/celery_eager.py` con eager_mode + spy hooks (no behavior change) | nuovo file ~150 LOC | `git revert <c0>` | n.a. (no production code change) |
| **c1** | Extract `_resolve_run_context(run_id, user_id, project_id, is_eseguibile, task_id) → (run_id_effective, group_id, early_return_or_none)` (idempotency + N9 + group_id resolution) | ~110 LOC (159–239) | `git revert <c1>` — moderate risk: 3 security guards spostati insieme | `tests2/arricchisci/unit/test_enrichment_task_idempotency_contract.py` (3 test, line 21–49) modernizzato + harness; `test_enrichment_task_tenant_binding_lock.py` test eseguibile branch (#225) modernizzato |
| **c2** | Dedup `_trigger_carica_if_present` con `OrchestratorService._execute_carica_if_present` → entrambi delegano a un helper condiviso `app/shared/carica_chain.py:trigger_carica_for_canvas(...)` | ~80 LOC (1348–1430) + ~80 LOC orchestrator | `git revert <c2>` — bassa risk: pure dedup | aggiungere unit test su helper condiviso (~3h test scaffolding); orchestrator integration test e enrichment integration test |
| **c3** | Extract `_finalize_run(task_id, run_id, success, stats, ...)` (`append_step` + `close_run` + cleanup file_source ids) | ~70 LOC (1136–1172 + handler 1237+) | `git revert <c3>` — moderate risk: 2 close_run path (success/failure) | end-to-end run completion test sull'harness |

`c0` è prerequisito **bloccante** per c1. Senza, i ~10 test del file
`test_enrichment_task_tenant_binding_lock.py` (33 inspect.getsource refs)
si rompono e devono essere modernizzati ad-hoc per ogni commit.

### 4.7 Stima effort

c0: 5h (harness reusable per altri task tests). c1: 4h. c2: 6h. c3: 3h.
Total: **~18h**.

Pre-requisito test modernization (vedi 0.5d wave 4): ~9h.

### 4.8 Test gate aggregate

- Pre-c1: `test_enrichment_task_idempotency_contract.py`,
  `test_enrichment_task_tenant_binding_lock.py`,
  `test_enrichment_task_columns_metadata_propagation.py` modernizzati e verdi.
- `test_batch_parallel_merge_contract.py` (6 test, line 60–208) verde
  invariato (testa helper module-level, non `execute_enrichment_task`).
- `test_node_may_explode_cardinality_contract.py` (12 test, line 49–279)
  verde invariato (testa `parallel.py`).
- `test_init_execution_context_fail_closed.py` (5 test, line 37–126)
  verde invariato (testa `api_connector`, NON enrichment_task).
- `test_generate_error_report_secret_redaction.py` (6 test, line 46–177)
  verde invariato (testa `enrichment_service`).
- e2e: arricchimento minimale → run_id closed con success=True, result_id
  presente, no orphan in `intermediate.runs`. **Manca oggi.**

---

## 5. `app/core/scheduler/execution_manager.py` (1125 LOC)

### 5.1 Internal map (line ranges)

| Block | Lines | Purpose |
| --- | --- | --- |
| Docstring + imports | 1–47 | psycopg2, logger, scheduler models, sub-services (schedule, log, concurrency, policy) |
| **`ExecutionManager` class** | **48–1118** | ~1070 LOC, 12 metodi |
| `__init__` | 63–72 | wire schedule_service, log_service, concurrency, policy, lazy connection_service, artifact_store |
| `_get_connection_service` | 74–79 | lazy singleton |
| `_get_orchestrator` | 81–112 | full DI assembly OrchestratorService (ProjectService, QueryExecutor, QueryGenerator, FeaturesService) |
| **`_start_lock_heartbeat(schedule_id, interval)`** | **117–209** | thread daemon + lock_lost detection (refresh False → re-acquire fail → flag) |
| **`_stop_lock_heartbeat(state)`** | **211–226** | stop event + join timeout 5s, idempotente |
| **`execute(schedule_id, triggered_by, retry_attempt, scheduled_for)`** | **232–753** | **~520 LOC, single hot path** |
| ↳ Carica config + project_meta validation | 262–349 | orphan, group isolation fail-closed |
| ↳ `open_run` + `set_run_context` | 351–360 | dopo le validazioni (no orphan run su validation fail) |
| ↳ Concurrency `try_acquire` (skipped/queued/acquired) | 362–388 | early return per skip/queue |
| ↳ `set_runtime_status("running")` | 391 | mutation DB |
| ↳ `_start_lock_heartbeat` | 397 | lancia daemon thread |
| ↳ try block: pre-check + orchestrator.execute_pipeline + carica | 399–537 | core flow |
| ↳ Material via `ArtifactResolver().materialize_run(ref)` if no output_path | 458–489 | fallback per arricchisci che salva blob diretto |
| ↳ `_artifact_store.archive(...)` | 494 | copia in cartella timestamped |
| ↳ `_execute_carica(config, result, scheduled_for)` if config.destination | 513 | entry to CARICA |
| ↳ Success branch: policy.decide_after_success → schedule_service.update_runtime → log_service → close_run → return dict (con `lock_lost`) | 538–625 | con propagation lock_lost da heartbeat_state |
| ↳ except: ExecutionError vs Exception (sanitize err_msg per ramo unknown) | 627–668 | err_msg = stringa canonica per `Exception` ramo |
| ↳ finally: `_stop_lock_heartbeat` + retry wrapper release | 670–753 | budget N=3 / T=10s / backoff exponential / jitter ±20% / classes narrow `psycopg2.OperationalError` + `DeadlockDetected` |
| `_pre_check_connection(config)` | 759–785 | test connection destination |
| **`_execute_carica(config, pipeline_result, scheduled_for)`** | **791–953** | **~163 LOC**: `ArtifactResolver` → `CaricaExecutor.execute(...)` → traduzione WriteResult |
| **`_handle_failure(config, schedule_id, ..., retry_attempt, error_message, steps, start_time, run_id, scheduled_for)`** | **959–1118** | **~160 LOC**: policy.decide_after_failure → log + close_run(failure) + DebugPayloadBuilder + retry schedulato (apply_async countdown) |
| `class ExecutionError(Exception)` | 1120–1124 | domain exception (str(e) safe nel ramo execute) |

### 5.2 Essential call graph

Inbound:
- `workers/tasks/schedule_task.py:98` — `manager = ExecutionManager()`
  → `manager.execute(schedule_id, triggered_by, retry_attempt, scheduled_for)`
- `workers/beat_scheduler.py` — indiretto (spawn schedule_task)

Internal:
- `execute()` chiama: `_start_lock_heartbeat`, `_stop_lock_heartbeat`,
  `_pre_check_connection`, `_execute_carica`, `_handle_failure`,
  `_get_orchestrator`, `_get_connection_service`.
- `_handle_failure()` chiama: `schedule_service`, `log_service`,
  `policy`, `close_run`, `schedule_task.apply_async`.
- `_execute_carica()` chiama: `ArtifactResolver().materialize_run`,
  `CaricaExecutor.execute`, `_get_connection_service`.

Sub-services iniettati in `__init__`:
- `ScheduleService` (per `get_schedule`, `update_runtime`, `mark_as_orphan`,
  `set_runtime_status`)
- `ScheduleLogService` (per `create_log_entry`, `write_log`)
- `ConcurrencyManager` (per `try_acquire`, `release`, `refresh_lock`)
- `PolicyEngine` (per `decide_after_success`, `decide_after_failure`)
- `ScheduleArtifactStore` (per `archive(...)`)

Lazy imports dentro `execute()` / `_execute_carica()`:
- `app.core.artifacts.{ArtifactResolver, ArtifactResolutionError, PipelineArtifact}`
- `app.core.carica.{executor.CaricaExecutor, models.WriteStatus}`
- `app.utils.dataframe.load_dataframe_from_file`
- `app.core.intermediate.db.close_connection` (finally cleanup)
- `app.db.config_db_service.close_connection` (finally cleanup)
- `workers.tasks.schedule_task.schedule_pipeline_task` (retry path)
- `app.utils.debug_payload_builder.DebugPayloadBuilder`

### 5.3 External imports

Top-level: `random`, `threading`, `time`, `uuid`, `datetime`, `pathlib`,
`typing`, `psycopg2`, `psycopg2.errors`, `app.config.settings`,
`app.utils.logger.*`, scheduler models + sub-services.

### 5.4 Stato che muta / I/O

- **DB writes (state machine schedule)**:
  - `schedule_service.set_runtime_status(schedule_id, "running")` (391, 1053)
  - `schedule_service.update_runtime(schedule_id, status=..., last_execution=...)` (545)
  - `schedule_service.mark_as_orphan(schedule_id)` (296)
- **DB writes (logs)**:
  - `log_service.create_log_entry(...)` (5+ siti)
  - `log_service.write_log(entry)` (5+ siti)
  - `close_run(run_id, status=..., ...)` (595, 1036)
  - `open_run(run_id=...)` (353)
- **Redis writes (lock)**:
  - `concurrency.try_acquire(schedule_id, max_queue_size)` (363)
  - `concurrency.release(schedule_id)` (703 dentro retry loop)
  - `concurrency.refresh_lock(schedule_id)` (159 dentro heartbeat thread)
- **Filesystem writes**:
  - `_artifact_store.archive(...)` copia output in cartella timestamped
  - `shutil.copy2(m.path, dest_path)` materializzazione blob (475)
- **Thread spawn**: `_start_lock_heartbeat` lancia thread daemon (208).
- **Network**: `_pre_check_connection` testa connection destination via
  `connection_service.test_connection`.
- **Celery dispatch**: `schedule_pipeline_task.apply_async(...)` per retry
  (1067 in `_handle_failure`).

### 5.5 Regression risk hotspots

| Hotspot | Lines | Why |
| --- | --- | --- |
| `open_run` posto DOPO le validazioni project_meta + group isolation | 351–360 (preceded by 286–349) | Pre-fix: open_run prima delle validazioni → orphan run "running" forever su validation fail |
| Group isolation fail-closed `proj_group_id != schedule_group_id` | 340–349 | Skip = cross-tenant pipeline execution |
| Concurrency state machine: try_acquire → "skipped"/"queued"/"acquired" → 3 return path distinti | 363–388 | "queued" deve ritornare success=True (il countdown task gestirà) altrimenti retry loop |
| `_start_lock_heartbeat` PRIMA del try block, `_stop_lock_heartbeat` PRIMO nel finally | 397, 671–676 | Invertendo: heartbeat continua a rinfrescare un lock già rilasciato (race) |
| Retry release wrapper: classes narrow `psycopg2.OperationalError + DeadlockDetected` con budget N=3 ∧ T=10s | 706–725 | Allargare la classe = retry su bug logici (loop). Rimuovere budget = hang scheduler |
| `_stop_lock_heartbeat` join timeout 5s | 226 | Senza timeout, finally può bloccarsi indefinitamente (refresh_lock appeso su DB) → Celery task non termina |
| `lock_lost` flag propagato a result success E failure | 605–625, 645–667 | Skip propagation in failure branch = operatore non distingue fail "pulito" da fail "con concorrenza potenziale" |
| `err_msg` sanitization sul ramo Exception (non-domain) | 631–644 | `str(e)` di eccezioni unknown ANTE-fix finiva user-facing via `_handle_failure` → leak DB internals |
| Outer `try/except Exception as release_err` su finally release | 732–739 | Senza, eccezione non-transient propaga oltre finally → Celery FAILED-with-exception → retry duplicato |
| `_handle_failure` retry path: `apply_async(args=[schedule_id], kwargs={..., scheduled_for}, countdown=...)` propaga scheduled_for | 1067–1071 | N15: senza, retry perde tick logico → guard idempotency CARICA non scatta → duplicati su append |
| Idempotency CARICA: `run_id = f"schedule-{config.id}-{scheduled_for}-carica"` se scheduled_for | 900–903 (in `_execute_carica`) | None se scheduled_for None → guard no-op (residuo documentato) |
| `intermediate.db.close_connection()` + `config_db_service.close_connection()` nel finally | 744–753 | Senza cleanup, file descriptor exhaustion sul worker dopo molte run + SQLITE_BUSY |

### 5.6 Refactor candidate (decomposed in small commits)

Recommendation (unchanged): **small refactor — heartbeat context manager**.

Decomposition (3 commits):

| Commit | Change | LOC | Rollback | Test gate |
| --- | --- | --- | --- | --- |
| **c1** | Introdurre `LockHeartbeat` context manager class in `execution_manager.py` (o `_lock_heartbeat.py`). `_start_lock_heartbeat` / `_stop_lock_heartbeat` rimangono come thin wrapper (back-compat) | ~100 LOC nuovi + ~120 LOC riarrangiati | `git revert <c1>` — i metodi originali restano | tutti 7 i test in `test_execution_manager_heartbeat_contract.py` (line 41–137) verdi invariati: testano semantica `_start/_stop` non come implementati internamente |
| **c2** | `execute()` usa il context manager: `with self._lock_heartbeat(schedule_id) as hb: ... hb.lock_lost`. Rimuove try/finally manuale per heartbeat | ~30 LOC modificati (397, 671–676) | `git revert <c2>` — moderate risk: il finally ora è implicito | `test_execution_manager_lock_lost_contract.py` (5 test, line 53–137) verde; `test_execution_manager_lock_lost_propagation_contract.py` (3 test, line 24–58) — questi sono source-bound (`inspect.getsource(ExecutionManager.execute)`), richiedono modernizzazione **prima** del commit (vedi 0.5d wave 2) |
| **c3** | (Opzionale, post-prod) `_execute_carica` extraction in `CaricaSchedulerExecutor` class in nuovo modulo `app/core/scheduler/carica_runner.py` | ~163 LOC (791–953) | `git revert <c3>` — moderate risk | `test_artifact_resolution_contract.py` (2 test, line 45–90) verde con import update; `test_execution_manager_contract.py` test su `_execute_carica` (line 49–77) verdi |

**c2 è bloccato** dal modernizzazione di `lock_lost_propagation` test
prima del commit (3 test source-bound che leggono direttamente
`inspect.getsource(ExecutionManager.execute)` per cercare la stringa
`heartbeat_state["lock_lost"]`). Vedi 0.5d wave 2 (effort 3h).

### 5.7 Stima effort

c1: 4h. c2: 3h (incl. modernizzazione 3 test wave 2 — vedi 0.5d).
c3 (opzionale, post-prod): 4h. Total wave-1: ~7h.

### 5.8 Test gate aggregate

- `test_execution_manager_heartbeat_contract.py` (7 test) verde invariato
- `test_execution_manager_lock_lost_contract.py` (5 test) verde invariato
- `test_execution_manager_lock_lost_propagation_contract.py` (3 test)
  modernizzato + verde (prerequisito c2)
- `test_execution_manager_orphan_and_release_contract.py` (3 test, line
  101–242) verde (behavior-bound, sopravvivono al refactor)
- `test_execution_manager_release_retry_wrapper.py` (7 test, line
  105–290) verde (behavior-bound)
- `test_execution_manager_contract.py` (6 test, line 49–142) verde
- `test_execution_manager_err_msg_sanitize.py` (4 test, line 47–141) —
  source-bound, modernizzazione opzionale (non blocca c1/c2/c3)
- aggiunta e2e: schedule trigger → run completion + assert lock_lost,
  scheduled_for, no leak internals nel result dict

---

## 6. Ranking Finale (invariato)

| Rank | File | Refactor | Effort | Rischio | Pre-requisiti test |
| ---:| --- | --- | ---:| --- | --- |
| 1 | `execution_manager.py` | Heartbeat context manager | ~7h | basso | 0.5d wave 2 (3 test, 3h) |
| 2 | `enrichment_task.py` | Extract 3 primitives + dedup carica | ~18h | medio | 0.5d wave 4 (harness + 13 test, ~9h) |
| 3 | `arcgis_client.py` | Auth submodule extraction | ~9h | basso | nessuno (test già behavior-bound) + add `_authenticate_token` unit test (2h) |
| 4 | `file_service.py` | Split helper classi + storage dispatch | ~5.5h | basso | nessuno (test già behavior-bound) |
| 5 | `spatial.py` | nessuno | — | — | n.a. |

### 6.1 Cosa NON toccare ora

- **Pre-prod (5 maggio)**: nessuno dei 5 refactor. Anche quelli a 0h
  modernizzazione test (rank 3, 4) hanno rischio non zero e l'utility
  marginale rispetto al costo opportunità (debug deploy, env, IaC).
- **`spatial.py`**: il prompt vieta esplicitamente "split perché grande".
  Qui non c'è coupling reale: 32 funzioni indipendenti, 0 source-bound
  test, 0 callers diretti, nessuno stato condiviso.
- **`enrichment_task.py` extract** prima dell'harness Celery e
  modernizzazione dei 13 test (wave 4, ~9h prerequisito).

---

## 7. Stato Phase 0.5a (rev. evidence-deep)

**Status: analysis only. No code change. No test change.**

Output:
- internal map line-ranged per i 5 file
- call graph essenziale (inbound + internal + lazy imports)
- external dependencies enumerate
- I/O & state mutation sites enumerated
- regression hotspots con rationale
- refactor decomposition in commit piccoli con rollback e test gate
  per-commit
- ranking invariato

Pending Phase 0:
- 0.5b — frontend wide audit
- 0.5c — Q1–Q4 SQL queries (`compat_residue_evidence_0_5c.md`)
- 0.5f — perf baseline
- 0.5g — UX / product readiness

---

*End of Phase 0.5a (rev. evidence-deep).*
