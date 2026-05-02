# Code Map

Bibbia operativa del repo. Una sezione per modulo: dove entrare, file principali con funzioni/classi reali, keywords per `rg`, dove intervenire e quali confini non rompere.

Indice rapido:
- Backend core: [admin_backups](#admin_backups), [admin_system_status](#admin_system_status), [arricchisci](#arricchisci), [artifacts](#artifacts), [auth](#auth), [carica](#carica), [connections](#connections), [datasources](#datasources), [eseguibili](#eseguibili), [estrai](#estrai), [features](#features), [files](#files), [imdf](#imdf), [intermediate](#intermediate), [logs](#logs), [manipola](#manipola), [orchestrator](#orchestrator), [projects](#projects), [scheduler](#scheduler), [tickets](#tickets)
- Backend shared: [shared/crypto](#sharedcrypto), [shared/storage-results](#sharedstorageresults)
- Frontend: [pages](#frontendpages), [api](#frontendapi), [store](#frontendstore), [components](#frontendcomponents)

Nota: file in ASCII puro (no accenti, no caratteri Unicode speciali) per evitare mojibake su viewer non-UTF8.

---

## `backend/app/core/admin_backups/` <a id="admin_backups"></a>

Entry-point: `backend/app/core/admin_backups/router.py`

Responsabilita: governa endpoint admin per listare/triggerare backup applicativi e leggerne lo stato.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `list_backups_endpoint`, `backup_status_endpoint`, `backup_schedule_endpoint`, `backup_trigger_endpoint` | contratto API admin/backups |
| `service.py` | `list_backups`, `get_last_run_status`, `get_schedule_info`, `is_manual_backup_running`, `trigger_manual_backup`, `AdminBackupDependencyError` | logica I/O sui file di backup, gating dipendenze |

Keywords: `list_backups`, `trigger_manual_backup`, `get_last_run_status`, `AdminBackupDependencyError`

Intervento/confini: intervenire su router/service per nuove operazioni admin. Non chiamare da pipeline runtime ne da scheduler; questi endpoint sono lato admin.

---

## `backend/app/core/admin_system_status/` <a id="admin_system_status"></a>

Entry-point: `backend/app/core/admin_system_status/router.py`

Responsabilita: aggrega stato sistema (DB pool, Redis, workers, host, blacklist, tickets) per la dashboard admin.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `get_system_status` | unico endpoint aggregato |
| `service.py` | `get_backup_status`, `get_verify_restore_status`, `get_schedules_status`, `get_workers_status`, `get_host_resources`, `get_db_pool_status`, `get_redis_status`, `get_blacklist_status`, `get_tickets_status`, `get_db_schema_size`, `get_system_status` | probe specifici per ogni area |

Keywords: `get_system_status`, `get_workers_status`, `get_db_pool_status`, `get_redis_status`, `get_blacklist_status`

Intervento/confini: aggiungere nuovi probe in `service.py` come funzioni `get_*_status` e includerli nell'aggregato. Sola lettura: non scrivere stato applicativo da qui.

---

## `backend/app/core/arricchisci/` <a id="arricchisci"></a>

Entry-point: `backend/app/core/arricchisci/router.py`

Responsabilita: governa l'enrichment di dataset via API esterne, con parallelismo per gruppi indipendenti, `execute_if` per-riga e gestione credenziali API cifrate.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_arricchisci`, `_check_api_connection_ownership`, `_check_project_ownership`, `fetch_openapi_schema`, `FetchSchemaRequest`, `ValidateRequest` | contratto API arricchisci, ownership |
| `enrichment_service.py` | (orchestrator-class, 502 righe post-Phase-5-B3-A, -75% vs baseline 2019) — `EnrichmentExecutor(7 mixin)` shell + `__init__` (orchestrator constructor) + `execute` (entry point dispatcher) + `_update_progress` (utility cross-mixin singleton) + 3 auth helper stub di delega a `app.shared.api_utils` (`_prepare_headers`, `_get_oauth2_token`, `_get_oauth2_client_credentials_token`) + module-level `generate_error_report` (back-compat con worker + test). RateLimiter re-exported per back-compat | entry point + dispatch + utility cross-mixin |
| `_rate_limiter.py` | `RateLimiter` (token bucket thread-safe in-memory, B3-A.1) | rate limiting standalone class |
| `enrichment_template_params.py` | `EnrichmentTemplateParamsMixin` (6 metodi: `_is_template`, `_template_columns`, `_interpolate_template`, `_build_params`, `_has_null_params`, `_make_cache_key`) | template parsing + params building + cache keying (B3-A.2) |
| `enrichment_response_processing.py` | `EnrichmentResponseProcessingMixin` (9 metodi: `_extract_output`, `_explode_rows`, `_auto_flatten`, `_detect_geometry_type`, `_infer_scalar_type`, `_flatten_dict`, `_apply_result`, `_resolve_nested_list`, `_extract_json_value`) | response → DataFrame rows transformation (B3-A.3) |
| `enrichment_batch_mode.py` | `EnrichmentBatchModeMixin` (4 metodi: `_execute_batch_native`, `_call_batch_api`, `_build_batch_payload`, `_extract_batch_output`) | batch native API path + 429+Retry-After + payload format dispatcher (B3-A.4) |
| `enrichment_validation_preview.py` | `EnrichmentValidationPreviewMixin` (2 metodi: `validate_input`, `preview`) | preflight validation + preview campione (B3-A.5) |
| `enrichment_retry_http.py` | `EnrichmentRetryHttpMixin` (1 metodo: `_call_api_with_retry`) | HTTP per-row retry chain + 429+Retry-After + ArcGIS token injection (B3-A.6) |
| `enrichment_execution_engines.py` | `EnrichmentExecutionEnginesMixin` (3 metodi: `_execute_parallel`, `_execute_parallel_async`, `_execute_internal`) | runtime engines (sync ThreadPoolExecutor / async asyncio.Semaphore / sequential per-row) (B3-A.7) |
| `enrichment_grouping.py` | `EnrichmentGroupingMixin` (2 metodi: `_group_by_params`, `_count_unique_combinations`) | raggruppamento parametri unici per cache_key MD5 (B3-A.8) |
| `executor.py` | `EnrichExecutor` | wrapper di livello pipeline |
| `parallel.py` | `compute_parallel_groups`, `should_execute_node`, `get_execute_mask`, `_extract_consumed_columns`, `_extract_produced_columns`, `execute_parallel_enrichers` | parallelizzazione + execute_if |
| `api_connector.py` | (orchestrator/core, 669 righe post-Phase-5-B3-B, -77.6% vs baseline 2992) — `ApiConnectionService(5 mixin)` shell + `__init__` + `execute_enrichment` (entry point orchestrator) + `_init_execution_context` (tenant binding) + `_get_execution_order` (Kahn topological sort) + `_run_single_enricher` (generator vs enricher dispatch) + `_is_file_source_for_file_processor` (passthrough branch logic) + `_prepare_headers` (3-line shim, source-bound test patch) + B3-B.1 helper re-export + back-compat `save_dataframe`/`save_result_blob`/`get_temp_output_path` imports | entry point + orchestrator + tenant context |
| `_api_connection_helpers.py` | `_AUTH_SENSITIVE_FIELDS`, `_encrypt_auth_in_config`, `_decrypt_auth_in_config`, `_nd` (module-level helpers, B3-B.1) | crypto auth utilities + node-data accessor standalone |
| `api_connection_management.py` | `ApiConnectionManagementMixin` (11 metodi: CRUD `_mask_credentials`, `list_api_connections`, `list_api_connections_decrypted`, `get_api_connection`, `create_api_connection`, `update_api_connection`, `delete_api_connection` + TEST_CONNECTION `_build_test_call_params`, `_inject_auth_query_params`, `_parse_test_response_json`, `test_api_connection`) | CRUD lifecycle + interactive test (B3-B.2b) |
| `api_connection_support.py` | `ApiConnectionSupportMixin` (9 metodi: FIELD_INTROSPECTION 5 `_extract_available_fields`, `_extract_geojson_fields`, `_is_geometry`, `_get_field_type`, `_get_sample_value` + RESULT_BUILDING 4 `_find_final_result`, `_build_file_result_response`, `_save_and_build_dataframe_response`, `_write_intermediate_snapshot`) | JSON response field analysis + execute_enrichment helpers (B3-B.3) |
| `api_connection_source_loading.py` | `ApiConnectionSourceLoadingMixin` (7 metodi: SOURCE_LOADING 6 `_assert_source_project_in_tenant`, `_recompute_upstream_project`, `_filesystem_fallback_load_project`, `_load_source_project`, `_load_file_source`, `_load_static_values` + SSRF 1 `_validate_outbound_url` `@staticmethod`) | tenant-fail-closed source loading + outbound URL guard (B3-B.4b) |
| `api_connection_rest_file.py` | `ApiConnectionRestFileMixin` (10 metodi: REST_FILE PIPELINE 6 `_parse_http_error_body`, `_download_binary_response`, `_build_rest_file_multipart`, `_parse_rest_file_config`, `_rest_file_error`, `_execute_rest_file` + FILE_SOURCE RESOLUTION 4 `_assert_file_source_group_access`, `_resolve_file_source_id_path`, `_resolve_file_source_path`, `_resolve_input_path_for_file_processor`) | file-to-file HTTP multipart + file_source materialization (B3-B.5b) |
| `api_connection_generator_enricher.py` | `ApiConnectionGeneratorEnricherMixin` (9 metodi: API GENERATOR 5 `_paginate_generator_results`, `_substitute_url_path_params` `@staticmethod`, `_call_generator_endpoint`, `_build_generator_base_params`, `_execute_api_generator` + API ENRICHER 3 `_extract_rows_from_response`, `_enrich_one_row`, `_execute_api_enricher` + JSON UTIL 1 `_extract_json_value`) | DataFrame from JSON + row-by-row enrichment + JSON path delegate (B3-B.6) |
| `models.py` | `RowStatus`, `BatchInputFormat`, `BatchConfig`, `RowError`, `EnrichmentStats`, `EnrichmentOptions`, `EnrichmentResult`, `PreviewResult`, `EnrichmentValidationResult` | shape stato/configurazione enrichment |

Keywords: `EnrichmentExecutor`, `ApiConnectionService`, `RateLimiter`, `compute_parallel_groups`, `should_execute_node`, `_encrypt_auth_in_config`, `EnrichmentResult`, `EnrichmentTemplateParamsMixin`, `EnrichmentResponseProcessingMixin`, `EnrichmentBatchModeMixin`, `EnrichmentValidationPreviewMixin`, `EnrichmentRetryHttpMixin`, `EnrichmentExecutionEnginesMixin`, `EnrichmentGroupingMixin`, `ApiConnectionManagementMixin`, `ApiConnectionSupportMixin`, `ApiConnectionSourceLoadingMixin`, `ApiConnectionRestFileMixin`, `ApiConnectionGeneratorEnricherMixin`

Punti di intervento tipici:
- nuova auth API (es. OAuth flow) -> `_api_connection_helpers.py` (`_encrypt_auth_in_config`/`_decrypt_auth_in_config`) + `api_connection_management.py` (CRUD lifecycle) + `models.py`
- nuovo CRUD/test API connection -> `api_connection_management.py` (`ApiConnectionManagementMixin`)
- nuovo introspection JSON / result building per `execute_enrichment` -> `api_connection_support.py` (`ApiConnectionSupportMixin`)
- tenant binding / source loading / SSRF guard -> `api_connection_source_loading.py` (`ApiConnectionSourceLoadingMixin`)
- nuovo connector REST file-to-file / file_source resolution -> `api_connection_rest_file.py` (`ApiConnectionRestFileMixin`)
- nuova strategia generator/enricher / pagination / JSON path -> `api_connection_generator_enricher.py` (`ApiConnectionGeneratorEnricherMixin`)
- modifiche all'orchestrator `execute_enrichment` o `_init_execution_context` -> `api_connector.py` (core)
- nuova policy di parallelizzazione/dipendenze tra nodi -> `parallel.py` (`compute_parallel_groups`)
- nuovo formato risposta API -> `enrichment_response_processing.py` (`EnrichmentResponseProcessingMixin`)
- regola `execute_if` per-riga -> `parallel.py` (`get_execute_mask`, `should_execute_node`)
- nuovo template params helper -> `enrichment_template_params.py`
- nuovo retry chain HTTP -> `enrichment_retry_http.py` (per-row) o `enrichment_batch_mode.py` (batch path)
- nuova strategy parallelismo -> `enrichment_execution_engines.py` (sync/async/sequential)
- nuovo preflight check / preview -> `enrichment_validation_preview.py`
- nuovo grouping/dedup helper -> `enrichment_grouping.py`
- modifiche a `execute()` o `__init__` -> `enrichment_service.py` (orchestrator core)

Intervento/confini: I/O dataset solo via `intermediate`. Auth API solo cifrata via `shared/crypto.py`. Nuove regole di parallelizzazione/`execute_if` vanno in `parallel.py`, non duplicarle nel router.

**MRO post-Phase-5-B3-A** (decisione architetturale ratificata B3-A.8):
```
EnrichmentExecutor
  → EnrichmentTemplateParamsMixin
  → EnrichmentResponseProcessingMixin
  → EnrichmentBatchModeMixin
  → EnrichmentValidationPreviewMixin
  → EnrichmentRetryHttpMixin
  → EnrichmentExecutionEnginesMixin
  → EnrichmentGroupingMixin
  → object
```
27 metodi distribuiti su 7 mixin specializzati per dominio + 1 standalone class (`RateLimiter`). `inspect.getsource(EnrichmentExecutor.<method>)` legge il source dal mixin tramite `__code__.co_filename` + line numbers. Le call-site `self.<method>(...)` interne (cross-mixin) risolvono via MRO senza modifiche.

**Logger naming contract** (uniforme per tutti i mixin che loggano + core class): tutti i moduli che emettono log usano esplicitamente `get_logger("app.core.arricchisci.enrichment_service")` (canonical pattern, parita' B2 carica/writer). I 2 mixin che NON loggano (`EnrichmentValidationPreviewMixin`, `EnrichmentGroupingMixin`) NON definiscono `logger` — documentato esplicitamente nei docstring. `StructuredLogger` wrapper di `app/utils/logger.py` delega a `_logger.log(...)` sul logger stdlib omonimo (singleton per nome) → handler/caplog agganciati nei test catturano emit indistintamente da quale modulo li produce.

**Residui intenzionali in `enrichment_service.py` core (post-B3-A.8)**:
- `__init__` (~80 body) — orchestrator constructor (instance attrs `api_config`, `param_mapping`, `auth`, `options`, `_rate_limiter`, `_cache`, ecc.)
- `execute` (~112 body) — entry point pubblico dispatcher (sync/async/parallel/sequential branch)
- `_update_progress` (~15 body) — utility cross-mixin singleton (pattern `_oracle_setup` di B2 carica/writer)
- 3 auth helper stub (`_prepare_headers`, `_get_oauth2_token`, `_get_oauth2_client_credentials_token`, ~17 totali) — 3-line di delega a `app.shared.api_utils`, infra-class
- `generate_error_report` (~52 body, module-level) — utility back-compat per `workers/tasks/enrichment_task.py` + `tests2/arricchisci/unit/test_generate_error_report_secret_redaction.py`
- `RateLimiter` re-exported via `from app.core.arricchisci._rate_limiter import RateLimiter` (back-compat)

**Cleanup B3-A.8**: rimosso module-level wrapper `_geojson_to_wkt` di `enrichment_service.py` (5 righe). Era dead code post-B3-A.3: `EnrichmentResponseProcessingMixin` ha sua copia locale per evitare circular import; nessun consumer esterno verificato.

**MRO post-Phase-5-B3-B** (decisione architetturale ratificata B3-B.7):
```
ApiConnectionService
  → ApiConnectionManagementMixin       (B3-B.2b)
  → ApiConnectionSupportMixin          (B3-B.3)
  → ApiConnectionSourceLoadingMixin    (B3-B.4b)
  → ApiConnectionRestFileMixin         (B3-B.5b)
  → ApiConnectionGeneratorEnricherMixin (B3-B.6)
  → object
```
46 metodi distribuiti su 5 mixin specializzati per dominio + 1 standalone helpers module (`_api_connection_helpers.py` con `_AUTH_SENSITIVE_FIELDS` constant + `_encrypt_auth_in_config` + `_decrypt_auth_in_config` + `_nd`). `inspect.getsource(ApiConnectionService.<method>)` legge il source dal mixin tramite `__code__.co_filename`. Le call-site `self.<method>(...)` interne (cross-mixin) risolvono via MRO senza modifiche. Cross-mixin INVERSO documentato: `test_api_connection` (mgmt) → `_substitute_url_path_params` (gen_enr); `_execute_rest_file` (rest_file) → `_validate_outbound_url` (source_loading); `_execute_api_generator/enricher` (gen_enr) + `_execute_rest_file` (rest_file) + `test_api_connection` (mgmt) → `_prepare_headers` (core).

**Logger naming contract** (uniforme per tutti i mixin che loggano + core class): tutti i moduli che emettono log usano esplicitamente `get_logger("app.core.arricchisci.api_connector")` (canonical pattern, parita' B2/B3-A). `StructuredLogger` wrapper di `app/utils/logger.py` delega a `_logger.log(...)` sul logger stdlib omonimo (singleton per nome) → handler/caplog agganciati nei test catturano emit indistintamente da quale mixin module li produce. `_assert_source_project_in_tenant` usa kwarg `op="load_source_project"` (StructuredLogger interface).

**Residui intenzionali in `api_connector.py` core (post-B3-B.9, 669 linee finali)**:
- `__init__` (~3 body) — orchestrator constructor (instance attrs `data_dir`, `results_dir` da `settings`)
- `execute_enrichment` (~194 body) — entry point pubblico orchestrator (boundary safety-net per ValueError/Exception domain-safe → response user_error)
- `_init_execution_context` (~37 body) — tenant binding + preview_limit setup (fail-closed via `ExecutionContextError`)
- `_get_execution_order` (~32 body) — topological sort algoritmo Kahn
- `_run_single_enricher` (~16 body) — dispatch generator vs enricher
- `_is_file_source_for_file_processor` (~25 body) — branch logic file_source passthrough vs `_load_file_source`
- `_prepare_headers` (~3 body) — 3-line stub di delega a `_shared_prepare_headers` (`app.shared.api_utils`); RESTA in core perche' `tests2/security/unit/test_temp_lifecycle_core.py:325` patch `app.core.arricchisci.api_connector._shared_prepare_headers` source-bound. Estrazione richiede test modernization in initiative dedicata (carry-over Run 0/5).
- B3-B.1 helper re-export: `_AUTH_SENSITIVE_FIELDS`, `_encrypt_auth_in_config`, `_decrypt_auth_in_config`, `_nd` per back-compat con direct imports `from app.core.arricchisci.api_connector import _decrypt_auth_in_config` (test_api_connection_decrypt_policy) e `_nd` (test_api_connector_contract).
- Singleton `api_connection_service = ApiConnectionService()` (linea finale) — usato da `app/main.py`, `executor.py`, `router.py`, `eseguibili/scanner.py`.

**Cleanup B3-B.7** (post-cluster extraction):
- 9 stdlib imports orphan rimossi: `io`, `os`, `re`, `json`, `uuid`, `datetime`, `timezone`, `Path`, `requests` (tutti con body migrato in mixin).
- 9 app imports orphan rimossi: `_shared_extract_json_value`, `_shared_get_oauth2_token`, `_shared_get_oauth2_cc_token`, `sanitize_for_log`, `load_dataframe_from_file`, `filter_visible_columns`, `find_latest_output_file`, `process_static_fields`, `ResponseParser`/`get_preset_config`, `FileSourceResolutionError`.
- 2 dead-code stub OAuth rimossi (prova forte: 0 self-call sites, 0 external refs, 0 test patches, 0 direct imports): `_get_oauth2_client_credentials_token` + `_get_oauth2_token` (3 linee ognuno; EnrichmentService class ha le sue copie separate in `enrichment_service.py:424,428`).

**Bugfix security B3-B.8** (`app/shared/api_utils.py:validate_external_url`, commit `be987d1`):
- DNS resolution via `socket.getaddrinfo(host, None)` invece di `gethostbyname_ex` per coprire ANCHE record AAAA (IPv6). Pre-fix: hostname che risolveva esclusivamente a IPv6 privato/loopback/link-local (`fc00::/7`, `::1`, `fe80::/10`) bypassava silenziosamente il check (fail-OPEN).
- Aggiunto blocco `ip.is_unspecified` per `0.0.0.0` / `::` (edge case minor).
- Interfaccia preservata: `raise ValueError`, loopback opt-in `PLENORA_ALLOW_LOOPBACK_OUTBOUND` + alias retro-compat `URBY_ALLOW_LOOPBACK_OUTBOUND`, hardened guard `ENVIRONMENT in {production/prod/staging/stage}`.
- 19 nuovi test cases B3-B.8 in `tests2/security/unit/test_validate_external_url_contract.py`: IPv6 loopback/private/link-local/multicast/ULA + `0.0.0.0` + `::` + dedup + opt-in IPv6 loopback + opt-in non sblocca IPv6 ULA.
- NON modificato `_validate_outbound_url` (B3-B.4b mixin, return-string interface). Carry-over Run 0/5: consolidamento architetturale single-source con wrapper raise-mode.

**Cleanup B3-B.9 dead patches** (commit `cac2d20`):
- Rimosse 3 patches dead in `tests2/arricchisci/unit/test_api_connector_execute_enrichment_lock.py`: `save_dataframe` (0 usages), `save_result_blob` (3 usages dead by indirection — sum `call_count + save_blob_bytes.call_count` con `save_blob.call_count` sempre 0 + 2 `return_value =` su mock mai chiamato), `get_temp_output_path` (0 usages).
- Conseguentemente rimossi 3 import back-compat fragili da `api_connector.py` (`save_dataframe`, `save_result_blob`, `get_temp_output_path` con `# noqa: F401`).
- Test modernizzati: `env.save_blob_bytes.call_count == 1` (rinforzato da somma generica), 2 `return_value = ` ridotti al solo canonical path (`save_blob_bytes`).
- Per scenari path-based futuri (gpkg/shp): patchare canonical `app.utils.result_store.save_result_blob`, NON l'alias api_connector ormai rimosso.

**Test modernizations B3-B** (necessarie pre-extraction per evitare source-bound patch breakage):
- **B3-B.2a**: `tests2/arricchisci/unit/test_api_connector_test_api_connection_lock.py` modernizzato da `patch("app.core.arricchisci.api_connector.logger")` source-bound a handler stdlib agganciato al logger canonico per nome. Pattern behavior-bound (singleton stdlib per nome → record indistinguibili dai consumer).
- **B3-B.4a**: `tests2/security/unit/test_api_connector_source_loading_core.py` modernizzato da `patch("app.core.arricchisci.api_connector.filter_visible_columns", ...)` (alias re-export) a `patch("app.utils.dataframe.filter_visible_columns", ...)` (source canonico). Identita' simbolica preservata; patch ora resiliente all'extraction.
- **B3-B.5a**: `tests2/arricchisci/unit/test_api_connector_rest_file_error_taxonomy.py` (×2) + `tests2/security/unit/test_temp_lifecycle_core.py` (×1) modernizzati da `patch("app.core.arricchisci.api_connector.requests.post", ...)` a `patch("requests.post", ...)` (source canonico — `requests.post is requests.api.post`, intercept funziona via module binding).

**Behavior-equivalent rewrite dichiarato B3-B.5b**: 8 forward-ref `ApiConnectionService._rest_file_error(...)` riscritti come `self._rest_file_error(...)` in `_build_rest_file_multipart` (×4) e `_parse_rest_file_config` (×4). Necessario per evitare import circolare nel mixin module (api_connector importa il mixin a module-load, prima che `ApiConnectionService` sia definita). Runtime-identico per staticmethod via descriptor protocol. Documentato esplicitamente nel docstring di `ApiConnectionRestFileMixin`.

**Carry-over fuori scope Phase 5** (futura nuova Phase 0/5):
- Redis-distributed rate limiter: il `RateLimiter` corrente e' in-memory thread-safe. Sostituzione con Redis-based per distributed scenarios e' miglioramento architetturale, non refactor behavior-preserving.
- Retry per-row vs batch retry consolidation: `_call_api_with_retry` (`EnrichmentRetryHttpMixin`) e `_call_batch_api` (`EnrichmentBatchModeMixin`) condividono retry chain pattern (max_retries + 429 + retry_on_status + exponential backoff) ma con body distinto (per-row index vs batch_indices[0], path params URL substitution vs payload builder). Consolidamento candidato in helper standalone `_retry_http_machinery`.

---

## `backend/app/core/artifacts/` <a id="artifacts"></a>

Entry-point: `backend/app/core/artifacts/resolver.py`

Responsabilita: risolve riferimenti ad artifact prodotti dalle run (consumer-side); modulo di supporto, niente endpoint propri.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `resolver.py` | `ArtifactResolver` | risoluzione riferimenti -> artifact materializzati |
| `source_loader.py` | `SourceLoader` | caricamento sorgenti per gli artifact |
| `types.py` | `ArtifactResolutionError`, `SourceLoadingError`, `RunArtifactRef`, `PipelineArtifact`, `SourceRef`, `LatestProjectArtifactRef`, `RunBlob`, `MaterializedArtifact` | tipi/contratti artifact |

Keywords: `ArtifactResolver`, `RunArtifactRef`, `PipelineArtifact`, `SourceLoader`, `MaterializedArtifact`, `LatestProjectArtifactRef`

Intervento/confini: nuovi tipi di riferimento in `types.py`, nuova logica di risoluzione in `resolver.py`. Trattare gli artifact come read-only lato consumer; non duplicare logica scheduler.

---

## `backend/app/core/auth/` <a id="auth"></a>

Entry-point: `backend/app/core/auth/auth.py`

Responsabilita: governa login, JWT, MFA, rate-limit IP, dipendenze RBAC e middleware request-id; espone `shares_router` per ownership/sharing.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `auth.py` | `LoginRequest`, `TokenResponse`, `CurrentUser`, `_check_rate_limit`, `_record_failed_login`, `_clear_failed_logins`, `hash_password`, `verify_password`, `needs_rehash`, `create_access_token`, `decode_token`, `get_current_user`, `require_user_or_above`, `require_not_esecutore`, `require_admin` | login, JWT, dipendenze RBAC |
| `auth_middleware.py` | `JWTMiddleware`, `_clear_request_state` | middleware JWT su tutte le route |
| `mfa.py` | `MFAEnrollment`, `is_enabled`, `generate_enrollment`, `verify_code`, `verify_enrollment`, `disable`, `status`, `_row_for`, `_iso` | enrollment/verify TOTP |
| `request_id_middleware.py` | `RequestIdMiddleware` (BaseHTTPMiddleware) | iniezione X-Request-ID nei log per ogni request |
| `shares_router.py` | `SharesResponse`, `SetSharesRequest`, `_validate_resource_type`, `_load_resource_owner_group`, `_check_owner_access`, `get_shares`, `set_shares`, `add_share`, `remove_share` | gestione condivisioni risorse |

Keywords: `JWTMiddleware`, `RequestIdMiddleware`, `MFAEnrollment`, `verify_code`, `_check_rate_limit`, `require_admin`, `shares_router`

Intervento/confini: segreti solo via `shared/crypto.py` (mai env diretta). Nuovi router devono passare per `JWTMiddleware`; le dipendenze RBAC sono `require_*`, non saltarle. Niente fetch credenziali in chiaro.

---

## `backend/app/core/carica/` <a id="carica"></a>

Entry-point: `backend/app/core/carica/router.py`

Responsabilita: governa scrittura DataFrame su DB target con DDL automatica, bulk insert, conflict-policy, idempotency e auditing.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_carica_service`, `AutoMappingRequest` | contratto API carica |
| `executor.py` | `CaricaExecutor`, `_resolve_aliases_in_mappings`, `_dict_to_conn_obj`, `_short_err` | orchestrazione di una scrittura |
| `database_writer.py` | (orchestrator-class, 460 righe post-Phase-5-B2, -81% vs baseline 2443) — `DatabaseWriter(DataPrepMixin, SchemaIntrospectionMixin, TransactionExecutionMixin, ReplaceStrategiesMixin, WriterPreflightLifecycleMixin, BatchDispatchMixin, DestinationWriter)`. Resta nel ruolo orchestrator: `__init__` + `write` (entry point) + `_dispatch_write_mode` (orchestrator interno) + `_oracle_setup` (utility cross-mixin chiamata da TransactionExecutionMixin + ReplaceStrategiesMixin via `self._oracle_setup` MRO; cross-mixin → resta in core class) | entry point + dispatch |
| `writer_base.py` | `DestinationWriter` (base) | interfaccia writer |
| `writer_data_prep.py` | `DataPrepMixin` (4 metodi: `_prepare_dataframe`, `_fix_mixed_type_columns`, `_geometry_to_wkt`, `_build_params`) | data transformation pre-write (B2.1) |
| `writer_introspection.py` | `SchemaIntrospectionMixin` (6 metodi: `table_exists`, `get_existing_columns`, `get_schemas`, `get_tables`, `_table_exists_for`, `_compute_schema_hints`) | schema introspection DB-side + DataFrame-side (B2.2) |
| `writer_transaction_exec.py` | `TransactionExecutionMixin` (4 metodi: `_execute_in_batches`, `_execute_all_or_nothing`, `_execute_per_batch`, `_execute_batch`) | batch execution con strategia transazionale (B2.3) |
| `writer_replace_strategies.py` | `ReplaceStrategiesMixin` (6 metodi: `_truncate_insert_atomic`, `_truncate_insert_stage_swap`, `_truncate_insert_oracle_redef`, `_replace_atomic`, `_mssql_replicate_schema_to_shadow`, `_generate_single_rename`) | replace/truncate strategies + helper SQL (B2.4b) |
| `writer_preflight_lifecycle.py` | `WriterPreflightLifecycleMixin` (8 metodi: `validate`, `_check_create_permission`, `_assert_safe_write_config`, `_build_initial_result`, `check_conflicts`, `create_table`, `drop_table`, `truncate_table`) | preflight validation + DDL lifecycle (B2.5b) |
| `writer_batch_dispatch.py` | `BatchDispatchMixin` (3 metodi: `_batch_insert`, `_batch_update`, `_batch_upsert`) | dispatch per write mode + delegate a `_execute_in_batches` (B2.6) |
| `bulk_writers.py` | `bulk_insert`, `is_bulk_enabled`, `BulkInsertNotSupportedError` | path bulk + telemetry |
| `ddl_generator.py` | `DDLGenerator` | DDL automatico da DataFrame |
| `column_mapper.py` | `ColumnMapper` | mapping colonne sorgente->destinazione |
| `conflict_checker.py` | `ConflictChecker`, `_serialize_value` | rilevazione conflitti pre-write |
| `idempotency.py` | `is_already_completed` (re-export da `app.shared.idempotency`) | dedup tra retry |
| `models.py` | `WriteMode`, `WriteStatus`, `TransactionMode`, `ConflictPolicy`, `ColumnMapping`, `WriteConfig`, `BatchProgress`, `ConflictDetail`, `ConflictReport`, `WriteResult`, `AuditRecord` | enum/contratti scrittura |

Keywords: `CaricaExecutor`, `DatabaseWriter`, `DDLGenerator`, `ColumnMapper`, `ConflictChecker`, `WriteConfig`, `ConflictPolicy`, `bulk_insert`, `DataPrepMixin`, `SchemaIntrospectionMixin`, `TransactionExecutionMixin`, `ReplaceStrategiesMixin`, `WriterPreflightLifecycleMixin`, `BatchDispatchMixin`

Punti di intervento tipici:
- nuovo dialect/destination -> sottoclasse di `DestinationWriter` in `writer_base.py` + registrazione in `executor.py`
- nuova `ConflictPolicy` -> enum in `models.py` + branch in `ConflictChecker` e `BatchDispatchMixin._batch_upsert`
- DDL su nuovo tipo colonna -> `DDLGenerator` (mapping tipo) + `ColumnMapper`
- regressione bulk -> `bulk_writers.py` (`bulk_insert`, `is_bulk_enabled`) e telemetry whitelist
- nuovo data-prep step -> `writer_data_prep.py` (`DataPrepMixin`)
- nuova introspection schema -> `writer_introspection.py` (`SchemaIntrospectionMixin`)
- nuova transaction strategy -> `writer_transaction_exec.py` (`TransactionExecutionMixin`)
- nuova replace strategy -> `writer_replace_strategies.py` (`ReplaceStrategiesMixin`)
- nuova preflight check / DDL lifecycle -> `writer_preflight_lifecycle.py` (`WriterPreflightLifecycleMixin`)
- nuovo write mode helper -> `writer_batch_dispatch.py` (`BatchDispatchMixin`)
- modifiche a `_dispatch_write_mode` o `write` -> `database_writer.py` (orchestrator core)

Intervento/confini: leggere dataset solo da `intermediate`. Credenziali via `connections`+`shared/crypto`. Preservare `ConflictPolicy` e idempotency keys; nuovi writer estendono `writer_base.DestinationWriter`. Non bypassare `ConflictChecker` quando policy != overwrite.

**MRO post-Phase-5-B2** (decisione architetturale ratificata B2.6):
```
DatabaseWriter
  → DataPrepMixin
  → SchemaIntrospectionMixin
  → TransactionExecutionMixin
  → ReplaceStrategiesMixin
  → WriterPreflightLifecycleMixin
  → BatchDispatchMixin
  → DestinationWriter (ABC)
  → object
```
31 metodi distribuiti su 6 mixin specializzati per dominio. `inspect.getsource(DatabaseWriter.<method>)` legge il source dal mixin tramite `__code__.co_filename` + line numbers. Le call-site `self.<method>(...)` interne (sia da `_dispatch_write_mode` sia inter-mixin) risolvono via MRO senza modifiche.

**Logger naming contract** (uniforme per tutti i 6 mixin + core class): tutti i moduli usano esplicitamente `get_logger("app.core.carica.writer")` (NOT `__name__`). `StructuredLogger` wrapper di `app/utils/logger.py` delega a `_logger.log(...)` sul logger stdlib omonimo (singleton per nome) → handler/caplog agganciati nei test catturano emit indistintamente da quale modulo li produce.

**Test contract modernization** (3 commit B2):
- **B2.0** (`test_database_writer_log_consistency_contract.py`): `monkeypatch.setattr(dw_module, "logger", ...)` → handler stdlib agganciato al logger canonico per nome (cattura emit dei mixin); `inspect.getsource(dw_module)` (cleanup guards) → `inspect.getsource(DatabaseWriter._method)` per ciascuno dei 3 metodi caller.
- **B2.4a** (`test_database_writer_bulk_integration_contract.py:test_stage_swap_callsite_propagates_stg_table_to_bulk`): `ast.parse(database_writer.py)` → `ast.parse("class _S:\n" + inspect.getsource(method))` per ciascuno dei 3 metodi (dedent via class scaffold per i body con SQL multiline).
- **B2.5a** (`test_unsafe_append_gate_contract.py:test_unsafe_append_combo_with_flag_proceeds`): `monkeypatch.setattr(dw_mod, "logger", ...)` → handler stdlib agganciato al logger canonico per nome (stesso pattern B2.0).

Tutti i 3 test contract ora behavior-bound (non source-location-bound): indipendenti dalla posizione fisica dei metodi. Anti-fragili rispetto a future estrazioni / refactor.

---

## `backend/app/core/connections/` <a id="connections"></a>

Entry-point: `backend/app/core/connections/router.py`

Responsabilita: governa configurazione connessioni DB e credenziali cifrate; espone test connessione e introspezione tabelle.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_connections`, `create_connection`, `list_connections`, `update_connection`, `delete_connection`, `test_connection`, `get_connection_tables` | contratto API connessioni |
| `models.py` | `DatabaseConnection`, `ConnectionCreate`, `ConnectionUpdate`, `ConnectionList`, `ConnectionTestResult` | shape dati e campi credenziali |

Keywords: `DatabaseConnection`, `ConnectionCreate`, `ConnectionTestResult`, `test_connection`, `connection_id`

Intervento/confini: intervenire su router/models per contratto API. Non leggere credenziali in chiaro; passare da `backend/app/shared/crypto.py`. Non duplicare logica datasource.

---

## `backend/app/core/datasources/` <a id="datasources"></a>

Entry-point: `backend/app/core/datasources/router.py`

Responsabilita: governa adapter per local/FTP/SFTP/SharePoint dietro un'interfaccia comune, con factory e CRUD configurazioni.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_datasources`, `_ds_error_to_http`, `create_datasource`, `list_datasources`, `get_datasource`, `update_datasource` | contratto API datasource |
| `service.py` | `DataSourceService` | servizio CRUD persistito |
| `interface.py` | `DataSourceInterface`, `BackendConfig`, `FileInfo`, `DataSourceError`, `DataSourceConnectionError`, `DataSourceFileNotFoundError`, `DataSourcePermissionError` | contratto adapter + errori tipati |
| `factory.py` | `create_backend`, `supported_types` | dispatch tipo->adapter |
| `local.py` | `LocalDataSource`, `_classify_path_for_multi_host` | adapter FS locale |
| `ftp.py` | `FTPDataSource`, `_sanitize_ftp_error` | adapter FTP |
| `sftp.py` | `SFTPDataSource`, `_import_paramiko`, `_sanitize_sftp_error` | adapter SFTP |
| `sharepoint.py` | `SharePointDataSource`, `_import_office365`, `_sanitize_sharepoint_error` | adapter SharePoint |
| `models.py` | `DataSource`, `DataSourceCreate`, `DataSourceUpdate`, `DataSourceList` | shape API |

Keywords: `DataSourceInterface`, `create_backend`, `BackendConfig`, `FTPDataSource`, `SFTPDataSource`, `SharePointDataSource`, `LocalDataSource`

Intervento/confini: nuovi adapter implementano `DataSourceInterface` e si registrano in `factory.create_backend`. FS solo via `path_policy`; credenziali via `connections`+`shared/crypto`. Errori adapter devono usare `DataSourceError*`.

---

## `backend/app/core/eseguibili/` <a id="eseguibili"></a>

Entry-point: `backend/app/core/eseguibili/router.py`

Responsabilita: governa catalogo e lancio di eseguibili registrati a partire da progetti, con storage isolato e analisi degli upload.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `_check_group_access`, `_check_visible`, `list_eseguibili`, `create_eseguibile`, `get_eseguibile`, `update_eseguibile`, `delete_eseguibile` | contratto API eseguibili |
| `service.py` | `create_from_project`, `analyze_file_upload`, `execute` | creazione, analisi input, esecuzione |
| `scanner.py` | `extract_expected_columns`, `_expand_mapping_columns` | colonne attese dal canvas |
| `storage.py` | `save_bytes`, `save`, `find_keys`, `open_stream`, `delete_by_task` | storage isolato per task |
| `db.py` | `create_eseguibile`, `get_eseguibile`, `list_eseguibili`, `update_eseguibile`, `delete_eseguibile`, `list_eseguibili_by_source` | persistenza catalogo |
| `models.py` | `ExpectedColumn`, `EseguibileCreate`, `EseguibileUpdate`, `EseguibileSummary`, `EseguibileDetails`, `ColumnMappingEntry`, `ExecuteRequest`, `AnalyzeUploadResponse` | contratti |

Keywords: `create_from_project`, `analyze_file_upload`, `extract_expected_columns`, `EseguibileDetails`, `save_bytes`

Intervento/confini: non eseguire binari fuori catalogo. Storage solo via `storage.py`. Tenere isolato dalla pipeline ETL principale (orchestrator).

---

## `backend/app/core/estrai/` <a id="estrai"></a>

Entry-point: `backend/app/core/estrai/router.py`

Responsabilita: governa estrazione da DB sorgente verso DataFrame, con catalog di funzioni e translate di espressioni.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `TranslateRequest`, `TranslateResponse`, `get_catalog`, `translate_expression` | contratto API estrai |
| `executor.py` | `EstraiExecutor`, `_traduci_errore_sql`, `_is_user_error_message` | esecutore + sanitize errori SQL |
| `query_generator.py` | `QueryGenerator` | costruzione SQL da nodo canvas |
| `database_inspector.py` | `DatabaseInspector`, `DatabaseConnection`, `DatabaseConnectorProtocol`, `ConnectionFactoryProtocol`, `DefaultConnectionFactory`, `SQLQueries`, `TypeMapper`, `ResultProcessor`, `ColumnInfo` | introspezione schema + mapping tipi |

Keywords: `EstraiExecutor`, `QueryGenerator`, `DatabaseInspector`, `TypeMapper`, `_traduci_errore_sql`, `TranslateRequest`

Intervento/confini: output sempre via `intermediate`. Credenziali via `connections`. Non accoppiare a UI o orchestrator. Errori SQL devono passare per `_traduci_errore_sql` (sanitize).

---

## `backend/app/core/features/` <a id="features"></a>

Entry-point: `backend/app/core/features/routes.py`

Responsabilita: governa servizi ArcGIS Feature: CRUD connessioni, browse services/folders/layers/schema, query preview, write features e create service.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `routes.py` | `get_service`, `_mask_secrets`, `_check_arcgis_owner`, `_check_arcgis_access`, `create_connection`, `list_connections`, `update_connection`, `delete_connection`, `test_connection`, `browse_services`, `list_folders`, `browse_layers`, `get_layer_schema`, `query_preview`, endpoint `/write` e `/create-service` | contratto API + ownership/masking |
| `features_service.py` | `FeaturesService`, `create_features_service`, `create_connection_db`, `list_connections_db`, `get_connection_db`, `update_connection_db`, `delete_connection_db`, `_row_to_connection`, `_generate_id`, `SaveFunctionProtocol` | logica servizio + persistenza |
| `arcgis_client.py` | `ArcGISClient`, `EsriTypeMapper`, `EsriJsonConverter`, `DefaultHttpClient`, `HttpClientProtocol`, `ArcGISConnectionInfo`, `_mask_secret`, `create_client` | client ArcGIS REST + conversioni Esri |
| `models.py` | `ArcGISConnection`, `ArcGISCreate`, `ArcGISUpdate`, `ArcGISList`, `ArcGISTestResult`, `FeatureWriteRequest`, `WriteFeaturesResponse`, `CreateServiceResponse`, `BrowseServicesResponse`, `ListFoldersResponse`, `BrowseLayersResponse` | contratti request/response |

Keywords: `FeaturesService`, `ArcGISClient`, `WriteFeaturesResponse`, `CreateServiceResponse`, `ArcGISConnection`, `EsriTypeMapper`, `browse_layers`

Intervento/confini: rispettare contratto `WriteFeaturesResponse`/`CreateServiceResponse` (consumati dai test e dal frontend). Token come `?token=` query param, non header. Secret via `shared/crypto`; serializzazione output va sempre via `_mask_secrets`.

---

## `backend/app/core/files/` <a id="files"></a>

Entry-point: `backend/app/core/files/router.py`

Responsabilita: governa upload, preview, listing e delete dei file utente con ownership e path-policy.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `_sanitize_upload_filename`, `_assert_path_within`, `_check_file_ownership`, `preview_file_temp`, `upload_file`, `upload_multi_layer`, `list_files`, `get_file`, `delete_file` | contratto API + validazioni FS |
| `models.py` | `FileSource`, `FileSourceList` | shape API |

Keywords: `upload_file`, `upload_multi_layer`, `_check_file_ownership`, `_assert_path_within`, `FileSource`, `_sanitize_upload_filename`

Intervento/confini: accesso FS solo via `path_policy` + `_assert_path_within`. Non aggirare ownership/sharing (controlli `require_file_owner`/`_check_file_ownership`).

---

## `backend/app/core/imdf/` <a id="imdf"></a>

Entry-point: `backend/app/core/imdf/router.py`

Responsabilita: governa pipeline IMDF (DXF -> IMDF/IFC/GDB) e pubblicazione su ArcGIS via bundle, con validation e storage isolato per task.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `_safe_bundle_dir`, `_hydrate_local_cache`, `_assert_bundle_group_access`, `ImdfPublishRequest`, `ImdfValidateRequest`, `ImdfPublishResponse`, `ImdfBundleSummary`, `publish_imdf`, `validate_publish_endpoint`, `get_bundle_metadata`, `list_bundles`, `delete_bundle` | contratto API IMDF + ownership bundle |
| `loader.py` | `ImdfBundle`, `extract_imdf_zip`, `_build_bundle_index`, `load_imdf_layers` | parsing bundle ZIP IMDF |
| `validator.py` | `validate_publish`, `_count_features_where_via_post`, `_coarse_type_from_esri`, `_coarse_type_from_dtype` | validazione pre-publish (lazy-import simboli da `arcgis_uploader` via facade) |
| `arcgis_uploader.py` | (orchestrator + facade, 573 righe post-Phase-5-B4, -56% vs baseline 1315) — `upload_imdf_layers` (orchestrator end-to-end) + costanti `LAYER_ORDER` / `DEFAULT_MATCH_COLUMNS` / `PUBLISH_MODES` + facade re-export di tutti gli helper privati distribuiti nel package `arcgis/` | entry point semantico publish ArcGIS |
| `arcgis/__init__.py` | (package marker vuoto, no auto-import per evitare cicli) | sub-package cluster split B4 |
| `arcgis/sql_helpers.py` | `_quote_sql_value`, `_chunked`, `_query_oid_by_key` | quoting SQL ArcGIS-safe + chunking + batch OBJECTID resolution (B4.1) |
| `arcgis/service_discovery.py` | `_list_user_folders`, `_ensure_folder`, `_is_service_name_available`, `_enumerate_all_user_items`, `_search_user_items`, `_find_existing_service` | folder + Feature Service discovery/lookup (B4.2) |
| `arcgis/service_lifecycle.py` | `_try_cleanup_zombie_service`, `_delete_existing_service` | rimozione/cleanup FS (B4.3, include bugfix `UnboundLocalError` contatori) |
| `arcgis/publish_ops.py` | `_add_layer_to_service`, `_publish_mode_existing` | scrittura layer-level su FS: replace + append/upsert/update (B4.4) |
| `persist.py` | `persist_imdf_bundle_to_disk`, `_count_features` | persistenza bundle su disco |
| `storage.py` | `_storage`, `_zip_key`, `_meta_key`, `_local_dir` | storage isolato per task |

Keywords: `upload_imdf_layers`, `validate_publish`, `persist_imdf_bundle_to_disk`, `ImdfPublishRequest`, `_publish_mode_existing`, `extract_imdf_zip`, `arcgis_uploader`, `arcgis/`

Punti di intervento tipici:
- nuovo layer IMDF da pubblicare -> `loader.py` (`load_imdf_layers`) + `arcgis/publish_ops.py` (`_add_layer_to_service`)
- regressione publish "esistente" -> `arcgis/publish_ops.py` (`_publish_mode_existing`) + `arcgis/sql_helpers.py` (`_query_oid_by_key`)
- nuovo helper di discovery/lookup ArcGIS -> `arcgis/service_discovery.py` (mai aggiungere a `arcgis_uploader.py` direttamente; il facade re-exporta solo)
- nuovo helper di cleanup/delete -> `arcgis/service_lifecycle.py`
- nuovo helper SQL/quoting -> `arcgis/sql_helpers.py`
- check pre-publish (count/types) -> `validator.py` (`validate_publish`)
- gestione bundle su disco/cleanup -> `persist.py` + `storage.py`

Intervento/confini: pubblicazione ArcGIS solo via `features` (HTTP client) — non chiamare ArcGIS REST direttamente da qui se non tramite il client. Preservare contratto bundle/validation. Bundle access via `_assert_bundle_group_access`. **`arcgis_uploader.py` resta entry point semantico (orchestrator + facade, decisione B4.5 NO-OP)**: nuovi helper privati vanno nel cluster `arcgis/<dominio>.py` appropriato, MAI direttamente nel facade. Logger naming: tutti i moduli figli di `arcgis/` usano esplicitamente `get_logger("app.core.imdf.arcgis_uploader")` (NOT `__name__`) per non spostare il name dei record.

Monkeypatch semantics (test contract `tests2/security/unit/test_imdf_validate_publish_*.py`):
- **Path A** (validator lazy import): `validator.py:130` lazy-importa `_find_existing_service` / `_is_service_name_available` / `_list_user_folders` da `arcgis_uploader` al call time. Patch via `monkeypatch.setattr(uploader_mod, "<helper>", fake)` rebinda l'attributo del facade prima del lazy import → fake visto. ✓
- **Path B** (`upload_imdf_layers` interno): chiama i 7 helper privati per name lookup nello scope del facade `arcgis_uploader`, che li re-exporta come attributi. Rebind del facade visto dal chiamante locale. ✓ Preservato dalla decisione B4.5 NO-OP che mantiene l'orchestrator nel facade.
- **Path C** (chiamate intra-cluster nei moduli figli `arcgis/*.py`): name lookup nello scope del modulo figlio. Esempi veri post-B4: `_find_existing_service → _search_user_items → _enumerate_all_user_items` (tutte in `service_discovery.py`); `_delete_existing_service → _search_user_items` via `from .service_discovery import _search_user_items` in `service_lifecycle.py`; `_publish_mode_existing → _query_oid_by_key` / `_chunked` / `_quote_sql_value` via `from .sql_helpers import ...` in `publish_ops.py`. Patch su `uploader_mod.<helper>` NON visto su questi path. Trade-off documentato in ogni docstring figlio: nessun test contract attuale dipende da Path C.

---

## `backend/app/core/intermediate/` <a id="intermediate"></a>

Entry-point: `backend/app/core/intermediate/router.py`

Responsabilita: governa dataset intermedi e contratto di scambio tra step pipeline (estrai -> manipola -> arricchisci -> carica), con compare e pin.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `RunSummary`, `CompareL1Result`, `PinRequest`, `_run_to_summary`, `_check_run_access`, `list_runs`, `get_node_data`, `compare_runs_l1`, `compare_runs_l2`, `pin_run` | contratto API intermediate |
| `db.py` | `init_db`, `create_run`, `get_run`, `list_runs`, `update_run`, `pin_run`, `upsert_node_meta`, `get_node_meta`, `list_node_meta`, `close_connection` | persistenza run + node meta |
| `writer.py` | `compute_aggregate_hash`, `compute_row_hash`, `_format_float_stable`, `_normalize_value`, `write_node`, `close_run` | scrittura node data + hash di confronto |
| `policy.py` | `validate_intermediate_settings`, `resolve_intermediate_policy`, `persistence_mode_for_record`, `reset_validation_cache` | mode `metadata_only`/`preview_only`/`full_pg`/`full_artifact` + checkpoint policy |
| `snapshot.py` | `record_checkpoint_snapshot`, `record_final_snapshot`, `_fingerprint_dataframe`, `_preview_rows_for_policy` | snapshot DataFrame policy-aware senza `df.to_dict` full sul path metadata |
| `artifact_writer.py` | `persist_full_artifact`, `build_artifact_key_prefix`, `ArtifactPersistResult` | `full_artifact`: Parquet + manifest su runtime object storage |
| `artifact_reader.py` | `load_preview_from_artifact`, `ArtifactPreviewResult` | preview da artifact full per API `get_node_data` |

Keywords: `RunSummary`, `create_run`, `pin_run`, `compare_runs_l1`, `compare_runs_l2`, `get_node_data`, `upsert_node_meta`, `compute_row_hash`, `write_node`, `metadata_only`, `full_artifact`, `artifact_uri`

Intervento/confini: preservare schema/compatibilita dei dataset intermedi (e' il contratto di scambio). Non accoppiare a UI o scheduler. Pin/compare sono read-side; non scrivere stato pipeline da qui. Postgres resta catalogo leggero (`runs`, `node_meta`, hash, row count); payload completi solo via `full_artifact` su runtime object storage. `metadata_only` deve avere zero `node_rows`; `compare/l2` deve dichiarare esplicitamente diff non disponibile quando non ci sono righe persistite.

---

## `backend/app/core/logs/` <a id="logs"></a>

Entry-point: `backend/app/core/logs/router.py`

Responsabilita: espone log applicativi e log per run, sola lettura/append.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `_row_to_dict`, `get_logs`, `get_log_stats` | endpoint log applicativi |
| `runs_router.py` | `_run_to_dict`, `_log_to_dict`, `get_runs`, `get_run`, `get_run_payload` | endpoint log per run |

Keywords: `get_logs`, `get_log_stats`, `get_runs`, `get_run_payload`, `runs_router`

Intervento/confini: sola lettura/append. Mantenere `SanitizingFilter` attivo nel logger (definito a livello applicativo). Non riusare per metriche o stato runtime.

---

## `backend/app/core/manipola/` <a id="manipola"></a>

Entry-point: `backend/app/core/manipola/router.py`

Responsabilita: governa trasformazioni tabellari e spaziali via registry plugin, con executor + sub-executor plugin e API per il registry.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | re-export di `app.core.manipola.api.transforms_api.router` | aggregatore router (logica reale in `api/transforms_api.py`) |
| `executor.py` | `ManipolaExecutor`, `_get_execution_chain`, `_assert_no_lineage_cycle_on_chain`, `_applica_crs_da_colonne` | esecutore principale + lineage |
| `plugin_executor.py` | `PluginExecutor`, `PluginExecutorCompatibility`, `get_executor`, `reload_executor` | dispatch plugin trasformazioni |
| `transforms_registry.json` | (registry JSON delle trasformazioni esposte) | catalogo nomi/parametri esposto al frontend |
| `transforms/base.py` | (facade re-export, 358 righe post-B1, -89.4% vs baseline 3385) — nessun body, solo `from .<cluster> import op_*` per preservare i path `from .base import op_X` e la risoluzione `getattr(base, "op_X")` del registry | facade modulo |
| `transforms/joins.py` | `op_join`, `op_union`, `op_cross_join` | join e unione tabelle |
| `transforms/strings.py` | `op_string_pad`, `op_string_length`, `op_string_extract`, `op_text_normalize` | manipolazione stringhe |
| `transforms/utility.py` | `op_add_row_number`, `op_date_extract`, `op_uuid_generator` | utility riga (numerazione, date, UUID) |
| `transforms/columns.py` | `op_drop_columns`, `op_rename`, `op_reorder_columns`, `op_concat_columns`, `op_split_column` | shaping colonne |
| `transforms/formula.py` | `op_formula`, `_validate_formula_ast` | espressioni Python con AST whitelist |
| `transforms/cleansing.py` | `op_fill_na`, `op_replace`, `op_type_cast` | pulizia + type coercion |
| `transforms/aggregation.py` | `op_aggregate`, `op_sort`, `op_distinct`, `op_dedup_advanced`, `op_window_function` | aggregazioni e window functions |
| `transforms/filtering.py` | `op_filter`, `op_conditional` | filtri + case/when |
| `transforms/analysis.py` | `op_sample`, `op_lookup`, `op_bin`, `op_flatten_json`, `op_statistics` | sampling, lookup, binning, JSON flatten, stats |
| `transforms/reshape.py` | `op_pivot`, `op_table_diff`, `op_melt`, `op_transpose` | pivot/melt/diff/transpose |
| `transforms/security.py` | `op_md5_hash`, `op_mask_data`, `MASK_FUNCTIONS` (+ helper privati `_mask_cf/email/phone/iban/custom`) | hashing + mascheramento PII fail-closed |
| `transforms/geo.py` | `op_geo_from_coords` | costruzione geometria da lat/lon o WKT |
| `transforms/spatial.py` | `op_sjoin`, `op_geo_buffer`, `op_geo_centroid`, `op_geo_area`, `op_geo_clip`, `op_geo_dissolve`, `op_geo_intersection`, `op_geo_union`, `op_geo_difference`, `op_geo_simplify`, `op_geo_convex_hull`, `op_geo_reproject`, `op_geo_envelope`, `op_geo_distance`, `op_geo_nearest` | trasformazioni spaziali |
| `api/transforms_api.py` | `get_registry` (endpoint `/registry`) | espone il registry al frontend |

Keywords: `ManipolaExecutor`, `PluginExecutor`, `op_join`, `op_filter`, `op_aggregate`, `op_formula`, `op_sjoin`, `transforms_registry`

Punti di intervento tipici:
- nuova trasformazione tabellare -> selezionare il cluster `transforms/<cluster>.py` per dominio (joins/strings/columns/cleansing/aggregation/filtering/analysis/reshape/security/geo/utility/formula) + entry in `transforms_registry.json` con prefisso `"base.<func>"` (il facade in `base.py` re-exporta tutti i public ops dei figli, quindi il dispatcher `getattr(base, func)` continua a risolvere)
- nuova trasformazione spaziale -> `transforms/spatial.py` (nuovo `op_geo_*`) + entry in `transforms_registry.json`
- estensione formula language -> `transforms/formula.py`: `op_formula` + `_validate_formula_ast` (mai allargare l'AST whitelist senza test)
- nuovo tipo di mascheramento PII -> `transforms/security.py`: estendere `MASK_FUNCTIONS` (NON sostituire il dict object — il test contract `test_op_mask_data_fail_closed.py` patcha via `patch.dict` con identita' preservata)
- ciclo lineage tra progetti -> `executor.py` (`_assert_no_lineage_cycle_on_chain`)

Intervento/confini: I/O via `intermediate`. Nuove trasformazioni: aggiungere `op_*` nel cluster `transforms/<cluster>.py` appropriato (oppure `spatial.py`) e registrare in `transforms_registry.json` come `"base.<func>"`, mai hardcoded nel router. Il facade `base.py` re-exporta tutti i public op dei figli — preservare i `from .<cluster> import op_*` non rimuoverli. Preservare AST validation in `op_formula` (`_validate_formula_ast`). Logger naming: tutti i moduli figli usano esplicitamente `get_logger("app.core.manipola.transforms.base")` (NOT `__name__`) per non spostare il name dei record.

---

## `backend/app/core/orchestrator/` <a id="orchestrator"></a>

Entry-point: `backend/app/core/orchestrator/router.py`

Responsabilita: coordina l'esecuzione end-to-end della pipeline (sync per preview, async via task per run reali), download risultati e gestione lifecycle task.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_orchestrator`, `execute_project_pipeline`, `preview_project`, `download_project_result`, `CreateTaskRequest`, `TaskStatusResponse`, `create_task`, `get_task_status`, `cancel_task`, `cleanup_task_completely`, `download_task_result`, `_assert_project_group`, `_assert_task_group`, `_mime_for_filename` | contratto API orchestrator |
| `service.py` | `OrchestratorService` | logica di esecuzione/preview |

Keywords: `OrchestratorService`, `execute_project_pipeline`, `create_task`, `get_task_status`, `cancel_task`, `download_task_result`

Punti di intervento tipici:
- nuovo step nella pipeline -> NON in `OrchestratorService`: aggiungere modulo `core/<step>/executor.py` e farlo invocare da qui
- regressione cancel/cleanup -> `cancel_task` + `cleanup_task_completely` (idempotenti)
- nuovo formato download risultato -> `download_project_result` / `download_task_result` + `_mime_for_filename`
- gating ownership task -> `_assert_project_group` / `_assert_task_group`

Intervento/confini: non duplicare logica di estrai/manipola/arricchisci/carica - delegare ai rispettivi executor. Cancel/cleanup devono restare idempotenti. Non parlare con UI direttamente.

---

## `backend/app/core/projects/` <a id="projects"></a>

Entry-point: `backend/app/core/projects/router.py`

Responsabilita: governa progetti/canvas ETL salvati con ownership/sharing; metadata-only, niente esecuzione.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `init_projects`, `ProjectCreateWithCanvas`, `CanvasWithPlugins`, `create_project`, `list_projects`, `get_project`, `delete_project` | contratto API projects |
| `models.py` | `ProjectMetadata`, `ProjectCreate`, `ProjectList` | shape progetti |

Keywords: `ProjectMetadata`, `ProjectCreate`, `CanvasWithPlugins`, `delete_project`, `init_projects`, `require_project_owner`

Intervento/confini: metadata-only - non eseguire pipeline qui (delegare a `orchestrator`). Rispettare ownership via `auth/shares_router` e `require_project_owner`.

---

## `backend/app/core/scheduler/` <a id="scheduler"></a>

Entry-point: `backend/app/core/scheduler/router.py`

Responsabilita: governa pianificazione run, concurrency, policy, run catalog, log per schedule e artifact store; supporta missed run detection.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `beat_healthcheck`, `list_schedules`, `create_schedule`, `get_schedule`, `update_schedule`, `delete_schedule`, `toggle_schedule` (catchup/healthcheck inclusi) | contratto API scheduler |
| `schedule_service.py` | `ScheduleService`, `detect_missed_run` | logica schedule + recovery missed run |
| `execution_manager.py` | `ExecutionManager`, `ExecutionError` | esecuzione delle run schedulate |
| `concurrency_manager.py` | `ConcurrencyManager` | limiti concorrenza per schedule/progetto |
| `policy_engine.py` | `PolicyEngine` | conflict/error policy applicate alle run |
| `runs_catalog.py` | `ScheduleRunsCatalog`, `RunInfo`, `RunFileInfo` | catalogo run + file output |
| `artifact_store.py` | `ScheduleArtifactStore` | persistenza artifact per run |
| `log_service.py` | `ScheduleLogService` | logging schedule-specifico |
| `models.py` | `ScheduleConfig`, `ScheduleCreate`, `ScheduleUpdate`, `ScheduleList`, `ScheduleToggle`, `ScheduleCatchupUpdate`, `ScheduleFrequency`, `ScheduleExecution`, `ScheduleDestination`, `ScheduleConcurrency`, `SchedulePreCheck`, `ScheduleErrorPolicy`, `ScheduleConflictPolicy`, `LastExecution`, `ScheduleRuntime`, `SchedulePipeline`, `LogStep`, `ScheduleLogEntry`, `ScheduleLogList`, `BeatHealthcheck` | contratti scheduler |

Keywords: `ScheduleService`, `ExecutionManager`, `ConcurrencyManager`, `PolicyEngine`, `ScheduleRunsCatalog`, `ScheduleArtifactStore`, `detect_missed_run`

Punti di intervento tipici:
- nuova frequenza/cron -> `models.py` (`ScheduleFrequency`) + `schedule_service.py`
- nuova policy errore/conflitto -> `policy_engine.py` (`PolicyEngine`) + enum in `models.py`
- limiti concorrenza per progetto/schedule -> `concurrency_manager.py` (`ConcurrencyManager`)
- recovery missed run / catchup -> `schedule_service.py` (`detect_missed_run`) + endpoint `toggle_schedule`/catchup nel router

Intervento/confini: esecuzione pipeline solo via `orchestrator`; artifact via `artifacts`. Non bypassare `concurrency_manager`/`policy_engine` ne `require_schedule_owner`. Nuovi tipi di policy/concurrency vanno nei rispettivi engine, non nel router.

---

## `backend/app/core/tickets/` <a id="tickets"></a>

Entry-point: `backend/app/core/tickets/router.py`

Responsabilita: governa ticketing interno con commenti, contatori e meta.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `_require_not_esecutore`, `TicketCreateRequest`, `TicketUpdateRequest`, `CommentCreateRequest`, `get_meta`, `get_counts`, `create_ticket_endpoint`, `list_tickets_endpoint`, `get_ticket_endpoint` | contratto API tickets |
| `service.py` | `TicketNotifier`, `set_notifier`, `_fire_notification`, `_validate_create_input`, `_is_admin`, `_can_view`, `_can_edit_privileged_fields`, `check_access`, `create_ticket`, `list_tickets_for`, `get_ticket_with_details`, `update_ticket`, `add_comment`, `delete_ticket`, `_category_display` | CRUD ticket/commenti, ACL e notifiche |

Keywords: `TicketNotifier`, `create_ticket`, `list_tickets_for`, `get_ticket_with_details`, `update_ticket`, `add_comment`, `delete_ticket`, `check_access`

Intervento/confini: tenere separato dalla pipeline runtime. Non usare i ticket come canale di stato per le run. Notifiche solo via `TicketNotifier` (registrato con `set_notifier`).

---

## `backend/app/shared/crypto.py` <a id="sharedcrypto"></a>

Entry-point: `backend/app/shared/crypto.py`

Responsabilita: governa encryption-at-rest (Fernet) per credenziali e segreti applicativi (DB conn, API conn auth, datasource auth, MFA secrets).

| funzioni/classi chiave | cosa guardare |
|---|---|
| `encrypt`, `decrypt`, `is_encrypted`, `encrypt_dict_fields`, `decrypt_dict_fields`, `_get_fernet`, `_get_raw_key`, `CredentialUnavailableError`, `_reset_cache_for_tests` | unico punto di crypto in tutto il backend |

Keywords: `encrypt`, `decrypt`, `_get_fernet`, `is_encrypted`, `encrypt_dict_fields`, `CredentialUnavailableError`

Intervento/confini: unico punto di encrypt/decrypt - non reimplementare crypto altrove. Env canonica unica: `PLENORA_ENCRYPTION_KEY`. Gli alias legacy `URBY_ENCRYPTION_KEY` e `CONN_ENCRYPT_KEY` non sono piu' supportati; non reintrodurre fallback runtime.

---

## Storage result e runtime object storage <a id="sharedstorageresults"></a>

Entry-point: `backend/app/utils/result_store.py`, `backend/app/db/results_db_service.py`, `backend/app/shared/cloud_backends.py`

Responsabilita: salva e legge result finali, separando catalogo DB e payload runtime. In `STORAGE_POLICY=object_storage` i nuovi result devono andare sempre su runtime object storage; il fallback BYTEA resta solo per legacy read.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `app/utils/result_store.py` | `save_result_blob`, `save_result_blob_from_bytes`, `ResultSaveError`, `_storage_policy`, `_should_use_object_storage`, `_apply_results_retention` | writer result policy-aware |
| `app/db/results_db_service.py` | `save_result`, `get_latest_result`, `get_result_data`, `delete_results_for_project`, `_s3_download_bytes`, `_s3_delete_best_effort` | catalogo result + reader compat legacy |
| `app/shared/cloud_backends.py` | `ObjectStorage`, `ObjectStorageDisabledError`, `object_storage_enabled`, `get_runtime_object_storage`, `get_backup_object_storage`, `get_log_archive_storage`, `validate_storage_config` | factory e gate object storage |
| `app/config/storage_config.py` | `StoragePolicy`, `StorageProvider`, `get_storage_policy`, `get_storage_provider` | policy storage dichiarativa |

Keywords: `STORAGE_POLICY`, `OBJECT_STORAGE_BUCKET`, `PLENORA_ARTIFACT_BUCKET`, `OBJECT_STORAGE_ENABLED`, `get_runtime_object_storage`, `backend=object_storage`, `backend=db_legacy`, `storage_key`, `delete_results_for_project`

Intervento/confini:
- Nuovi result in `STORAGE_POLICY=object_storage`: `storage_key` valorizzato, `data=NULL`, nessun fallback BYTEA su errore upload/capability off/bucket mancante.
- Reader invariato compat: `get_result_data` legge `storage_key` da object storage oppure `data` BYTEA legacy; logga `backend=object_storage` o `backend=db_legacy`.
- `validate_storage_config` deve fallire al boot se `STORAGE_POLICY=object_storage` non ha `OBJECT_STORAGE_ENABLED=true` e bucket runtime esplicito (`OBJECT_STORAGE_BUCKET` o alias `PLENORA_ARTIFACT_BUCKET`). Non usare `BACKUP_BUCKET` come fallback runtime in questa policy.
- Non chiamare `boto3.client("s3")` dai moduli business: passare dalle factory `cloud_backends`.
- `RESULT_S3_THRESHOLD_BYTES` resta solo per `hybrid`; non usarlo per decidere i nuovi result in policy `object_storage`.

---

## `frontend/src/pages/` <a id="frontendpages"></a>

Responsabilita: schermate route-level del canvas ETL builder e gestione risorse (progetti, connessioni, file, log, schedule, eseguibili, ticket, admin).

File principali:
- editor canvas (i tre entry-point del builder ETL):
  - `EstraiEditor.jsx` -> componente `EstraiEditor`, registra `nodeTypes` ReactFlow (`tableNode: TableNode`, `resultNode: ResultNode`, `staticValues: StaticValuesNode`, `carica: CaricanNode`, `carica_arcgis: CaricanArcGISNode`, `featureLayerNode: FeatureLayerNode`, `comment: AnnotationNode`, `datasourceNode: DataSourceNode`); apre `JoinModal`/`PreviewModal`/`ExecutionModal`/`CaricaricaModal` lazy
  - `ManipolaEditor.jsx` -> componente `ManipolaEditor`, canvas trasformazioni (consuma `transforms_registry` da backend manipola)
  - `ArricchisciEditor.jsx` -> componente `ArricchisciEditor`, canvas enrichment con nodi API
- gestione risorse: `ProjectList.jsx`, `ConnectionManager.jsx`, `ApiConnectionManager.jsx`, `DataSourceManager.jsx`, `FileManager.jsx` + subcomponents in `pages/FileManager/` (`FileCard`, `UploadModal`, `LayerModal`, `PreviewModal`, helpers)
- scheduling/log/ticket: `ScheduleList.jsx`, `LogViewer.jsx`, `TicketsList.jsx`, `TicketDetail.jsx`
- esecuzione/admin: `EseguibiliList.jsx`, `EsecutoreHome.jsx`, `LoginPage.jsx`, `UserManager.jsx`, `AdminBackupsPage.jsx`, `AdminSystemStatusPage.jsx`

Keywords: `EstraiEditor`, `ManipolaEditor`, `ArricchisciEditor`, `nodeTypes`, `ProjectList`, `ScheduleList`, `LoginPage`, `AdminSystemStatusPage`

Intervento/confini: ogni pagina e' il route-handler di una schermata. Chiamate backend solo via `frontend/src/api/`; stato condiviso solo via `frontend/src/store/`. Niente fetch diretto, niente logica HTTP nelle pagine. La registry `nodeTypes` ReactFlow vive nel singolo editor, non in store. I tre editor condividono `components/editor/EditorShell.jsx` per guard/loading/error/canvas wrapper e `AccessDeniedBanner.jsx` per il banner permission; gli hook applicativi restano nei page editor.

---

## `frontend/src/api/` <a id="frontendapi"></a>

Responsabilita: client HTTP verso il backend FastAPI; centralizza auth, errori, configurazione e wrapping per ciascun modulo backend.

File principali:
- core HTTP (toccare quando serve auth/header/error globali):
  - `client.js` - client HTTP base con interceptor auth (token JWT)
  - `http.js` - helper request/response e gestione errori HTTP
  - `config.js` - base URL e config runtime
  - `errors.js` - mapping errori backend -> errori UI
  - `index.js` - barrel export
- client per modulo backend (top-7 piu' usati):
  - `projects.js` - CRUD progetti/canvas
  - `connections.js` - connessioni DB
  - `apiConnections.js` - connessioni API esterne (consumate da arricchisci)
  - `features.js` - servizi ArcGIS Feature (consumate da nodi feature)
  - `enrichment.js` - run/preview enrichment
  - `schedules.js` - CRUD schedule + toggle/catchup
  - `runs.js` + `logRuns.js` - run/log run intermediate
- altri client per-modulo: `auth.js`, `datasources.js`, `carica.js`, `eseguibili.js`, `files.js`, `imdf.js`, `logs.js`, `shares.js`, `tasks.js`, `templates.js`, `tickets.js`, `transforms.js`, `uploadGuards.js`, `users.js`, `adminBackups.js`, `adminSystemStatus.js`

Keywords: `client`, `http`, `config`, `errors`, `projects`, `connections`, `apiConnections`, `features`, `enrichment`, `schedules`, `runs`

Intervento/confini: unico punto di contatto col backend; auth/error handling vivono in `client`/`http`/`errors`. Non duplicare fetch nei componenti o nelle pagine. Nuovi endpoint backend -> nuova funzione nel client per-modulo corrispondente. Mai chiamare `fetch`/`axios` direttamente fuori da `api/`.

---

## `frontend/src/store/` <a id="frontendstore"></a>

Responsabilita: stato condiviso lato UI (Zustand) per il canvas ReactFlow e il flow ETL.

File principali:
- `useCanvasStore.js` - store unico del canvas (Zustand). Stato: `nodes`, `edges`, `projectId`, `mode` (`'estrai' | 'manipola' | 'arricchisci'`), `lastTaskResult`, `isReadOnly`, `isDirty`, `connections`, `editorActionsRef`. Actions principali: `init`, `reset`, `setReadOnly`, `setConnections`, `setEditorActions`, `getEditorAction`, `onNodesChange`, `onEdgesChange`, `setNodes`, `setEdges`, `updateNodeData`, `setLastTaskResult`, `deleteNode`, `toggleColumn`, `toggleAllColumns`, `addCalculatedColumn`, `removeCalculatedColumn`, `updateColumnAlias`, `handleSaveQuiet`, `markClean`, `getNodeById`, `getCanvas`

Keywords: `useCanvasStore`, `nodes`, `edges`, `projectId`, `mode`, `isDirty`, `setNodes`, `setEdges`, `updateNodeData`, `deleteNode`, `handleSaveQuiet`

Intervento/confini: nessuna logica HTTP qui - consumare `frontend/src/api/` (eccezione documentata: `handleSaveQuiet` puo' chiamare api di salvataggio progetto). Mantenere lo store coerente col canvas ReactFlow (sincronizzazione nodi/edge). Nuove actions sul canvas vanno qui, non nei componenti.

---

## `frontend/src/components/` <a id="frontendcomponents"></a>

Responsabilita: componenti UI riusabili - nodi ReactFlow, modali, panel, layout - e blocchi specifici per i flussi `arricchisci`, `imdf`, `transforms`, `eseguibili`, `logviewer`.

Sotto-cartelle e file principali:
- `nodes/` - nodi ReactFlow per il canvas. File reali: `TableNode.jsx`, `ResultNode.jsx` (+ `ResultNodeConfig.js`), `StaticValuesNode.jsx`, `DataSourceNode.jsx`, `FileSourceNode.jsx`, `ProjectSourceNode.jsx`, `CaricanNode.jsx`, `CaricanArcGISNode.jsx`, `FeatureLayerNode.jsx`, `ImdfPublisherNode.jsx`, `AnnotationNode.jsx`. Registrazione: ciascun editor (`EstraiEditor.jsx`, `ManipolaEditor.jsx`, `ArricchisciEditor.jsx`) dichiara la propria mappa `nodeTypes` ReactFlow scegliendo quali nodi esporre. Consumo dello stato: i nodi leggono/scrivono `nodes`/`edges` via hook `useCanvasStore`.
- `panels/` - pannelli laterali (`EditorSidebar.jsx`, `FilesSidebar.jsx`, `ProjectsSidebar.jsx`, `FeatureServicePanel.jsx`, `QueryPreview.jsx`, `QuickActions.jsx`, `ApiEnrichNode.jsx`)
- `modals/` - modali editor (`CaricaricaModal.jsx`, `ColumnBuilderModal.jsx`, `EnrichmentConfirmModal.jsx`, `ExecutionModal.jsx`, `FilterModal.jsx`, `JoinModal.jsx`, `PreviewModal.jsx`)
- `editor/` - shell editor shared (`EditorShell.jsx`, `AccessDeniedBanner.jsx`)
- `transforms/`, `arricchisci/`, `imdf/`, `eseguibili/`, `logviewer/`, `shared/`, `common/` - blocchi specifici di flusso
- root: `AppSidebar.jsx`, `ChangePasswordModal.jsx`, `TicketCreateModal.jsx`

Keywords: `TableNode`, `ResultNode`, `DataSourceNode`, `CaricanNode`, `CaricanArcGISNode`, `FeatureLayerNode`, `ImdfPublisherNode`, `EditorSidebar`, `EditorShell`, `AccessDeniedBanner`, `ExecutionModal`, `nodeTypes`

Intervento/confini: leggere stato da `store/` (`useCanvasStore`); nessun fetch diretto - passare per `api/`. Nuovi nodi ReactFlow: aggiungere `<Nome>Node.jsx` in `components/nodes/` e registrarlo nella mappa `nodeTypes` dell'editor che lo usa (mai globalmente). Mantenere disaccoppiamento dai modelli backend (i nodi non importano modelli Python).

---

## Contratti da non rompere

Sintesi trasversale dei confini operativi piu' importanti del repo. Toccarli senza pensarci rompe sicurezza, isolamento o compatibilita' dei dataset.

- **`backend/app/core/intermediate/`** = contratto dati tra step pipeline. Estrai/Manipola/Arricchisci/Carica scambiano dataset *solo* tramite intermediate. Cambiare lo schema `RunSummary`/`node_meta` o il formato di `write_node`/`compute_row_hash` significa rompere tutti gli step a valle.
- **Storage result/runtime object storage**: in `STORAGE_POLICY=object_storage`, i nuovi result non vanno mai in BYTEA. Il DB conserva metadata + `storage_key`; `data` BYTEA e' solo legacy read path. Runtime artifacts/result usano `OBJECT_STORAGE_BUCKET`/`PLENORA_ARTIFACT_BUCKET`, non `BACKUP_BUCKET`.
- **`backend/app/shared/crypto.py`** = unico punto di crypto-at-rest. Env canonica unica `PLENORA_ENCRYPTION_KEY` (gli alias legacy sono rimossi dal 2026-04-23). Qualsiasi nuovo segreto persistito passa da `encrypt`/`decrypt`/`encrypt_dict_fields`. Mai reimplementare Fernet altrove ne reintrodurre fallback runtime.
- **`frontend/src/api/`** = unico punto HTTP del frontend. Pagine, componenti e store NON chiamano `fetch`/`axios` direttamente; auth/header/error globali vivono in `client.js`/`http.js`/`errors.js`.
- **`backend/app/core/orchestrator/`** coordina, non duplica logica step. Nuovi pezzi di pipeline vanno nel modulo step (`estrai`/`manipola`/`arricchisci`/`carica` + executor dedicato), non in `OrchestratorService`. Cancel/cleanup task devono restare idempotenti.
- **`backend/app/core/scheduler/`** passa sempre da `orchestrator` per eseguire la pipeline. Mai eseguire step direttamente dallo scheduler. `concurrency_manager` e `policy_engine` non vanno bypassati: nuovi tipi di policy vanno la' dentro, non nel router.
- **`backend/app/core/features/`** governa il contratto ArcGIS. I response model `WriteFeaturesResponse` e `CreateServiceResponse` sono consumati da test e frontend: cambiarne shape e' un breaking change. Token come `?token=` query param, mai header. Output sempre via `_mask_secrets` per non leakare credenziali.
- **`backend/app/core/auth/` + RBAC** non si bypassa. Tutte le route nuove devono passare per `JWTMiddleware`; protezioni di ruolo via `require_admin`/`require_user_or_above`/`require_not_esecutore`/`require_*_owner`. Niente lettura segreti in chiaro: passare da `shared/crypto`.
- **`backend/app/core/manipola/transforms_registry.json`** e' la fonte unica delle trasformazioni esposte. Nuove `op_*` devono essere registrate li'; il frontend (`api/transforms.js` -> `transforms_api.get_registry`) consuma quel file. Mai hardcodare trasformazioni nel router o in `executor.py`.
