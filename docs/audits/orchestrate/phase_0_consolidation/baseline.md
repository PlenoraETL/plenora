# Phase 0.0 — Baseline pre-implementation (Run 0/5)

**Data**: 2026-04-27
**Stato**: read-only completata. Nessun codice/test/CI/conftest modificato.
**Input usati**:
- `URBY-ETL/CODE_MAP.md` (710 righe, post-Phase-5, commit `5e34fd3`)
- `orchestrate/CONTEXT.md`, `METHOD.md`, `LEDGER.md`, `phase_0_consolidation/brief.md`
- Codice reale: `URBY-ETL/backend/`, `URBY-ETL/frontend/`, `URBY-ETL/deploy.sh`
- `_history/phase0_4_root_docs_2026-04-27/PHASE_4_CLOSEOUT.md` (per contesto deploy/launch)
- `_history/orchestrate_phase5_2026-04-27/` (archivio, non autorita`)

**Regola**: i closeout Phase 5 e il LEDGER sono ipotesi. Ogni voce di questo
documento e` riconfermata o smentita dalla lettura del codice attuale.

---

## 1. Invarianti di sistema confermate

Le 8 invarianti dichiarate in `CODE_MAP.md` "Contratti da non rompere" sono
state ricontrollate sul codice. Sono tutte ancora vere a 2026-04-27. Le
elenco con la prova osservata.

1. **`backend/app/core/intermediate/`** — contratto dati pipeline.
   Confermato: `intermediate/db.py` (641 righe) + `intermediate/writer.py`
   espongono `create_run`, `pin_run`, `compute_row_hash`, `compute_aggregate_hash`.
   `compare_runs_l1`/`l2` in `router.py` consumano lo schema. Modifiche allo
   shape rompono estrai/manipola/arricchisci/carica.

2. **`backend/app/shared/crypto.py`** — unico crypto.
   Confermato: env canonica `PLENORA_ENCRYPTION_KEY` (`crypto.py:140`).
   Alias legacy `URBY_ENCRYPTION_KEY` e `CONN_ENCRYPT_KEY` **rimossi dal runtime**
   il 2026-04-23 (`crypto.py:21-23, 137-140`). Test `test_crypto_key_sourcing_contract.py`
   blinda il tombstone (legge i 3 env, ma solo la canonical viene usata).

3. **`frontend/src/api/`** — unico HTTP point.
   Confermato dal closeout Phase 4 Batch C3.1-C3.4 (boundary FE/API chiuso a 2026-04-26).

4. **`backend/app/core/orchestrator/`** coordina, non duplica step.
   Confermato: `orchestrator/router.py` 796 righe, `service.py` separato. Estrai/manipola/arricchisci/carica
   espongono ognuno il proprio executor.

5. **`backend/app/core/scheduler/`** passa sempre da orchestrator.
   Confermato: `execution_manager.py` 1125 righe, `concurrency_manager.py`,
   `policy_engine.py` separati.

6. **`backend/app/core/features/`** — contratto ArcGIS.
   `WriteFeaturesResponse` / `CreateServiceResponse` in `features/models.py`. Token
   come `?token=` query param (memoria utente lo conferma).

7. **`backend/app/core/auth/` + RBAC** — non bypassabile.
   `auth.py` 1171 righe, `JWTMiddleware`, `require_admin`/`require_user_or_above`/`require_not_esecutore`.

8. **`manipola/transforms_registry.json`** — fonte unica.
   `transforms/base.py` 358 righe = puro facade re-export (post B1).

---

## 2. Path canonici (mappa minima)

```
backend/app/
├── core/<modulo>/router.py + service.py + executor.py + models.py
│   moduli: admin_backups, admin_system_status, arricchisci, artifacts, auth,
│           carica, connections, datasources, eseguibili, estrai, features,
│           files, imdf, intermediate, logs, manipola, orchestrator, projects,
│           scheduler, tickets
├── shared/         crypto, api_utils, async_http, idempotency, request_utils,
│                   token_blacklist, cloud_backends, models
├── db/             pg_connection, query_executor, sql_functions,
│                   db_service (facade), config_db_service (facade), config/
│                   results_db_service
├── utils/          logger, dataframe, response_parser, zip_safe,
│                   debug_payload_builder, result_store
├── services/       file_service, schema_inference, project_service,
│                   connection_service, task_service
└── workers/        celery_app, beat_scheduler, tasks/
```

---

## 3. Fallback ancora attivi (intentional residual)

Verificati nel codice. Tutti documentati o implicitamente accettati. Nessuno
da rimuovere senza analisi consumer.

| Posizione | Tipo | Stato |
|---|---|---|
| `shared/crypto.py:256` | `legacy plaintext` decrypt — valore senza prefisso ritorna as-is | INTENZIONALE (D-3 ratificato in `0003-2026-04-21-fernet-crypto-at-rest.md`) |
| `shared/cloud_backends.py:288-609` | S3 → MinIO → filesystem fallback policy `STORAGE_POLICY=hybrid` | INTENZIONALE |
| `carica/bulk_writers.py:125` | `CARICA_BULK_INSERT=auto` → bulk se supportato, altrimenti executemany | INTENZIONALE |
| `carica/writer_transaction_exec.py:435` | bulk path non applicabile → executemany | INTENZIONALE |
| `carica/executor.py:385,406` | DB password decrypt + DefaultEngineFactory fallback | INTENZIONALE |
| `arricchisci/enrichment_batch_mode.py:226` | batch native fallisce → row-by-row per isolare righe buone | INTENZIONALE |
| `arricchisci/api_connection_rest_file.py:1148` | comportamento classico (file_source/project_source) se nessun upstream | INTENZIONALE |
| `arricchisci/parallel.py:304,351` | input_df fallback su execute_if mancante + RangeIndex fallback | INTENZIONALE |
| `utils/dataframe.py:148,572` | file format → CSV fallback; lat/lon → Point fallback | INTENZIONALE |
| `features/arcgis_client.py:769,1295,1596` | generic fallback, MapServer fallback, name lookup fallback | INTENZIONALE |
| `manipola/plugin_executor.py:556` | "vecchio sistema" fallback dopo plugin failure | DA VERIFICARE |
| `manipola/executor.py:694` | Y position fallback su nodi senza coordinate | INTENZIONALE |
| `orchestrator/router.py:234` | result blob temp file fallback (post-R#1) | INTENZIONALE |
| `arricchisci/enrichment_validation_preview.py:154` | "legacy: tratta tutto come required" | DA VERIFICARE (`return True` esplicito) |
| `arricchisci/enrichment_service.py:263` | "legacy: tutti i parametri sono trattati come required" | DA VERIFICARE |
| `auth/auth.py:299` | legacy bcrypt verify cost differente fino al rehash | INTENZIONALE |

I tre marcati DA VERIFICARE meritano una passata: il termine "legacy" non e` etichetta tecnica
neutra qui, e` un percorso comportamentale ancora attivo.

---

## 4. Compatibility wrappers / Re-export

### 4.1 Re-export con `# noqa: F401` (visibilita` API stabile)

| File | Cosa re-esporta | Motivo |
|---|---|---|
| `db/db_service.py` | tutto da `config_db_service` + `results_db_service` | Facade post-PG migration |
| `db/config_db_service.py:230-432` | 7 cluster da `app/db/config/{users,projects,comparison_keys,connections,file_sources,datasources,api_connections,schedules}` | Batch G #9 micro-extraction |
| `app/models.py:10,26` | `from app.shared.models import *` + dominio | Top-level barrel |
| `arricchisci/enrichment_service.py:87` | `RateLimiter` da `_rate_limiter` | B3-A.1 cutover |
| `arricchisci/api_connector.py:75` | `_AUTH_SENSITIVE_FIELDS`, `_encrypt_auth_in_config`, `_decrypt_auth_in_config`, `_nd` da `_api_connection_helpers` | B3-B.1 cutover, consumato da test direct-import |
| `imdf/arcgis_uploader.py` | tutti gli helper da `arcgis/{sql_helpers,service_discovery,service_lifecycle,publish_ops}` | B4 facade pattern, **necessario** per Path A/B test contract (`validator.py:130` lazy import + `upload_imdf_layers` interno) |
| `carica/idempotency.py:9` | `is_already_completed` da `app.shared.idempotency` | Pulito, single-symbol |
| `manipola/transforms/base.py` | tutto da `joins/strings/utility/columns/formula/cleansing/aggregation/filtering/analysis/reshape/security/geo/spatial` | **Necessario**: `getattr(base, "op_X")` del registry dispatch (`transforms_registry.json` usa prefisso `base.`) |
| `auth/auth.py:46` | `import app.config` | Side-effect: carica `.env` prima delle env reads |

### 4.2 Wrapper backward-compatible esposti

| Funzione | File | Note |
|---|---|---|
| `get_oauth2_token` (legacy entry) | `shared/api_utils.py:580` | Wrapper su `get_cached_oauth2_token` |
| `get_oauth2_client_credentials_token` (legacy) | `shared/api_utils.py:591` | Idem |
| `_prepare_headers` shim 3-line | `arricchisci/api_connector.py` | Stub di delega a `_shared_prepare_headers`; **bloccato da test source-bound** (vedi §6) |
| `generate_error_report` module-level | `arricchisci/enrichment_service.py` | back-compat con `workers/tasks/enrichment_task.py` + 1 test |

### 4.3 COMPAT-RESIDUE espliciti (3 punti reali)

Ricerca `[COMPAT-RESIDUE]` nel codice:

1. **`manipola/executor.py:416`** — warning `[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto` → emesso quando il modulo entra in un fallback storico. Da capire se ancora osservato in produzione.
2. **`arricchisci/api_connection_source_loading.py:328-340`** — `[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project` → emesso quando il filesystem fallback per project loading viene usato. Documentazione in docstring del mixin (linee 46, 170, 184, 189). Carry-over LEDGER.
3. **`arricchisci/api_connection_rest_file.py:1092`** — commento inline `owns=True resta solo per path backward-compat non usati oggi`. Probabile dead branch.

Tutti e 3 sono candidati alla classificazione "needs evidence" (residuo E): la rimozione richiede log/observability per confermare che NON vengano piu' eseguiti in produzione.

---

## 5. Feature flags / env vars (inventario)

### Crypto / Security
- **`PLENORA_ENCRYPTION_KEY`** (canonical, OBBLIGATORIO, gate hard fuori dev)
- **`PLENORA_DISABLE_DEFAULT_KEY=1`** — dev opt-in: fail-fast invece di chiave default
- **`PLENORA_ALLOW_LOOPBACK_OUTBOUND=1`** (canonical) — opt-in test/dev SSRF allowance
- **`URBY_ALLOW_LOOPBACK_OUTBOUND=1`** — alias retro-compat **deprecato** (ancora letto in `api_utils.py:121`)
- **`URBY_ENCRYPTION_KEY`** / **`CONN_ENCRYPT_KEY`** — tombstoned 2026-04-23, runtime non li legge piu' (test `test_crypto_key_sourcing_contract.py` blinda)

### Environment / Runtime
- `ENVIRONMENT` / `ENV` — `"production"|"prod"|"staging"|"stage"` triggera produzione hardening
- `DATABASE_URL`, `DB_SCHEMA` (default `plenora`)
- `JWT_SECRET_KEY` (env-only, non default), `JWT_EXPIRE_HOURS` (default 8)
- `LOGIN_MAX_ATTEMPTS` (10), `LOGIN_WINDOW_SECONDS` (60), `LOGIN_LOCKOUT_SECONDS` (300)
- `JWT_BLACKLIST_STALE_THRESHOLD_SEC` (1800), `JWT_BLACKLIST_REDIS_TIMEOUT_SEC` (1)
- `TRUSTED_PROXIES` (default lista interna)

### Cloud / Storage
- `CLOUD_PROVIDER` (default `aws`), `STORAGE_POLICY` (default `hybrid`)
- `AWS_REGION` (default `eu-south-1`)
- `BACKUP_BUCKET` / `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`
- `MINIO_ENDPOINT_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- `LOG_ARCHIVE_ENABLED`, `LOG_ARCHIVE_BUCKET`, `LOG_ARCHIVE_KEY_PREFIX`, `AUDIT_RETENTION_DAYS`
- `BACKUP_SCHEDULE_CRON` (default `"0 2 * * *"`)

### Pipeline behavior
- `ENRICHMENT_POOL_SIZE` (50, usato in 2 punti: `async_http.py:40`, `enrichment_execution_engines.py:409`)
- `ENABLE_ASYNC_ENRICHMENT` (default false)
- `CARICA_BULK_INSERT` (default `auto`)
- `MSSQL_FAST_EXECUTEMANY` (default `on`)
- `FILE_SOURCE_S3_THRESHOLD_BYTES`
- `TICKET_ATTACHMENT_MAX_BYTES` (10MB)
- `ESTRAI_MAX_ROWS` (legacy path caricato in `query_executor.py:844`)
- `DB_SCHEMA_WARN_GB` (1), `DB_SCHEMA_ERROR_GB` (5)

### Test-only (opt-in)
- `URBY_TEST_REAL_DB_INIT=1`
- `URBY_RUN_DB_LIVE_TESTS=1`
- `URBY_RUN_PERF_SHORTLIST=1`

**Osservazioni**:
- L'unico alias retro-compat ancora letto a runtime e` `URBY_ALLOW_LOOPBACK_OUTBOUND`. Il LEDGER (Phase 1 d4 archiviato) ha gia' chiuso `URBY_ENCRYPTION_KEY`/`CONN_ENCRYPT_KEY`. Coerenza: anche `URBY_ALLOW_LOOPBACK_OUTBOUND` dovrebbe seguire lo stesso destino — ma ha solo impatto test/dev, quindi la fretta e` minore.
- I 3 env test-only sono `URBY_*`, non `PLENORA_*`. Da rinominare quando si chiude l'opt-in (basso valore).

---

## 6. Test source-bound rimasti

Cardine del METHOD: i test source-bound bloccano refactor corretti. Inventario reale al 2026-04-27.

### 6.1 `inspect.getsource(<module>)` o `inspect.getsource(<class.method>)`

Trovati **17 test** in produzione con dipendenza source-bound concreta:

| File test | Target | Nota |
|---|---|---|
| `tests2/carica/unit/test_n13_alias_resolution_contract.py:193-194` | `CaricaExecutor.execute` + `exec_mod` | Doppio bind |
| `tests2/arricchisci/unit/test_enrichment_task_tenant_binding_lock.py` (×7) | `enrichment_task` + `execute_enrichment_task` | Lock test |
| `tests2/arricchisci/unit/test_enrichment_task_idempotency_contract.py:34,45` | `execute_enrichment_task` | |
| `tests2/imdf/unit/test_persist_storage_decoupling.py:118,148` | `persist_mod` + `enrichment_task` | |
| `tests2/manipola/unit/test_manipola_runtime_cycle_detection_contract.py:38,57` | `ManipolaExecutor._carica_sorgenti_progetto` + `manipola_executor` | Lega `_carica_sorgenti_progetto` al modulo executor |
| `tests2/carica/unit/test_carica_task_retry_policy_contract.py:34` | `carica_task` | |
| `tests2/carica/unit/test_carica_idempotency_contract.py:115` | `execute_carica_task` | |
| `tests2/shared/test_column_types_sources_contract.py:177,191,213` | `estrai_mod`, `enrich_mod`, `enricher_mod` | 3 moduli |
| `tests2/shared/test_type_dispatch_contract.py:153` | `carica_router` | |
| `tests2/scheduler/unit/test_execution_manager_lock_lost_propagation_contract.py:27,39,49` | `ExecutionManager.execute` (×3) | |
| `tests2/scheduler/unit/test_execution_manager_scheduled_for_persist_contract.py:28,43` | `ExecutionManager.execute` (×2) | |
| `tests2/security/unit/test_audit_action_coverage_contract.py:120` | `ast.parse(path.read_text())` | AST scan |

### 6.2 Logger source-bound rimasti

Ricerca `monkeypatch.setattr(<module>, "logger", ...)` o `patch("...logger")` in `tests2/`:

| File test | Target | Nota |
|---|---|---|
| `tests2/shared/test_artifact_resolver_trusted_roots.py:87,128,167` | `app.core.artifacts.resolver.logger` (×3) | |
| `tests2/services/test_project_service_canvas_parser_narrow.py:59,113,147` | `app.services.project_service.logger` (×3) | |
| `tests2/security/unit/test_auth_delete_group_logging_contract.py:53` | `monkeypatch.setattr(auth_mod, "logger", spy_logger)` | |
| `tests2/security/unit/test_audit_action_decorator.py:90` | `monkeypatch.setattr(audit_mod, "logger", spy_logger)` | |

I commenti in `tests2/carica/unit/test_database_writer_log_consistency_contract.py:24` e in
`test_unsafe_append_gate_contract.py:130` documentano che il pattern e` stato gia' modernizzato
per quel test (handler stdlib agganciato per nome). Resta da fare per 4 file × ~10 occorrenze.

### 6.3 Test source-bound dichiarato carry-over esplicito

`CODE_MAP.md:146` dichiara: `tests2/security/unit/test_temp_lifecycle_core.py:325` patch
`app.core.arricchisci.api_connector._shared_prepare_headers` source-bound → blocca extraction
del shim 3-line `_prepare_headers` da `api_connector.py` core. Carry-over Run 0/5 ratificato.

**Totale test debt source-bound stimato**: ~17 file con `inspect.getsource` + ~5 file con
logger patch + 1 carry-over esplicito = ~23 file da modernizzare prima dei refactor che
toccano i target. Questo e` il **principale collo di bottiglia metodologico** per Run 0/5,
in linea con `METHOD.md` "Behavior-bound > Source-location-bound".

---

## 7. Moduli grandi / hot-path

### Backend (>1500 righe)

| File | Righe | Funzione | Note |
|---|---:|---|---|
| `core/features/arcgis_client.py` | 2476 | client ArcGIS REST + EsriTypeMapper + EsriJsonConverter | non toccato da Phase 5 (B4 ha lavorato sul cluster `imdf/arcgis/`, non sul client features) |
| `core/manipola/transforms/spatial.py` | 1941 | 15 op_geo_* | hot-path geospaziale; ha gia` documentato "Invariante backward-compat" in commento |
| `services/file_service.py` | 1795 | upload + parsing + path policy | resta dopo Phase 5 |
| `db/sql_functions.py` | 1717 | funzioni SQL portabili PG/MSSQL/Oracle | resta dopo Phase 5 |
| `core/estrai/query_generator.py` | 1692 | `QueryGenerator` | resta dopo Phase 5 |
| `services/schema_inference.py` | 1465 | sotto la soglia 1500 ma vicino | |
| `workers/tasks/enrichment_task.py` | 1358 | task Celery enrichment | source-bound da 2 test (vedi §6.1) |

### Backend tests (>500 righe — superficie test grande)

| File test | Righe | Note |
|---|---:|---|
| `tests2/scheduler/unit/test_beat_scheduler_archive_hook_contract.py` | 937 | da verificare se source-bound |
| `tests2/services/test_log_archive_writer_contract.py` | 689 | da verificare se source-bound |

### Frontend (>800 righe)

| File | Righe | Funzione |
|---|---:|---|
| `pages/ApiConnectionManager.jsx` | 1183 | gestione connessioni API |
| `components/panels/ResponseConfigBuilder.jsx` | 978 | response config |
| `components/transforms/ParameterInput.jsx` | 974 | input parametri trasformazioni |
| `components/panels/ApiEnrichNode.jsx` | 906 | pannello node enrichment |
| `components/nodes/result/ResultNodeStandardView.jsx` | 880 | result node view |
| `pages/FileManager.jsx` | 875 | file manager |
| `pages/ProjectList.jsx` | 862 | project list |

**Phase 4 closeout** segnalava i file >2000 come carry-over a Phase 5; Phase 5 ha lavorato
su `database_writer.py` (2443 → 460), `api_connector.py` (2992 → 669), `enrichment_service.py`
(2019 → 502), `transforms/base.py` (3385 → 358), `arcgis_uploader.py` (1315 → 573). Tutti
ridotti.

`arcgis_client.py` 2476 e` il **piu' grande non toccato** — candidato Run 0/5 Phase 5
structural pass. Per Phase 0 e` solo da inventariare, non rifattorizzare.

---

## 8. Security boundaries

### 8.1 SSRF — debt confermato (single-source da consolidare)

Due interfacce diverse coesistono per lo stesso scopo:

| Simbolo | File | Interfaccia | Comportamento |
|---|---|---|---|
| `validate_external_url` | `app/shared/api_utils.py:67` | `(url) -> None`, `raise ValueError` | post-B3-B.8 copre IPv4 privato/loopback/link-local + IPv6 (`fc00::/7`, `::1`, `fe80::/10`) + `0.0.0.0` + `::`; getaddrinfo (no piu` gethostbyname_ex) |
| `_validate_outbound_url` | `app/core/arricchisci/api_connection_source_loading.py:499` | `@staticmethod (url) -> Optional[str]` (None=OK, stringa=errore) | usato in `_execute_rest_file` e cross-mixin (`_parse_rest_file_config`, `_build_rest_file_multipart`) |

Posizioni di chiamata:
- `validate_external_url` (raise mode): `arricchisci/router.py:153,675` (via `_validate_external_url` wrapper interno), `arricchisci/api_connection_management.py:570`, `arricchisci/api_connection_generator_enricher.py:467,737`
- `_validate_outbound_url` (return-string mode): `arricchisci/api_connection_rest_file.py:512`, mock target nei test

**LEDGER + CODE_MAP:160** dichiarano consolidamento "single-source con wrapper raise-mode" come
carry-over Run 0/5. Confermato.

### 8.2 Altre security boundaries

- **Auth**: `JWTMiddleware`, `RequestIdMiddleware` (`app/core/auth/`)
- **RBAC**: `require_admin`, `require_user_or_above`, `require_not_esecutore`, `require_*_owner`
- **Tenant isolation**: `_assert_source_project_in_tenant`, `_check_*_ownership`, `_check_owner_access` (`auth/shares_router.py`)
- **Path traversal**: `_assert_path_within`, `_sanitize_upload_filename` (`files/router.py`)
- **Sanitize**: `sanitize_for_log` (`shared/api_utils.py`), `_mask_secrets` (`features/routes.py`), `_mask_credentials` (`api_connection_management.py`)
- **Rate limit login**: `_check_rate_limit`, `_record_failed_login` (`auth/auth.py:82-84`)
- **JWT blacklist**: `app/shared/token_blacklist.py` con Redis opzionale (`JWT_BLACKLIST_REDIS_TIMEOUT_SEC`)
- **Crypto fail-closed**: `CredentialUnavailableError` in `shared/crypto.py`
- **AST whitelist**: `op_formula._validate_formula_ast` in `manipola/transforms/formula.py`
- **PII mask fail-closed**: `MASK_FUNCTIONS` dict in `manipola/transforms/security.py` (test contract `test_op_mask_data_fail_closed.py` patch.dict con identita` preservata)

---

## 9. Deploy / server assumptions

`URBY-ETL/deploy.sh` (97 righe). Stato osservato:

```
PEM     = C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem
SERVER  = ubuntu@15.160.219.93
REMOTE  = origin → https://github.com/PlenoraETL/plenora.git (post-Phase-5)
SERVER REPO = /home/ubuntu/plenora-etl/{backend,frontend}/
SYSTEMD UNITS = plenora-backend, plenora-celery, plenora-beat
DEPS (check_deps) = libmagic1, python-magic (venv), libgdal-dev/libgdal34t64
```

Flow:
1. Local `git add -A && git commit -m "deploy: $(date ...)"`
2. `git push origin main`
3. SSH server: `git pull origin main` + `pip install -r requirements.txt` + `systemctl restart`
4. Frontend: `npm run build` → `rm -rf dist remoto` → `scp dist/*`

**Assunzioni non verificate**:
- Il server e` ancora **disallineato** rispetto al remote pulito post-Phase-5 (`PlenoraETL/plenora.git`).
  LEDGER lo conferma esplicitamente: "aggiornare deploy/server remote in uno step separato".
  Lo script attualmente fa `git pull origin main` ma non sappiamo a quale `origin` punta il
  server (potrebbe essere ancora il vecchio `URBY-ETL` repo o un fork interno).
- I systemd unit names `plenora-{backend,celery,beat}` potrebbero non esistere ancora se il
  server ha unit `urby-*`. Da verificare via `! ssh ubuntu@... systemctl list-units 'plenora-*' 'urby-*'`
  prima del primo deploy Run 0/5.
- `deploy.sh` fa **commit automatico** se il working tree e` sporco (`git add -A`). Pattern
  rischioso post-Phase-5: gli orchestrate/closeout sono fuori repo, ma future modifiche locali
  non commitate verrebbero pushate con messaggio generico `deploy: <date>`.
- `deploy.sh` NON ha rollback. Se `pip install` non triggerizza restart (commento "fail-fast"),
  il vecchio resta vivo. Ma se il restart parte e l'import esplode, il servizio resta giu'.
- `deploy.sh` NON usa `git fetch + diff` per mostrare cosa sta per essere applicato.

**Carry-over deploy**: `_history/orchestrate_phase5_2026-04-27/phase_2_hardening/d4_ssh_runbook.md`
e` esistente come archivio. Si presume contenga il runbook vecchio. Non e` autorita`: serve un
runbook nuovo per Run 0/5 che includa il riallineamento server.

---

## 10. Classificazione residui (A/B/C/D/E)

Definizioni:
- **A** = fix-now, safe, locale, testabile (scope ≤ 1 file, rischio basso)
- **B** = intentional residual (no fix, da documentare/lockare)
- **C** = architecture/design initiative (multi-file, design call, test debt)
- **D** = deploy/external coordination (server, CI, GitHub, third-party)
- **E** = needs evidence/log observation (capire se ancora attivo prima di rimuovere)

### A — fix-now safe/local/testable

| # | Cosa | Dove | Rischio | Test gate |
|---|---|---|---|---|
| A1 | Verificare 3 fallback marcati "legacy" in arricchisci (`enrichment_validation_preview.py:154`, `enrichment_service.py:263`, `manipola/plugin_executor.py:556`) — capire se ancora coperti da test | 3 file | basso | grep + esecuzione test esistenti |
| A2 | Documentare `_get_oauth2_token` / `_get_oauth2_client_credentials_token` wrapper come `# DEPRECATED` espliciti in docstring (oggi sono solo "Wrapper backward-compatible") | `shared/api_utils.py:580,591` | basso | smoke import |
| A3 | Documentare `URBY_ALLOW_LOOPBACK_OUTBOUND` come deprecato + warning loud quando impostato | `shared/api_utils.py:121` | basso | test esistenti |
| A4 | Verificare `arricchisci/api_connection_rest_file.py:1092` `owns=True backward-compat path non usato`: rimuovibile? | 1 file | basso-medio | test rest_file esistenti |

### B — intentional residual (lock + documenta)

| # | Cosa | Stato |
|---|---|---|
| B1 | Tutti i fallback elencati in §3 marcati INTENZIONALE | gia` documentati |
| B2 | `_prepare_headers` shim 3-line in `api_connector.py` core | dichiarato carry-over, no-op finche` test non modernizzato |
| B3 | `RateLimiter` re-export in `enrichment_service.py` | back-compat reale |
| B4 | `arcgis_uploader.py` facade re-export | necessario per test contract Path A/B |
| B5 | `transforms/base.py` 358-righe facade | necessario per registry `getattr(base, op_X)` |
| B6 | `_filesystem_fallback_load_project` `[COMPAT-RESIDUE]` | warning attivo, candidato E |

### C — architecture/design initiative (Run 0/5 multi-fase)

| # | Cosa | Fase target | Note |
|---|---|---|---|
| C1 | **SSRF single-source consolidation** | Phase 1/2 | un wrapper raise-mode + un wrapper return-string sopra la stessa policy. LEDGER esplicito. |
| C2 | **Retry consolidation** | Phase 1/2 | per-row + batch + api_connector + ArcGIS condividono pattern (max_retries + 429 + retry_on_status + exp backoff) ma 4 implementazioni separate. Helper standalone `_retry_http_machinery` candidato. LEDGER esplicito. |
| C3 | **Rate limiter distributed** | Phase 2 | `RateLimiter` in-memory token bucket. Multi-worker coerente solo via Redis. Decisione architetturale, non refactor behavior-preserving. LEDGER esplicito. |
| C4 | **Logger naming uniform** | Phase 1 | gia` fatto in arricchisci/carica/imdf/manipola, manca verifica completa su scheduler/orchestrator/auth/features. Da grep esaustivo. |
| C5 | **Test source-bound modernization** | Phase 1 (preflight) | ~17 file `inspect.getsource` + ~5 file logger patch (vedi §6). Modernizzare prima dei refactor target, in commit separati. |
| C6 | **Big modules audit Phase 5-second-pass** | Phase 5 | `arcgis_client.py` 2476, `transforms/spatial.py` 1941, `file_service.py` 1795, `sql_functions.py` 1717, `query_generator.py` 1692. Phase 0 solo inventario. |

### D — deploy/external coordination

| # | Cosa | Bloccante? |
|---|---|---|
| D1 | Verificare server alignment con `PlenoraETL/plenora.git` post-Phase-5 (vs vecchio repo) | **SI** prima del primo deploy Run 0/5 |
| D2 | Verificare systemd unit names attivi sul server (`plenora-*` vs `urby-*`) | SI |
| D3 | Riallineare `deploy.sh` se server ha origin diverso (`git remote -v` sul server) | SI |
| D4 | Aggiornare runbook deploy a Run 0/5 (l'archivio Phase 5 D4 ssh_runbook esiste ma e` snapshot) | medio |
| D5 | Decidere policy commit-automatico in `deploy.sh` (pattern rischioso) | basso |
| D6 | GitHub repo `PlenoraETL/plenora.git`: settings di branch protection, CI, e checks da verificare | basso |

### E — needs evidence/log observation

| # | Cosa | Come decidere |
|---|---|---|
| E1 | `[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto` — ancora osservato? | grep log produzione |
| E2 | `[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project` — ancora attivo? | log warning count |
| E3 | `manipola/plugin_executor.py:556` "Fallback al vecchio sistema" — ancora raggiungibile? | code path + telemetry |
| E4 | `enrichment_service.py:263` + `enrichment_validation_preview.py:154` "legacy: tutti required" — ancora il path attivo? | unit test esiste? |
| E5 | `auth/auth.py:299` legacy bcrypt — quanti utenti residui? | DB query password_hash format |

---

## 11. Roadmap Phase 0 — batch piccoli proposti

**Vincolo**: Phase 0 chiude con un baseline solido + closeout. Niente refactor sostanziale (quello e` Phase 1/2/5).

### Batch 0.A — Locking & documentation (1-2 commit, ~3h)
**Scope**: bloccare le invarianti, marcare i deprecati.
- A2 — `# DEPRECATED` in docstring di `get_oauth2_token` / `get_oauth2_client_credentials_token`
- A3 — warning loud su `URBY_ALLOW_LOOPBACK_OUTBOUND` (in dev/test no, in prod si gia` bloccato)
- Documentare in CODE_MAP "Section: feature flags / env vars" (oggi e` sparsa nei moduli)
- Rischio: basso. Test gate: `tests2/security/unit/test_validate_external_url_contract.py` + `tests2/crypto/`

### Batch 0.B — Evidence collection (1 commit, ~2h)
**Scope**: configurare osservabilita` per i 5 candidati E.
- E1, E2, E3, E4 — confermare che i `logger.warning("[COMPAT-RESIDUE] ...")` esistenti siano scritti in DB log (`logs/`)
- Aggiungere counter (NON action) su `auth.py:299` legacy bcrypt branch per capire backlog
- Output: tabella "evidence to collect" da rivedere a Phase 1.0 per decidere E1-E5

### Batch 0.C — Source-bound test inventory & quick modernization (2-3 commit, ~5h)
**Scope**: rendere i prossimi refactor di Phase 1/2 piu` sicuri.
- Modernizzare i 4 file logger-bound piu' piccoli: `test_artifact_resolver_trusted_roots.py`, `test_project_service_canvas_parser_narrow.py`, `test_auth_delete_group_logging_contract.py`, `test_audit_action_decorator.py` → handler stdlib agganciato per nome (pattern B2.0 gia` consolidato)
- NON modernizzare i 17 `inspect.getsource` test ora — vanno fatti in coppia col refactor target (METHOD: "test-only modernization in commit separato" prima del refactor)

### Batch 0.D — Deploy alignment dry-run (1 commit + ssh probe, ~2h)
**Scope**: chiudere il gap server prima di Phase 1.
- D1 — `! ssh ubuntu@15.160.219.93 "cd /home/ubuntu/plenora-etl/backend && git remote -v && git log --oneline -5"`
- D2 — `! ssh ubuntu@15.160.219.93 "systemctl list-units --all 'plenora-*' 'urby-*'"`
- D3 — patch `deploy.sh` se necessario (no commit-auto se working tree sporco; opzionale `--dry-run`)
- Rischio: medio (tocca server). Output: D4 runbook aggiornato in `orchestrate/phase_0_consolidation/deploy_alignment.md`

### Batch 0.E — Closeout Phase 0 (1 commit doc, ~1h)
**Scope**: ratifica baseline, carry-over a Phase 1.
- File: `orchestrate/phase_0_consolidation/closeout.md`
- Aggiornare `orchestrate/LEDGER.md` con voci A1-A4 chiuse o declassate, C1-C6 indirizzate
- Decidere kickoff Phase 1 brief

**Stima totale Phase 0**: ~13h work, 7-9 commit atomici, **0 modifiche al codice produzione** salvo A2/A3 (marker deprecazione + warning loud — comunque safe e locali).

---

## 12. Verifiche LEDGER originale

Riconferma punto-per-punto rispetto a `LEDGER.md`:

| LEDGER voce | Stato osservato | Esito |
|---|---|---|
| Retry consolidation | 4 implementazioni separate (per-row, batch, api_connector chain, ArcGIS) | **CONFERMATO**, classificato C2 |
| Rate limiting Redis | `RateLimiter` in-memory thread-safe (`_rate_limiter.py`) | **CONFERMATO**, classificato C3 |
| Logger normalization | gia` fatto in arricchisci/carica/imdf/manipola | **PARZIALE**, classificato C4 |
| Compatibility wrappers | re-export documentati, 3 COMPAT-RESIDUE | **CONFERMATO**, classificato B + E |
| Dead code | Phase 5 ha rimosso 2 stub OAuth + 9 import orphan | **MIGLIORATO**, no nuova dead code evidente |
| Remote/GitHub | repo pubblicato `PlenoraETL/plenora.git`, tag `phase-5-complete` | **CONFERMATO** |
| Deploy server riallineamento | non ancora fatto | **CONFERMATO**, classificato D1-D3 |
| Behavior-bound test debt | ~17 source-bound + ~5 logger-bound ancora | **CONFERMATO**, classificato C5 |
| Phase 5 follow-up second pass | fuori scope Phase 0 | **DEFERRED a Phase 5** |
| API connector post B3-B | architettura mixin in atto | **CONFERMATO** |
| Logger naming uniform | parziale | **PARZIALE**, classificato C4 |

Nessuna voce LEDGER smentita dal codice. Il LEDGER e` rimasto **affidabile**.

---

## 13. Verifiche brief.md focus iniziali

| Focus brief | Esito |
|---|---|
| Validare carry-over LEDGER | §12 — tutti riconfermati |
| File >1500 righe | §7 — 7 file backend, 7 file frontend |
| Logger / source-bound test rimasti | §6 — ~22 file totali |
| SSRF `validate_external_url` vs `_validate_outbound_url` | §8.1 — debt confermato, classificato C1 |
| COMPAT-RESIDUE | §4.3 + §10.E — 3 punti reali, classificati E1+E2+E3 |
| Deploy/server remote | §9 — server ancora disallineato, classificato D1-D3 |

---

## 14. Decisioni esplicite per Phase 0 (proposte, NON implementate)

1. **Phase 0 non e` un refactor**. Output = 7-9 commit doc + ≤2 marker safe (A2, A3).
2. **Roadmap a batch piccoli** (0.A → 0.E) deliberatamente ridondante con METHOD §"Phase Template": baseline → preflight → proposal → execution cluster atomici → stop pre-commit → closeout → carry-over.
3. **Server alignment in 0.D PRIMA di Phase 1**: senza questo, qualsiasi fix Phase 1 rischia di non arrivare in produzione, o di sovrascrivere uno stato non noto. Bloccante implicito.
4. **Test source-bound modernization 0.C** = solo i 4 logger-piccoli. I 17 `inspect.getsource` aspettano il loro refactor target — l'ordine "modernize-then-refactor" sta nel METHOD.
5. **C1 (SSRF) NON in Phase 0** — e` design call multi-file con interfaccia diversa. Sta in Phase 1 hardening insieme a C2 (retry).

---

## 15. Riferimenti

- `URBY-ETL/CODE_MAP.md` (commit `5e34fd3`)
- `orchestrate/LEDGER.md`
- `orchestrate/CONTEXT.md`
- `orchestrate/METHOD.md`
- `_history/phase0_4_root_docs_2026-04-27/PHASE_4_CLOSEOUT.md`
- `_history/orchestrate_phase5_2026-04-27/` (archivio non-autoritativo)

---

**Stato**: Phase 0.0 baseline pre-implementation **chiusa**.
**Next**: in attesa di approvazione utente sulla roadmap §11 prima di aprire Batch 0.A.
**Vincolo rispettato**: nessun file di codice/test/CI/conftest modificato.
