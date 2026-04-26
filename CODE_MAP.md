# Code Map

Bibbia operativa del repo. Una sezione per modulo: dove entrare, file principali con funzioni/classi reali, keywords per `rg`, dove intervenire e quali confini non rompere.

Indice rapido:
- Backend core: [admin_backups](#admin_backups), [admin_system_status](#admin_system_status), [arricchisci](#arricchisci), [artifacts](#artifacts), [auth](#auth), [carica](#carica), [connections](#connections), [datasources](#datasources), [eseguibili](#eseguibili), [estrai](#estrai), [features](#features), [files](#files), [imdf](#imdf), [intermediate](#intermediate), [logs](#logs), [manipola](#manipola), [orchestrator](#orchestrator), [projects](#projects), [scheduler](#scheduler), [tickets](#tickets)
- Backend shared: [shared/crypto](#sharedcrypto)
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
| `enrichment_service.py` | `EnrichmentExecutor`, `RateLimiter`, `_geojson_to_wkt`, `generate_error_report` | esecutore principale, rate-limit, output errori |
| `executor.py` | `EnrichExecutor` | wrapper di livello pipeline |
| `parallel.py` | `compute_parallel_groups`, `should_execute_node`, `get_execute_mask`, `_extract_consumed_columns`, `_extract_produced_columns`, `execute_parallel_enrichers` | parallelizzazione + execute_if |
| `api_connector.py` | `ApiConnectionService`, `_encrypt_auth_in_config`, `_decrypt_auth_in_config` | crud connessioni API + cifratura auth |
| `models.py` | `RowStatus`, `BatchInputFormat`, `BatchConfig`, `RowError`, `EnrichmentStats`, `EnrichmentOptions`, `EnrichmentResult`, `PreviewResult`, `EnrichmentValidationResult` | shape stato/configurazione enrichment |

Keywords: `EnrichmentExecutor`, `ApiConnectionService`, `RateLimiter`, `compute_parallel_groups`, `should_execute_node`, `_encrypt_auth_in_config`, `EnrichmentResult`

Punti di intervento tipici:
- nuova auth API (es. OAuth flow) -> `api_connector.py` (`_encrypt_auth_in_config`/`_decrypt_auth_in_config`) + `models.py`
- nuova policy di parallelizzazione/dipendenze tra nodi -> `parallel.py` (`compute_parallel_groups`)
- nuovo formato risposta API -> `enrichment_service.py` (`EnrichmentExecutor` + path che produce `EnrichmentResult`)
- regola `execute_if` per-riga -> `parallel.py` (`get_execute_mask`, `should_execute_node`)

Intervento/confini: I/O dataset solo via `intermediate`. Auth API solo cifrata via `shared/crypto.py`. Nuove regole di parallelizzazione/`execute_if` vanno in `parallel.py`, non duplicarle nel router.

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
| `database_writer.py` | `DatabaseWriter` (estende `DestinationWriter`) | scrittura effettiva su DB target |
| `writer_base.py` | `DestinationWriter` (base) | interfaccia writer |
| `bulk_writers.py` | `bulk_insert`, `is_bulk_enabled`, `BulkInsertNotSupportedError` | path bulk + telemetry |
| `ddl_generator.py` | `DDLGenerator` | DDL automatico da DataFrame |
| `column_mapper.py` | `ColumnMapper` | mapping colonne sorgente->destinazione |
| `conflict_checker.py` | `ConflictChecker`, `_serialize_value` | rilevazione conflitti pre-write |
| `idempotency.py` | `is_already_completed` (re-export da `app.shared.idempotency`) | dedup tra retry |
| `models.py` | `WriteMode`, `WriteStatus`, `TransactionMode`, `ConflictPolicy`, `ColumnMapping`, `WriteConfig`, `BatchProgress`, `ConflictDetail`, `ConflictReport`, `WriteResult`, `AuditRecord` | enum/contratti scrittura |

Keywords: `CaricaExecutor`, `DatabaseWriter`, `DDLGenerator`, `ColumnMapper`, `ConflictChecker`, `WriteConfig`, `ConflictPolicy`, `bulk_insert`

Punti di intervento tipici:
- nuovo dialect/destination -> sottoclasse di `DestinationWriter` in `writer_base.py` + registrazione in `executor.py`
- nuova `ConflictPolicy` -> enum in `models.py` + branch in `ConflictChecker` e `DatabaseWriter`
- DDL su nuovo tipo colonna -> `DDLGenerator` (mapping tipo) + `ColumnMapper`
- regressione bulk -> `bulk_writers.py` (`bulk_insert`, `is_bulk_enabled`) e telemetry whitelist

Intervento/confini: leggere dataset solo da `intermediate`. Credenziali via `connections`+`shared/crypto`. Preservare `ConflictPolicy` e idempotency keys; nuovi writer estendono `writer_base.DestinationWriter`. Non bypassare `ConflictChecker` quando policy != overwrite.

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
| `validator.py` | `validate_publish`, `_count_features_where_via_post`, `_coarse_type_from_esri`, `_coarse_type_from_dtype` | validazione pre-publish |
| `arcgis_uploader.py` | `upload_imdf_layers`, `_publish_mode_existing`, `_ensure_folder`, `_try_cleanup_zombie_service`, `_is_service_name_available`, `_search_user_items`, `_delete_existing_service`, `_add_layer_to_service`, `_query_oid_by_key` | upload effettivo verso ArcGIS |
| `persist.py` | `persist_imdf_bundle_to_disk`, `_count_features` | persistenza bundle su disco |
| `storage.py` | `_storage`, `_zip_key`, `_meta_key`, `_local_dir` | storage isolato per task |

Keywords: `upload_imdf_layers`, `validate_publish`, `persist_imdf_bundle_to_disk`, `ImdfPublishRequest`, `_publish_mode_existing`, `extract_imdf_zip`

Punti di intervento tipici:
- nuovo layer IMDF da pubblicare -> `loader.py` (`load_imdf_layers`) + `arcgis_uploader.py` (`_add_layer_to_service`)
- regressione publish "esistente" -> `_publish_mode_existing` + `_query_oid_by_key`
- check pre-publish (count/types) -> `validator.py` (`validate_publish`)
- gestione bundle su disco/cleanup -> `persist.py` + `storage.py`

Intervento/confini: pubblicazione ArcGIS solo via `features` (HTTP client) - non chiamare ArcGIS REST direttamente da qui se non tramite il client. Preservare contratto bundle/validation. Bundle access via `_assert_bundle_group_access`.

> File grande (`arcgis_uploader.py` ~1100+ righe): elencate solo le funzioni principali del flusso publish; helper minori (`_chunked`, `_quote_sql_value`, `_list_user_folders`, `_enumerate_all_user_items`, `_find_existing_service`) sono interni al flusso.

---

## `backend/app/core/intermediate/` <a id="intermediate"></a>

Entry-point: `backend/app/core/intermediate/router.py`

Responsabilita: governa dataset intermedi e contratto di scambio tra step pipeline (estrai -> manipola -> arricchisci -> carica), con compare e pin.

| file | funzioni/classi chiave | cosa guardare |
|---|---|---|
| `router.py` | `RunSummary`, `CompareL1Result`, `PinRequest`, `_run_to_summary`, `_check_run_access`, `list_runs`, `get_node_data`, `compare_runs_l1`, `compare_runs_l2`, `pin_run` | contratto API intermediate |
| `db.py` | `init_db`, `create_run`, `get_run`, `list_runs`, `update_run`, `pin_run`, `upsert_node_meta`, `get_node_meta`, `list_node_meta`, `close_connection` | persistenza run + node meta |
| `writer.py` | `compute_aggregate_hash`, `compute_row_hash`, `_format_float_stable`, `_normalize_value`, `write_node`, `close_run` | scrittura node data + hash di confronto |

Keywords: `RunSummary`, `create_run`, `pin_run`, `compare_runs_l1`, `get_node_data`, `upsert_node_meta`, `compute_row_hash`, `write_node`

Intervento/confini: preservare schema/compatibilita dei dataset intermedi (e' il contratto di scambio). Non accoppiare a UI o scheduler. Pin/compare sono read-side; non scrivere stato pipeline da qui.

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

## `frontend/src/pages/` <a id="frontendpages"></a>

Responsabilita: schermate route-level del canvas ETL builder e gestione risorse (progetti, connessioni, file, log, schedule, eseguibili, ticket, admin).

File principali:
- editor canvas (i tre entry-point del builder ETL):
  - `EstraiEditor.jsx` -> componente `EstraiEditor`, registra `nodeTypes` ReactFlow (`tableNode: TableNode`, `resultNode: ResultNode`, `staticValues: StaticValuesNode`, `carica: CaricanNode`, `carica_arcgis: CaricanArcGISNode`, `featureLayerNode: FeatureLayerNode`, `comment: AnnotationNode`, `datasourceNode: DataSourceNode`); apre `JoinModal`/`PreviewModal`/`ExecutionModal`/`CaricaricaModal` lazy
  - `ManipolaEditor.jsx` -> componente `ManipolaEditor`, canvas trasformazioni (consuma `transforms_registry` da backend manipola)
  - `ArricchisciEditor.jsx` -> componente `ArricchisciEditor`, canvas enrichment con nodi API
- gestione risorse: `ProjectList.jsx`, `ConnectionManager.jsx`, `ApiConnectionManager.jsx`, `DataSourceManager.jsx`, `FileManager.jsx`
- scheduling/log/ticket: `ScheduleList.jsx`, `LogViewer.jsx`, `TicketsList.jsx`, `TicketDetail.jsx`
- esecuzione/admin: `EseguibiliList.jsx`, `EsecutoreHome.jsx`, `LoginPage.jsx`, `UserManager.jsx`, `AdminBackupsPage.jsx`, `AdminSystemStatusPage.jsx`

Keywords: `EstraiEditor`, `ManipolaEditor`, `ArricchisciEditor`, `nodeTypes`, `ProjectList`, `ScheduleList`, `LoginPage`, `AdminSystemStatusPage`

Intervento/confini: ogni pagina e' il route-handler di una schermata. Chiamate backend solo via `frontend/src/api/`; stato condiviso solo via `frontend/src/store/`. Niente fetch diretto, niente logica HTTP nelle pagine. La registry `nodeTypes` ReactFlow vive nel singolo editor, non in store.

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
- `transforms/`, `arricchisci/`, `imdf/`, `eseguibili/`, `logviewer/`, `shared/`, `common/` - blocchi specifici di flusso
- root: `AppHeader.jsx`, `AppSidebar.jsx`, `ChangePasswordModal.jsx`, `TicketCreateModal.jsx`

Keywords: `TableNode`, `ResultNode`, `DataSourceNode`, `CaricanNode`, `CaricanArcGISNode`, `FeatureLayerNode`, `ImdfPublisherNode`, `EditorSidebar`, `ExecutionModal`, `nodeTypes`, `AppHeader`

Intervento/confini: leggere stato da `store/` (`useCanvasStore`); nessun fetch diretto - passare per `api/`. Nuovi nodi ReactFlow: aggiungere `<Nome>Node.jsx` in `components/nodes/` e registrarlo nella mappa `nodeTypes` dell'editor che lo usa (mai globalmente). Mantenere disaccoppiamento dai modelli backend (i nodi non importano modelli Python).

---

## Contratti da non rompere

Sintesi trasversale dei confini operativi piu' importanti del repo. Toccarli senza pensarci rompe sicurezza, isolamento o compatibilita' dei dataset.

- **`backend/app/core/intermediate/`** = contratto dati tra step pipeline. Estrai/Manipola/Arricchisci/Carica scambiano dataset *solo* tramite intermediate. Cambiare lo schema `RunSummary`/`node_meta` o il formato di `write_node`/`compute_row_hash` significa rompere tutti gli step a valle.
- **`backend/app/shared/crypto.py`** = unico punto di crypto-at-rest. Env canonica unica `PLENORA_ENCRYPTION_KEY` (gli alias legacy sono rimossi dal 2026-04-23). Qualsiasi nuovo segreto persistito passa da `encrypt`/`decrypt`/`encrypt_dict_fields`. Mai reimplementare Fernet altrove ne reintrodurre fallback runtime.
- **`frontend/src/api/`** = unico punto HTTP del frontend. Pagine, componenti e store NON chiamano `fetch`/`axios` direttamente; auth/header/error globali vivono in `client.js`/`http.js`/`errors.js`.
- **`backend/app/core/orchestrator/`** coordina, non duplica logica step. Nuovi pezzi di pipeline vanno nel modulo step (`estrai`/`manipola`/`arricchisci`/`carica` + executor dedicato), non in `OrchestratorService`. Cancel/cleanup task devono restare idempotenti.
- **`backend/app/core/scheduler/`** passa sempre da `orchestrator` per eseguire la pipeline. Mai eseguire step direttamente dallo scheduler. `concurrency_manager` e `policy_engine` non vanno bypassati: nuovi tipi di policy vanno la' dentro, non nel router.
- **`backend/app/core/features/`** governa il contratto ArcGIS. I response model `WriteFeaturesResponse` e `CreateServiceResponse` sono consumati da test e frontend: cambiarne shape e' un breaking change. Token come `?token=` query param, mai header. Output sempre via `_mask_secrets` per non leakare credenziali.
- **`backend/app/core/auth/` + RBAC** non si bypassa. Tutte le route nuove devono passare per `JWTMiddleware`; protezioni di ruolo via `require_admin`/`require_user_or_above`/`require_not_esecutore`/`require_*_owner`. Niente lettura segreti in chiaro: passare da `shared/crypto`.
- **`backend/app/core/manipola/transforms_registry.json`** e' la fonte unica delle trasformazioni esposte. Nuove `op_*` devono essere registrate li'; il frontend (`api/transforms.js` -> `transforms_api.get_registry`) consuma quel file. Mai hardcodare trasformazioni nel router o in `executor.py`.
