# CI Red Triage Post-PR #4 — Backend Runtime Failures

Status: **analysis only — read-only triage. Niente patch, niente deploy, niente test runtime.**

Run analizzato: `25093005588` (post-merge PR #4 su `main` @ `b3c271e`).
Risultato pytest: **38 failed + 38 errors + 2844 passed + 57 skipped** in 14m08s.

---

## 0. Distribuzione cluster

| Cluster | Count | Tipo | Localizzazione |
| --- | ---:| --- | --- |
| **F1 — Service not ready (uvicorn spawn fallisce)** | 38 errors | infrastruttura runner CI | `tests2/functional/`, `tests2/integration/` |
| **F2 — pyarrow / fastparquet ImportError** | 8 failed | dipendenza opzionale | `tests2/intermediate/`, `tests2/arricchisci/`, `tests2/shared/` |
| **F3 — openpyxl ModuleNotFoundError** | 5 failed | dipendenza opzionale | `tests2/arricchisci/`, `tests2/shared/` |
| **F4 — pyodbc ModuleNotFoundError** | 3 failed | dipendenza opzionale (driver DB, residuo non in harness integration) | `tests2/services/` |
| **F5 — BACKUP_BUCKET non configurato** | 4 failed | env CI mancante | `tests2/admin_backups/` |
| **F6 — Scheduler beat/missed-run logic** | 12 failed | bug reale (tempo/cron) | `tests2/scheduler/unit/` |
| **F7 — assertion vari (bug isolati)** | 6 failed | bug isolati | sparsi |
| **Totale** | **38 failed + 38 errors = 76** | | |

---

## 1. Cluster F1 — Service not ready (38 errors)

### 1.1 Pattern unico

Tutti i 38 errors sono `ERROR at setup of <test_name>` con stesso messaggio:
```
RuntimeError: Service not ready at http://127.0.0.1:<random_port>/: <urlopen error [Errno 111] Connection refused>
```

Errno 111 = "Connection refused" su localhost. Test che spawnano un backend uvicorn locale via fixture (`spawn_process` + `wait_for_http_ready` da `tests2/functional/harness/processes.py`) e non riescono a contattare l'endpoint sul porto random.

### 1.2 Test interessati (38 unici)

| Subdirectory | File | Test count |
| --- | --- | ---:|
| `functional/api_connector/` | `test_backend_api_connection_flow.py` | 2 |
| `functional/api_connector/` | `test_backend_api_enrich_http_failure_flow.py` | 1 |
| `functional/api_connector/` | `test_backend_api_enrich_http_flow.py` | 1 |
| `functional/orchestrator/` | `test_backend_process_contract.py` | 1 |
| `functional/orchestrator/` | `test_backend_smoke.py` | 1 |
| `functional/orchestrator/` | `test_backend_task_async_smoke.py` | 1 |
| `functional/projects/` | `test_arricchisci_file_upload_execute_smoke.py` | 1 |
| `functional/projects/` | `test_project_auth_roundtrip.py` | 1 |
| `integration/` | `test_api_connection_external_roundtrip.py` | 1 |
| `integration/` | `test_arricchisci_file_source_execute_roundtrip.py` | 1 |
| `integration/` | `test_auth_login_db_roundtrip.py` | 2 |
| `integration/` | `test_http_response_shape_contract.py` | 5 |
| `integration/` | `test_jwt_logout_blacklist_integration.py` | 3 |
| `integration/` | `test_login_rate_limit_integration.py` | 2 |
| `integration/` | `test_manipola_formula_roundtrip.py` | 1 |
| `integration/` | `test_postgres_carica_roundtrip.py` | 1 |
| `integration/` | `test_postgres_connection_and_estrai_preview.py` | 1 |
| `integration/` | `test_postgres_execute_download_roundtrip.py` | 1 |
| `integration/` | `test_postgres_pipeline_carica_chain.py` | 1 |
| `integration/` | `test_project_columns_metadata_atomic_integration.py` | 3 |
| `integration/` | `test_projects_canvas_roundtrip.py` | 2 |
| `integration/` | `test_scheduler_manual_run_roundtrip.py` | 1 |
| `integration/` | `test_task_api_async_contract.py` | 3 |
| `integration/` | `test_task_download_result_contract.py` | 1 |
| **Totale** | | **38** |

### 1.3 Root cause ipotesi

Il backend uvicorn spawnato dai test integration/functional usa la fixture `integration_env` (vedi `backend/tests2/integration/conftest.py:30–47`) che:
- popola `PYTHONPATH`, `JWT_SECRET_KEY`, `CELERY_BROKER_URL`, `DATA_DIR`
- **rimuove `DATABASE_URL`** dall'env: `env.pop("DATABASE_URL", None)` (commento: "I tentativi di forzare manualmente il DSN dei container hanno mostrato uno schema non inizializzato rispetto al backend reale.")

Quindi il backend reale spawnato cerca `DATABASE_URL` dal `.env` o dai default. Sul runner CI **non c'è `.env` né un PG container** (il CI workflow ha solo Redis service). Il backend si rifiuta di partire (lo stesso `pg_connection.py:111-113` che PR #2 aveva sbloccato per lo stub conftest, ma qui esce di processo).

**Diagnosi**: questi test richiedono un PG container reale per girare. Non skippano automaticamente perché il guard tipico è `_pg_reachable()` ma quel guard non è applicato a tutti questi file functional/integration.

### 1.4 Tipologia

**Integration service issue + missing skip guard.** Non è bug del codice applicativo, è gap di configurazione test runner CI.

### 1.5 Patch minima raccomandata

**Strategia preferita**: marker `pytest.mark.requires_real_backend` + skip in CI.

Opzioni concrete:

| Opzione | Pro | Contro |
| --- | --- | --- |
| **A. PG service container nel workflow CI** (analogo al Redis service esistente) | I test girano davvero in CI | +30s setup, complessità schema migration nel runner, scope diverso |
| **B. Marker `requires_pg` + skip via `-m "not requires_pg"` in CI** | Skip pulito, copertura locale invariata | 38 file da marcare manualmente |
| **C. Skip a livello `conftest.py` con guard `_pg_reachable()`** | Centralizza il guard | Richiede tutti i test functional/integration di passare per la stessa fixture |
| **D. Aggiungere `--ignore=tests2/functional --ignore=tests2/integration` al workflow** (escludere intere directory) | 1 riga, soluzione drastica | Perde copertura su test integration "leggeri" che non spawn backend (oggi non distinguibili) |

**Raccomandazione**: opzione **A** (PG service container) o **C** (skip via guard). Decisione architetturale, non chirurgica come gli altri batch. Va aperto come **PR separata dedicata** dopo decisione esplicita su pattern.

---

## 2. Cluster F2 — pyarrow / fastparquet ImportError (8 failed)

### 2.1 Pattern

```
ImportError: `Import pyarrow` failed. pyarrow is required for parquet support.
ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.
```

### 2.2 Test interessati

| File | Test | Errore esatto |
| --- | --- | --- |
| `tests2/intermediate/unit/test_intermediate_run_data_artifact_contract.py` | `test_full_artifact_complete_returns_parquet_preview` | `ImportError: Import pyarrow failed` |
| `tests2/intermediate/unit/test_intermediate_run_data_artifact_contract.py` | `test_full_artifact_complete_sanitizes_nan_and_inf` | idem |
| `tests2/intermediate/unit/test_intermediate_run_data_artifact_contract.py` | `test_full_artifact_default_limit_is_100` | idem |
| `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_writes_parquet_and_manifest_to_runtime_object_storage` | `AssertionError: []` (downstream conseguenza?) |
| `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_parquet_payload_roundtrip` | `StopIteration` (downstream conseguenza?) |
| `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` | `test_resolve_respects_materialize_format[parquet-input.parquet]` | `Unable to find a usable engine` |
| `tests2/shared/test_in_memory_result_contract.py` | `test_save_dataframe_to_bytes_roundtrip[parquet]` | idem |

### 2.3 Tipologia

**Dependency missing** in CI. `pyarrow` è dipendenza optional di pandas per I/O parquet. Funzionalità `full_artifact` (intermediate) usa parquet nativamente.

Il modulo `app/core/intermediate/snapshot.py:417-453` (vedi `0.5a` evidence-deep) usa `df.to_parquet(BytesIO)` quando `persistence_mode=full_artifact`. Senza pyarrow nel runner CI, i contract test su quel path falliscono.

### 2.4 Patch minima raccomandata

`pyarrow` è una dipendenza **già usata in produzione** (è quasi sicuramente in `requirements.txt` come transitive di altre lib). Da verificare: se NON è in `requirements.txt`/`requirements-dev.txt`, è un gap reale di runtime — non solo di test.

**Opzione A (raccomandata)**: aggiungere `pyarrow` a `requirements.txt` (se confermato non presente) — è dependency reale di produzione per la feature full_artifact.

**Opzione B**: `pytest.importorskip("pyarrow")` nei 3 file test interessati. Più chirurgica ma documenta che la feature `full_artifact` ha dipendenza optional non installata in CI runner (situazione divergente prod vs test).

Decisione: read del `requirements.txt`/`requirements-dev.txt` per pyarrow è il pre-step. **Se pyarrow è già in requirements, allora il gap è solo del runner CI** (es. cache pip stale o pip install fail silenzioso) e va investigato separatamente.

---

## 3. Cluster F3 — openpyxl ModuleNotFoundError (5 failed)

### 3.1 Pattern

```
ModuleNotFoundError: No module named 'openpyxl'
```

### 3.2 Test interessati

| File | Test |
| --- | --- |
| `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` | `test_resolve_dataframe_upstream_returns_bytes_tuple` |
| `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` | `test_resolve_respects_materialize_format[xlsx-input.xlsx]` |
| `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` | `test_resolve_unsupported_format_falls_back_to_xlsx` |
| `tests2/shared/test_in_memory_result_contract.py` | `test_save_dataframe_to_bytes_roundtrip[xlsx]` |

### 3.3 Tipologia

**Dependency missing** in CI. `openpyxl` è dipendenza optional di pandas per I/O xlsx. Usato dal modulo `app/utils/dataframe.py` (verificato in 0.5a evidence-deep) e da `enrichment_task` per gpkg/shp temp.

### 3.4 Patch minima raccomandata

Stesso pattern di F2: `openpyxl` è dipendenza reale di produzione per export xlsx (usato pesantemente dal frontend FileManager + tasks). **Va in `requirements.txt`** se non già presente.

**Verifica preliminare**: `grep openpyxl backend/requirements*.txt`. Se assente → gap reale prod.

---

## 4. Cluster F4 — pyodbc ModuleNotFoundError (3 failed)

### 4.1 Pattern

```
ModuleNotFoundError: No module named 'pyodbc'
```

### 4.2 Test interessati

| File | Test |
| --- | --- |
| `tests2/services/test_connection_service_mssql_sanitize.py` | `test_test_mssql_does_not_leak_raw_sqlstate_in_message_or_details` |
| `tests2/services/test_connection_service_mssql_sanitize.py` | `test_test_mssql_aggregated_message_cites_sanitized_first_error` |
| `tests2/services/test_connection_service_mssql_sanitize.py` | `test_test_mssql_log_preserves_technical_detail` |

### 4.3 Tipologia

**Dependency missing — pattern simmetrico a PR #4** ma residuo non coperto. Il file è in `tests2/services/`, NON in `tests2/integration/`, quindi non passa per i 4 harness integration che hanno `pytest.importorskip("pyodbc")`.

### 4.4 Patch minima raccomandata

`pytest.importorskip("pyodbc")` a module-level del file `test_connection_service_mssql_sanitize.py` o nell'helper che importa pyodbc. Pattern identico a PR #4, scope ridotto (1 file).

---

## 5. Cluster F5 — BACKUP_BUCKET non configurato (4 failed)

### 5.1 Pattern

```
ValueError: BACKUP_BUCKET (o BACKUP_S3_BUCKET legacy) non configurato.
```

### 5.2 Test interessati

| File | Test |
| --- | --- |
| `tests2/admin_backups/test_degraded_paths.py` | `test_list_backups_when_boto3_missing_returns_empty` |
| `tests2/admin_backups/test_degraded_paths.py` | `test_list_backups_when_client_error_returns_empty` |
| `tests2/admin_backups/test_degraded_paths.py` | `test_list_backups_raises_on_missing_dependency` |
| `tests2/admin_backups/test_degraded_paths.py` | `test_list_backups_empty_bucket_returns_empty` |

### 5.3 Tipologia

**Env missing in CI workflow** (pattern simmetrico a PR #2 `DATABASE_URL`).

Lo stub conftest non copre `BACKUP_BUCKET` perché il modulo `admin_backups.service` legge l'env a runtime nel codice path testato (`list_backups` chiama `get_backup_object_storage()` → `cloud_backends._get_backup_bucket()` → `os.getenv("BACKUP_BUCKET")` o `BACKUP_S3_BUCKET`).

Ironia: il test si chiama `test_list_backups_when_boto3_missing_returns_empty` ma fallisce **prima** di testare il path "boto3 missing", perché l'env BACKUP_BUCKET manca.

### 5.4 Patch minima raccomandata

Aggiungere al workflow CI env del job backend:
```yaml
BACKUP_BUCKET: test-backup-bucket-no-real-s3
```

**1 riga**, pattern identico a PR #2 (`DATABASE_URL` dummy). Il valore non deve essere reale (i test sono `degraded_paths`, simulano scenari di errore — il bucket non viene mai realmente toccato perché boto3 è mockato dentro i test stessi).

---

## 6. Cluster F6 — Scheduler beat / missed-run / cron logic (12 failed)

### 6.1 Pattern

```
AssertionError: Expected 'send_task' to have been called once. Called 0 times.
AssertionError: nessuna INSERT su schedule_catchup_history
assert False
assert None == datetime.datetime(2026, 4, 16, 6, 0, tzinfo=datetime.timezone.utc)
```

### 6.2 Test interessati

| File | Test count | Sottocluster |
| --- | ---:| --- |
| `tests2/scheduler/unit/test_beat_scheduler_catchup_contract.py` | 4 | beat scheduler emette catchup |
| `tests2/scheduler/unit/test_beat_scheduler_catchup_no_premature_update_contract.py` | 3 | beat NON aggiorna last_run prematuramente |
| `tests2/scheduler/unit/test_detect_missed_run_contract.py` | 5 | detect missed run logic |
| `tests2/scheduler/unit/test_schedule_service_next_execution_contract.py` | 4 | next execution datetime calculation |

### 6.3 Tipologia

**Bug reale** o **regression** — non dependency, non env. Le assertion failing dicono che la logica scheduler non sta più calcolando correttamente i missed run / catchup / next execution.

### 6.4 Patch minima raccomandata

**Investigation richiesta** — non patch chirurgica.

I test failing sono **contract test**: sono lì per blindare invarianti specifiche che ora sono violate. Possibili cause:
- Drift tempo: i test passavano in una finestra di tempo specifica e ora la data corrente li rompe (es. cron expression hardcoded che dipende da `now()`).
- Refactor scheduler che ha cambiato semantica senza aggiornare i test.
- Bug introdotto in commit recente.

**NON è un fix di CI** — è un fix di codice o di test. **Va trattato come batch dedicato di analisi scheduler**.

---

## 7. Cluster F7 — assertion isolati (6 failed)

| File | Test | Errore | Tipologia |
| --- | --- | --- | --- |
| `tests2/estrai/unit/test_query_executor_result_id_lock.py` | `test_execute_query_propagates_result_id_from_save_blob` | `Expected 'save_result_blob_from_bytes' to have been called once. Called 0 times.` | bug isolato |
| `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_writes_parquet_and_manifest_to_runtime_object_storage` | `AssertionError: []` | downstream da F2 (pyarrow missing) |
| `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_parquet_payload_roundtrip` | `StopIteration` | downstream da F2 |
| `tests2/performance/test_correctness_op_concat_columns.py` | `test_op_concat_columns_invariant[numeric+date+bool skip=False]` | `TypeError: sequence item 3: expected str instance, float found` | bug isolato (pandas dtype) |
| `tests2/security/unit/test_files_router_security_contract.py` | `test_sanitize_upload_filename_reduces_to_safe_basename` | `assert '..\\nested\\report.csv' == 'report.csv'` | bug platform-dipendente Windows path su Linux runner |
| `tests2/shared/test_cloud_backends.py` | `test_factory_unknown_provider_raises` | `Regex pattern did not match` | bug isolato |

### 7.1 Tipologia

**Bug isolati**, ognuno richiede investigazione singola. NON sono dependency né env.

### 7.2 Patch minima raccomandata

Investigation per-test dopo i cluster a basso effort (F2, F3, F4, F5). Non in batch CI red baseline.

---

## 8. Tabella riepilogativa

| Cluster | Count | Tipologia | Effort patch | Branch raccomandato | Priorità |
| --- | ---:| --- | ---:| --- | ---:|
| **F5** BACKUP_BUCKET env | 4 | env CI missing | ~10 min | `chore/ci-fix-backend-backup-bucket-env` | 1 |
| **F4** pyodbc tests/services | 3 | dependency missing (residuo PR #4) | ~10 min | `chore/ci-fix-backend-pyodbc-services` | 2 |
| **F2** pyarrow / fastparquet | 8 | dependency optional (verifica prod gap) | ~30 min (incluso check requirements) | `chore/ci-fix-backend-pyarrow` | 3 |
| **F3** openpyxl | 5 | dependency optional (verifica prod gap) | ~15 min | `chore/ci-fix-backend-openpyxl` | 4 |
| **F1** Service not ready | 38 errors | infrastructure / missing skip | ~2-4h (decisione architetturale) | `chore/ci-skip-or-add-pg-service` | 5 |
| **F6** Scheduler bugs | 12 | bug reale, non CI | ~2-8h investigation | `fix/scheduler-catchup-missed-run-regression` | 6 |
| **F7** assertion isolati | 6 | bug isolati vari | per-test | sparsi | 7 |

---

## 9. Raccomandazione PR separate in ordine di priorità

### PR 5 — F5: BACKUP_BUCKET env in CI (priorità 1)

- **Scope**: 1 file (`.github/workflows/ci.yml`), +1 env var.
- **Pattern**: identico a PR #2 (`DATABASE_URL` dummy).
- **Effort**: ~10 min.
- **Sblocca**: 4 test (`tests2/admin_backups/test_degraded_paths.py`).
- **Branch**: `chore/ci-fix-backend-backup-bucket-env`.

### PR 6 — F4: pyodbc importorskip nel test services (priorità 2)

- **Scope**: 1 file (`tests2/services/test_connection_service_mssql_sanitize.py`).
- **Pattern**: `pytest.importorskip("pyodbc")` a module-level (analogo a PR #4 ma per file singolo fuori da harness integration).
- **Effort**: ~10 min.
- **Sblocca**: 3 test.
- **Branch**: `chore/ci-fix-backend-pyodbc-services`.

### PR 7 — F2 + F3: pyarrow + openpyxl in requirements (priorità 3+4 combinate)

- **Pre-step read-only**: `grep -nE "pyarrow|openpyxl|fastparquet" backend/requirements.txt backend/requirements-dev.txt`.
- **Decisione branching**:
  - Se entrambe già in requirements → CI runner ha install issue (cache pip stale?). Investigation diversa.
  - Se assenti → aggiungere a `requirements.txt` (sono usate in prod) o solo `requirements-dev.txt` (se solo nei test). Decisione architetturale.
- **Scope ottimistico**: 1 file (`requirements.txt` o `requirements-dev.txt`), +2 righe.
- **Effort**: ~30 min (incluso check + decisione).
- **Sblocca**: 8 + 5 = 13 test.
- **Branch**: `chore/ci-fix-backend-parquet-xlsx-deps`.

### PR 8 — F1: Service not ready / spawn backend (priorità 5)

- **Decisione architetturale richiesta**: opzione A (PG service container) vs C (skip via guard centralizzato).
- **Scope**: dipende dalla scelta.
- **Effort**: 2-4 ore + review approfondita.
- **Sblocca**: 38 errors.
- **Branch**: `chore/ci-fix-backend-functional-integration-spawn`.
- **Pre-step**: triage decisione tra PR-config (workflow) vs PR-test (skip pattern).

### PR 9 — F6: Scheduler bug investigation (priorità 6)

- **Pre-step read-only**: leggere i 4 file di test scheduler e identificare la natura della regression.
- **Scope**: dipende dall'investigation.
- **Effort**: 2-8 ore.
- **Sblocca**: 12 test.
- **Branch**: `fix/scheduler-catchup-missed-run-regression`.

### PR 10+ — F7: bug isolati (priorità 7+)

- Per-test investigation, branch separati per ogni cluster logico.

---

## 10. Cosa NON fare

### 10.1 NON installare deps pesanti se meglio skip

- `pyarrow` è ~25 MB binario, ma è dependency reale prod → install OK.
- `openpyxl` è ~5 MB, dependency reale prod → install OK.
- `pyodbc` richiede ODBC system libs (apt) → meglio `importorskip` sul singolo test residuo invece di installare runtime.

### 10.2 NON escludere intera directory `tests2/integration/` o `tests2/functional/` "a caso"

- Perderebbe copertura sui test che potrebbero girare in CI senza spawn backend (es. test che usano solo Redis fakeredis).
- Pattern preferito: skip granulare via marker `requires_pg` o guard centralizzato in conftest.

### 10.3 NON forzare PR aggregata "ci-fix-everything"

- Le 5 cluster richiedono pattern diversi (env / importorskip / requirements / architectural / bug investigation).
- PR separate = review più semplice + rollback granulare + CI failure attribution chiara.

### 10.4 NON ignorare F6 (scheduler bug) come "tanto è scheduler"

- Sono 12 contract test su una funzionalità critica (catchup + missed run detection).
- Se i test passavano prima e falliscono ora, c'è una regression introdotta o un drift di tempo da indagare.
- Va trattato come bug investigation prioritaria DOPO che CI ha smesso di rumoreggiare con dependency missing.

### 10.5 NON applicare patch automatica oggi

L'utente ha esplicitato vincolo. Triage solo. Decisione esplicita richiesta su quali cluster aprire come batch + ordine.

---

## 11. Stato

**Status: triage completo. Niente patch applicata.**

Output:
- 7 cluster identificati (F1..F7) con count e tipologia
- Tabella test → file → root cause → tipologia per ognuno
- Raccomandazione PR separate ordinate per priorità (5 PR principali + bug investigation)
- 5 anti-pattern esplicitati ("cosa NON fare")
- Effort estimate per cluster

Decisione esplicita richiesta:
1. Approvazione per partire con **PR 5** (BACKUP_BUCKET env, ~10 min)?
2. Approvazione per **PR 6** (pyodbc importorskip services, ~10 min) in parallelo?
3. Pre-step read-only su `requirements.txt` per **PR 7** (pyarrow/openpyxl) — autorizzi il `grep`?
4. **PR 8** (Service not ready, 38 errors) richiede decisione architetturale separata. Discuterne prima dei batch piccoli?
5. **PR 9** (scheduler bugs) ha priorità rispetto a F1, oppure è separato?
6. **PR 10+** bug isolati: rinviato a fine batch CI red?

Resto in attesa. Nessun file modificato. Nessun test runtime. Nessun deploy.

---

*End of CI Red Triage Post-PR #4.*
