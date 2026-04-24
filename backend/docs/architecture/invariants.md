# URBY-ETL — Architecture Invariants

**Riferimento vincolante**: code charter del progetto (regole base 1/4/5/6/7/8/9, review 1–7, arch 1/2/3/4/5). Questo documento e' il ponte tra il charter astratto e il codice concreto post-Fase 0.

**Stato**: pubblicato al termine di Fase 0 consolidation e aggiornato al termine di Fase 1 stabilization. Phase B + re-pass chiusi: S1-S5, S9-S13, S15-S16, S19-S20 applicati; D1 (`ENABLE_ASYNC_ENRICHMENT`) parity sync/async sanata + kill-switch mantenuto; D2 (`_read_csv_*`) unificato. Phase 1 chiusi: S17, S18, S23, S25, S26, S27 (commit atomici), S28 (triage conformita' 2026-04-20); D5 (SECRET_KEY/JWT_SECRET_KEY fallback) rimosso; Architecture A1 storage policy validate + dispatch simmetrici (commit `2a80970`+`1176c32`+`c5578db`). Post Phase 1 micro-0bis: D6 (policy preview vs execute) ratificato come ADR-0012 — codice gia' allineato via S26, §1.6 tre righe canoniche e §2 registrazione fallback autorizzato `_init_security_context(strict=False)`. **Phase 2 cluster O-ARC (log lifecycle) chiuso 2026-04-21**: §1.4 cita il path archive-before-purge canonico su object storage, §3 registra `LOG_ARCHIVE_*` + `AUDIT_RETENTION_DAYS` come config-not-flag, §6 sposta la carry-over log lifecycle da aperta a ratificata (ADR-0014 + addenda §A Batch 2 + §B Batch 4). **Phase 2 cluster P-CLR (log purge user path cleanup) chiuso 2026-04-22**: §1.4 registra la rimozione di `POST /logs/clear` + FE button come ultimo user path che toccava DELETE `plenora.logs`; il beat worker resta unico consumer autorizzato del bypass `SET LOCAL plenora.retention_cleanup='true'`; cascade cleanup `config_db_service.delete_group_cascade` + `orchestrator` task cleanup ora operano solo su `log_runs` (state table), non su `logs` (append-only). §6 chiude la carry-over user-path. Residui veri classificati in §6.

**Aggiornato**: 2026-04-22 (post Phase 1 stabilization + micro-0bis D6 ratifica + Phase 2 O-ARC cluster ratifica + Phase 2 P-CLR cluster ratifica)

**Ambito**: le invarianti qui elencate valgono per il codice `backend/app/` e vincolano ogni nuovo commit da Fase 1 in poi. Le scelte di prodotto o di performance restano fuori scope.

---

## 1. Core path canonici

Per ogni operazione fondamentale dichiariamo **il principio**, non l'implementazione specifica. L'implementazione concreta puo' evolvere (rename, refactor) senza violare il principio, purche' resti uno solo il path di default e le ottimizzazioni vivano isolate.

### 1.1 Scrittura su DB destinazione

- **Principio**: il path canonico passa per la strategia `DestinationWriter` (`backend/app/core/carica/writer_base.py`), la cui unica implementazione attiva e' `DatabaseWriter` (`backend/app/core/carica/database_writer.py`). Un solo entry point, una sola policy di transazione, un solo modello di errore.
- **Ottimizzazioni native bulk — rimosse 2026-04-23**: `bulk_writers.py` e' ora scaffolding dispatcher che raise sempre `BulkInsertNotSupportedError(reason="dialect_not_supported")` per tutti i dialect (postgres, mysql, mariadb, oracle, mssql, sqlite). Il caller `_execute_batch` fa fallback universale a `executemany` (path unico effettivo post-rimozione). Storico delle rimozioni: MySQL LOAD DATA LOCAL INFILE (`kickoff.md §9.5`, commit `54d6e44`) + Postgres COPY FROM STDIN (`kickoff.md §9.6`, commit `7256940`, deviazione esplicita dal no-go `performance_bulk_review.md §5.1` accettando regressione ~19x). Per MSSQL resta il boost `fast_executemany=True` sull'engine factory (`app/db/query_executor.py::_crea_engine_mssql`), path driver-level non coinvolto dalle rimozioni. Il kill-switch `CARICA_BULK_INSERT` resta come scaffolding (no-op post-rimozione). Tombstone anti-regressione in contract test `tests2/carica/unit/test_bulk_writers_contract.py`.
- **Vincolo**: una seconda strada di scrittura DB richiede motivazione scritta nel PR, test di parita' con il path canonico e benchmark documentato. Non sono ammessi writer "paralleli" creati per convenienza.

### 1.2 Lettura DB sorgente

- **Principio**: la lettura dati da connessioni utente passa per `QueryExecutor` (`backend/app/db/query_executor.py`), che possiede il ciclo di vita di engine e pool, l'iniezione parametri, la normalizzazione dei nomi colonna e i cap di righe (`ESTRAI_MAX_ROWS`). Le dipendenze dialect-specifiche (timeout, Oracle `ALTER SESSION`) sono incapsulate in QueryExecutor, non sparse nei moduli business.
- **Streaming**: per output file (CSV/Parquet) esiste un secondo entry point (`_execute_streaming_to_file`) che mantiene peak memory proporzionale al chunk (10k righe). E' una specializzazione ammessa perche' i due path divergono per requisito di memoria, non per convenienza.
- **Vincolo**: query raw o DBAPI diretto fuori da QueryExecutor sono ammessi solo per `app/db/pg_connection.py` (infra DB interno PLENORA) e per i service `*_db_service.py` (catalogo interno). I moduli `core/*` business non devono parlare direttamente con i driver DB utente.

### 1.3 Gestione transazioni

- **Principio**: per il catalogo PLENORA (PostgreSQL interno) il pattern canonico e' `with pg_connection.transaction() as cur:` (`backend/app/db/pg_connection.py`). `commit` e `rollback` manuali sono confinati a `pg_connection.py` e ai `*_db_service.py` che lo usano come facade.
- **Facade stabile**: `app/db/db_service.py` ri-esporta l'API usata dal resto dell'app e mantiene backward-compat post-migrazione SQLite → PostgreSQL.
- **Vincolo**: nessun `commit()` sparso in `core/*` business. Nessun pool di connessione privato in un modulo di dominio.

### 1.4 Logging

- **Principio**: ogni modulo che logga usa `from app.utils.logger import get_logger` + `logger = get_logger(__name__)`. Non si istanzia `logging.Logger` direttamente ne' si usa `print` per output diagnostico.
- **Livelli**:
  - `debug` = tracing di path alternativi, cleanup non bloccanti, parsing fallback non critici (es. JSON parse in `logs/_row_to_dict`)
  - `info` = milestones operative (avvio worker, inizio/fine pipeline)
  - `warning` = condizione anomala gestita senza fallimento (es. `putconn()` fallito, upload rifiutato per MIME)
  - `error` = fallimento gestito che compromette l'operazione corrente (prefisso `[CRASH]` per errori che poi diventano 500)
  - `critical` riservato a infrastruttura core (startup fallito, crypto broken)
- **Policy fail-closed nei consumer logging**: se logging stesso fallisce, il modulo chiamante non deve cascare — il logger ha i propri `except: pass` nel solo percorso di formatting per evitare ricorsione.
- **Archive-before-purge canonico (O-ARC cluster, ADR-0014 — ratificato 2026-04-21)**: il lifecycle di `plenora.logs` e `plenora.audit_actions` segue un path unico implementato nel beat worker (`backend/workers/beat_scheduler.py` hook `_archive_old_logs` + `_archive_old_audit_actions`). Sequenza vincolante per ogni tick giornaliero con `LOG_ARCHIVE_ENABLED=true`: (1) `LogArchiveWriter` (`backend/app/services/log_archive.py`, Batch 1 frozen) seleziona le righe oltre retention, serializza in NDJSON gzip deterministico (`mtime=0`) e carica su object storage via `ObjectStorage` Protocol (factory `get_log_archive_storage()` in `backend/app/shared/cloud_backends.py`); (2) verify post-upload via `list_objects(prefix=key)` con `size>0` — la DELETE **non** parte se verify fallisce; (3) DELETE scoped `ts < cutoff AND id <= max(batch_ids)` con bypass `SET LOCAL plenora.retention_cleanup='true'` sullo **stesso cursor**, unica via d'uscita dal trigger append-only `plenora.block_log_modifications` (migration `2026-04-21_log_immutable_triggers.sql`). Il bypass e' scoped al beat worker — nessun altro consumatore autorizzato. Il legacy `_cleanup_old_logs` resta gated: quando `LOG_ARCHIVE_ENABLED=true` non esegue la DELETE per evitare doppio purge.
- **Observability circolare beat — deviazione autorizzata (ADR-0014 addendum §B Batch 4)**: al termine di ogni run success degli hook archive, il beat emette **1 riga** su `plenora.logs` con `op='log_archive'` + `module='workers'` + `details` JSON (`table`, `cutoff`, `archived_rows`, `object_keys`, `batch_runs`). Contro la policy generale "solo `logger` canonico" (primo bullet), qui la scrittura passa per **`pg_execute` diretta** sulla stessa shape 11-colonne del `PostgresLogHandler` (`backend/app/utils/logger.py:290-346`). Motivo: `LOG_LEVEL_DB=WARNING` default filtra `logger.info`, quindi il sink canonico non instrada la riga. Perimetro chiuso ai 2 hook archive; best-effort con `logger.warning('Observability log_archive fallita ...')` su eccezione (non contamina lo stato "archive ok": lo slot `_last_*_archive` e' gia' aggiornato prima della INSERT observability). Estensioni future del pattern (altri moduli che vogliono INSERT diretta) non sono autorizzate: richiedono nuovo ADR.
- **User path DELETE su `plenora.logs` chiuso (P-CLR cluster, ratificato 2026-04-22)**: l'endpoint `POST /logs/clear` (`backend/app/core/logs/router.py`) e la UI admin associata (`frontend/src/pages/LogViewer.jsx` button + `frontend/src/api/logs.js` client + `frontend/src/utils/permissions.js` capability) sono stati **rimossi** in commit `698ff9d`. Dopo O-ARC l'endpoint era de-facto dead code: il trigger `plenora.block_log_modifications` (migration `2026-04-21_log_immutable_triggers.sql`) avrebbe respinto ogni DELETE senza il bypass `SET LOCAL plenora.retention_cleanup='true'`, bypass scoped al solo beat worker. P-CLR chiude formalmente il perimetro: **nessun endpoint utente autorizza DELETE su `plenora.logs`**. I cleanup cascade applicativi (`config_db_service.delete_group_cascade`, `app/core/orchestrator/router.py` task full-cleanup) operano ora esclusivamente su `log_runs` (state table mutabile), mai su `logs` (append-only). Anti-reintroduzione blindata da contract test `backend/tests2/security/unit/test_logs_clear_endpoint_removed.py` (POST/GET 404 + assenza `/logs/clear` dall'OpenAPI schema). **Il beat worker resta unico consumatore autorizzato del bypass `retention_cleanup`.**

### 1.5 Config loading

- **Principio**: l'entry point canonico e' `app.config.settings` (`backend/app/config.py`, classe `Settings` con singleton `settings`). I valori ambientali transitano tutti da qui, con default, type coercion e validazione centralizzate.
- **Eccezioni documentate**: alcuni moduli leggono direttamente `os.getenv` per:
  - feature flag locali (`ENABLE_ASYNC_ENRICHMENT`, `CARICA_BULK_INSERT`, `MSSQL_FAST_EXECUTEMANY`, `PLENORA_ALLOW_LOOPBACK_OUTBOUND`) — il flag vive vicino al codice che lo consuma per ridurre indirezione; deve comunque comparire nella tabella §3
  - crypto key sourcing (`app/shared/crypto.py:_get_raw_key`) — legge solo `PLENORA_ENCRYPTION_KEY` (D4 chiuso 2026-04-23, D5 chiuso 2026-04-20). Documentato nella docstring del modulo
  - tuning numerici locali (`ENRICHMENT_POOL_SIZE`, `LOGIN_MAX_ATTEMPTS`, thresholds `DB_SCHEMA_*`) — valori numerici stretti a un solo chiamante
- **Vincolo**: qualsiasi nuovo `os.getenv` in business code (`core/*`) richiede giustificazione nel PR e aggiornamento di §3 se e' un flag, o promozione a `settings` se e' config.

### 1.6 Error handling per modulo

Principio generale: **fail-fast su dato critico (charter base 8)**, **best-effort su cleanup/osservabilita'**, **mai silent** (charter base 9). La tabella registra la policy dominante osservata dopo Fase 0.

| Modulo | Policy dominante | Note post-Fase 0 |
|--------|------------------|------------------|
| `app/shared/crypto.py` | Fail-fast su encrypt, best-effort loggato su decrypt | S1 chiuso: encrypt solleva `RuntimeError` se Fernet e' rotto, niente plaintext silenzioso. Decrypt ritorna `None` su `InvalidToken` con `logger.error` (fallback autorizzato, §2) |
| `app/db/` | Fail-fast su query, best-effort loggato su cleanup pool | S12 chiuso (`pg_connection.put_connection` ora logga warning). S10 chiuso: i 7 siti di cleanup in `query_executor.py` loggano `logger.debug` uniforme (dispose engine, timeout setup, Oracle `ALTER SESSION`, disconnect) |
| `app/core/auth/` | Fail-fast | S5 chiuso: `delete_group_cascade` logga `[CRASH]` e poi solleva `HTTPException 500` |
| `app/core/carica/` | Fail-fast su introspection e scrittura; best-effort loggato su cleanup cursor | S2 chiuso: `table_exists/get_existing_columns/get_schemas/get_tables` sollevano (niente return vuoto ambiguo). S9 chiuso: `cursor.close()` in COPY logga `debug` |
| `app/core/features/` | Fail-fast su formato file non supportato | S3 chiuso: `_load_dataframe` non cade piu' silenziosamente in CSV; solo `.tsv/.txt` sono fallback legittimo. S4 chiuso: `features_service_db.py` merged in `FeaturesService` |
| `app/core/logs/` | Best-effort loggato (modulo di display) | S11 chiuso: JSON parse in `_row_to_dict` logga `debug` quando `details` non e' JSON valido |
| `app/core/files/` | Fail-fast con HTTPException; log uniforme su MIME rejection | S16 chiuso: livello `warning` uniforme nei due punti di rejection MIME (preview + upload) |
| `app/core/admin_backups/` | Fail-fast con HTTPException; warning su calcoli non bloccanti | S15 chiuso: calcolo `duration_sec` logga `warning` con contesto |
| `app/core/connections/`, `projects/`, `tickets/`, `scheduler/`, `orchestrator/`, `artifacts/`, `eseguibili/` | Fail-fast via HTTPException | Pattern uniforme, nessuna anomalia in Fase 0 |
| `app/core/estrai/`, `manipola/`, `arricchisci/` — `execute()` | Fail-fast | Policy canonizzata da ADR-0012 (ratifica D6): errore che compromette correttezza/persistenza/tenancy propaga come `HTTPException` con `logger.error [CRASH]`. Nessun return soft `{success: false}` su path che persiste |
| `app/core/estrai/`, `manipola/`, `arricchisci/` — `preview()` | Best-effort loggato (`warning+`) | Policy canonizzata da ADR-0012: catch top-level + return uniforme `{success: False, message: str}` per UI; mai silent (`debug` come unico canale vietato). Pattern applicato tatticamente da S26 (`db9ef03` arricchisci, `6db33c7` manipola) prima della ratifica formale |
| `app/core/estrai/`, `manipola/`, `arricchisci/` — `preview()` fail-fast | Fail-fast | Eccezioni alla best-effort di preview, da ADR-0012 §3: (a) auth/tenant binding non risolvibile, (b) canvas malformato (result node assente, edge dangling, connection_id non esistente), (c) validazione contrattuale di input. Queste salgono come eccezione al router anche in preview |
| `workers/` | Fail-fast su raise Celery, best-effort su cleanup intermediate | Invariato in Fase 0 |
| `app/utils/` | Fail-fast | Pattern uniforme; `logger.py` e' l'unica eccezione motivata (no-recursion) |

**Vincolo**: una PR che cambia policy errore di un modulo deve dichiararlo e argomentare perche'. Sposare "silent" non e' mai ammesso.

### 1.7 Storage dispatch (result / file)

- **Principio**: ogni scrittura/lettura di artefatti di risultato e file sorgente passa per un dispatch policy-aware. La policy e' `STORAGE_POLICY` con valori `db | object_storage | hybrid`, caricata via `app/config/storage_config.py` e validata fail-fast all'avvio da `validate_storage_config()` (ADR A1, commit `2a80970`).
- **Entry point canonici**:
  - `app/core/*/result_store.py` — dispatch risultati per modulo (result_store policy-aware, commit `1176c32`)
  - `app/services/file_service.py` — dispatch file sorgente (pattern FS-4 simmetrico, commit `c5578db`)
  - `app/db/results_db_service.py` (`_s3_download_bytes` :67, `_s3_delete_best_effort` :134) — reader/cleanup simmetrico FS-4 (stesso dominio result, log prefisso `[FS-4]`) — registrato review A1-B1 (2026-04-22)
  - `app/db/config_db_service.py:618-627` — cleanup S3 simmetrico del writer FS-5 (stesso dominio file_source, ramo delete) — registrato review A1-B1 (2026-04-22)
- **Provider disponibili**: DB locale (Postgres) + AWS S3 + MinIO (P1 ratificato A1-B2, commit `a5956f7`, ADR-0015). Azure/GCP fuori scope P2 (A1 §8).
- **Vincolo**: nessun accesso diretto a bucket S3 / filesystem / DB bytea fuori da questi entry point. Se un modulo legge/scrive artefatti bypassando il dispatch, e' una violazione della policy e va corretta, non normalizzata via fallback locale.
- **Config-not-flag**: STORAGE_POLICY non e' un toggle (niente `on`/`off`) ma un enum di deployment. E' registrato in §3 Config-not-flag per trasparenza.

### 1.7.bis Callers ortogonali dichiarati (registro A1-B1 chiuso)

Call site che raggiungono object storage / secrets ma appartengono a domini distinti dal dispatch §1.7. Non sono bypass: hanno governance propria. Registro chiuso — review `A1-B1` 2026-04-22, `orchestrate/phase_2_hardening/a1_bulk_review.md`.

| Caller | Dominio | Fonte ortogonalita' |
|---|---|---|
| `app/core/eseguibili/storage.py` + `workers/tasks/enrichment_task.py:833-835` | eseguibili output | Dominio distinto (non result, non file_source). Vedi `a1_bulk_review.md §4.2` |
| `app/core/imdf/storage.py` | IMDF bundle | Dominio distinto (IMDF bundle cache multi-host); boundary ratificato `ADR-0016` |
| `workers/beat_scheduler.py:918,1099` (`get_log_archive_storage`) | log archive | `ADR-0014` + §1.4 |
| `scripts/backup_db.py`, `scripts/test_restore.py` | DB backup | `a1_storage_policy.md §7.3` |
| `scripts/load_secrets.py` (`get_secrets_backend`) | secrets bootstrap | `a1_storage_policy.md §7.3` |

Promozione o rimozione di una riga richiede PR dedicata e nota in `a1_bulk_review.md §8 Follow-up`.

---

## 2. Fallback autorizzati

Questo e' l'elenco chiuso dei fallback attivi nel codice `backend/app/` che soddisfano le 4 proprieta' charter base 5 (motivo, perimetro, log, piano). Un fallback non presente in questa tabella = violazione dell'invariant, da rimuovere o da promuovere qui con PR dedicata.

| Path | Motivo | Perimetro | Log | Piano rimozione |
|------|--------|-----------|-----|-----------------|
| `app/shared/crypto.py:decrypt()` → ritorna `None` su `InvalidToken`/errore | Consentire all'app di restare in vita: le connessioni terze con token corrotto falliranno al primo uso dando errore per-connessione, anziche' crash globale | Solo percorso decrypt; encrypt fa fail-fast (S1) | `logger.error` con causa (InvalidToken vs exception generica) | Rimuovere quando il policy-product sara' "crypto-broken = app down" (fuori Fase 0) |
| `app/shared/crypto.py:_get_raw_key()` → dev default key in ambiente non-prod | Ergonomia sviluppo locale: non bloccare lo start dell'app se nessuna env var e' settata | Solo `ENVIRONMENT != production/prod` e senza `PLENORA_DISABLE_DEFAULT_KEY=1` | `logger.warning` rumoroso al primo uso | Rimuovere quando tutti i dev-env avranno `.env` configurato (target decisione: D-fascia) |
| `app/core/carica/bulk_writers.py` COPY/LOAD DATA → `executemany` | Dialect non supporta bulk nativo o COPY fallisce per geometry/permessi | Sollevato `BulkInsertNotSupportedError`, intercettato da `database_writer.py:2010` | `logger.warning` con tipo errore | Flag `CARICA_BULK_INSERT=off` (§3.3); rimozione non prevista (safety valve) |
| `app/db/query_executor.py:_crea_engine_mssql` Driver 18 → Driver 17 | Alcuni host prod hanno solo Driver 17 installato | Soltanto MSSQL | `logger.warning` sul secondo tentativo | Rimuovere quando tutti i deploy avranno Driver 18 (target: verifica infra) |
| `app/core/features/features_service.py:_load_dataframe` → CSV fallback per estensioni `.tsv/.txt` con suffix GeoJSON fallito | Formati CSV-like plausibili non sono riconosciuti dalla mappa suffix | Solo `.tsv/.txt` dopo fallimento `gpd.read_file`; altri suffix sollevano `ValueError` (S3) | `logger.warning` + `logger.info` sul fallback | Rimozione non prevista; e' estensione controllata della mappa formati |
| `app/db/pg_connection.py:put_connection()` → warning se `putconn` fallisce | Connessione non tornata al pool non deve crashare la richiesta in corso | Solo shutdown path della connessione | `logger.warning` con type+msg (S12) | Segnala pool exhaustion; rimozione non prevista (observability marker) |
| `app/core/logs/router.py:_row_to_dict()` → lascia `details` come stringa se non e' JSON | Log legacy hanno `details` testuale | Solo display log | `logger.debug` (S11) | Rimuovere quando tutti i log verranno scritti con `details` JSON normalizzato (target: data-cleanup) |
| `app/core/carica/bulk_writers.py:cursor.close() in finally` | psycopg2 cursor close non deve mascherare l'errore originale se fallisce | Solo ramo finally di COPY Postgres | `logger.debug` con type+msg (S9) | Rimozione non prevista |
| `app/core/admin_backups/service.py` → warning su `duration_sec` | `started_at/finished_at` potrebbero essere stringhe o `None` in rows legacy | Solo rendering job summary | `logger.warning` con contesto started/finished (S15) | Rimuovere quando schema log job avra' tipi consolidati |
| `app/core/arricchisci/enrichment_service.py:408-410` → `_execute_parallel` su eccezione async | Safety-net: se il path async (attivo in prod) solleva per ragione inattesa, il path sync offre lo stesso contratto osservabile (parity D1) | Solo ramo `except` del routing async; non maschera errori API normali (quelli tornano come `RowError`) | `logger.error` con `exc_info=True` e prefisso `[ASYNC]` | Rimozione non prevista finche' il flag resta kill-switch |
| `app/core/manipola/executor.py:146` `_init_security_context(strict=False)` in `preview()` | Preview non persiste: un `group_id` non risolvibile non compromette l'integrita' tenant. I source loader sono fail-closed e restituiscono "Nessun dato sorgente disponibile" con messaggio leggibile invece di bloccare la UI con 500 | Solo `preview()` di manipola (`executor.py:305`); `execute()` chiama `strict=True` e fail-fast con `ValueError` se `group_id` e' `None` | `logger.error` prefisso `[GESTITO]` sul catch interno al lookup progetto (`executor.py:163-164`); `strict=False` di per se' non logga perche' e' un mode-switch di contratto, non un catch | Rimozione non prevista finche' preview resta non-persistente. Ratificato da ADR-0012 §3; registrazione post-S26 richiesta da D6 §4.1 |

**Fallback note di debito (non invarianti, da risolvere fuori Fase 0)**:

- `app/services/file_service.py` 21 `except: pass` sparsi (triage rimandato — S17 Phase 1). Mix legittimo (type detection) + sospetto; richiede audit riga-per-riga.
- `app/core/arricchisci/api_connector.py` 25 `except` in retry loop (triage rimandato — S18 Phase 1). Pattern ripetitivo: verificare se va unificato in helper comune o se ogni ramo ha ragione specifica.

---

## 3. Feature flag vivi

Ogni flag attivo nel codice deve stare qui con owner, data di introduzione e rimozione attesa. Un flag senza data di rimozione e' un flag morto in attesa di essere accorto.

| Flag | Owner modulo | Introdotto | Scopo | Stato | Rimozione attesa |
|------|--------------|------------|-------|-------|------------------|
| `ENVIRONMENT` / `ENV` | `app/main.py`, `app/shared/crypto.py`, `app/core/auth/auth.py` | 2026-04-10 | Kill-switch prod/dev: disabilita `/docs`, forza fail-fast crypto key, attiva policy JWT strette | Attivo, operazionale | Mai (infrastrutturale) |
| `CARICA_BULK_INSERT` | `app/core/carica/bulk_writers.py` | 2026-04-10 | Kill-switch operazionale per bulk DB write (`auto`/`on`/`off`) | Attivo, default `auto` | Mai (safety valve prod) |
| `MSSQL_FAST_EXECUTEMANY` | `app/db/query_executor.py` | 2026-04-10 | Opt-out driver tuning (default `on`, `off` per precisione DATETIME2) | Attivo, default `on` | Mai (dialect-specific) |
| `PLENORA_ALLOW_LOOPBACK_OUTBOUND` (canonical) + `URBY_ALLOW_LOOPBACK_OUTBOUND` (alias retro-compat, deprecato) | `app/shared/api_utils.py` | 2026-04-10, rinominato 2026-04-23 (P1.5), guard `ENVIRONMENT` aggiunto 2026-04-23 (P2.4/S6) | Test-only: consente loopback in outbound validation. IGNORATO by-design se `ENVIRONMENT in {production, prod, staging, stage}` | Attivo solo in test harness | Mai (test infra) |
| `PLENORA_DISABLE_DEFAULT_KEY` | `app/shared/crypto.py` | 2026-04-10 | Dev opt-in per replicare fail-fast di prod | Attivo | Mai (dev ergonomy) |
| `ENABLE_ASYNC_ENRICHMENT` | `app/core/arricchisci/enrichment_service.py` | 2026-04-10 (security audit) | Routing tra `_execute_parallel` (sync) e `_execute_parallel_async` (async) | Kill-switch operazionale post-D1. Verifica prod 2026-04-20: `true` in `/home/ubuntu/plenora-etl/backend/.env`. Parity rate-limiter + error granularity sanata (D1); divergenze residue accettate e documentate (early stop, progress) | Rimozione non prevista (safety valve analogo a `CARICA_BULK_INSERT`) |

### Env var crypto-key (coda di §1.5)

La chain legge solo `PLENORA_ENCRYPTION_KEY` (D4 chiuso 2026-04-23). Tutti gli alias legacy sono stati rimossi.

| Env var | Tipo | Stato |
|---------|------|-------|
| `PLENORA_ENCRYPTION_KEY` | Canonico | Attivo, unica env crypto supportata |
| `URBY_ENCRYPTION_KEY` | Alias retro-compat | **Rimosso** 2026-04-23 (commit `1005018`, O-D4-C) — chain non la legge piu'; i test tombstone in `tests2/crypto/test_crypto_key_sourcing_contract.py` impediscono re-introduzione silenziosa |
| `CONN_ENCRYPT_KEY` | Alias legacy pre-migrazione | **Rimosso** 2026-04-23 (commit `1005018`, O-D4-C) — come sopra |
| `URBY_DISABLE_DEFAULT_KEY` | Alias ridondante | **Rimosso** 2026-04-21 (commit `3924bfb`) — la funzionalita' e' gia' in `PLENORA_DISABLE_DEFAULT_KEY` |

Il fallback su `SECRET_KEY` / `JWT_SECRET_KEY` (D5) e' stato **rimosso** il 2026-04-20 (commit `9fc330a` + doc `81ac385`): quei nomi appartengono al dominio auth JWT e condividerli con crypto-at-rest rendeva la rotazione `JWT_SECRET_KEY` un'operazione silently distruttiva sui record cifrati. Review futuri che vedessero tentativi di re-introdurre quel fallback devono essere respinti: un secret, un consumatore, un nome canonico.

### Config-not-flag (registrati per trasparenza, non soggetti a §3)

- `ENRICHMENT_POOL_SIZE` (tuning numerico enrichment async)
- `FILE_SOURCE_S3_THRESHOLD_BYTES`, `TEMP_ORPHAN_CLEANUP_HOURS` (operational knobs)
- `CLOUD_PROVIDER` (provider enum architetturale)
- `STORAGE_POLICY` (enum `db | object_storage | hybrid`, ADR A1 — validato fail-fast all'avvio, vedi §1.7)
- `LOG_ARCHIVE_ENABLED`, `LOG_ARCHIVE_BUCKET`, `LOG_ARCHIVE_KEY_PREFIX`, `LOG_ARCHIVE_BATCH_ROWS` (parametri di deployment del cluster O-ARC — path archive-before-purge §1.4, ADR-0014). **Non sono toggle di feature**: `LOG_ARCHIVE_ENABLED` governa il path di lifecycle del log (archive+purge vs legacy purge diretto), mirror simmetrico di `STORAGE_POLICY`. Validati fail-fast all'avvio da `validate_storage_config()` (`backend/app/shared/cloud_backends.py`) quando `ENABLED=true`: bucket e prefix non vuoti, storage backend raggiungibile. Il flip `ENABLED=true` e' azione ops documentata nel runbook `orchestrate/phase_2_hardening/o_arc_runbook.md` (local-only), **non** un refactor di codice.
- `AUDIT_RETENTION_DAYS` (retention window `plenora.audit_actions`, int. `0 = archive disabilitato per audit_actions`; `>0 = archive-before-purge con cutoff a N giorni`. Ratificato ADR-0014 addendum §A Batch 2). Semantica `0=disabled` allineata con `LOG_RETENTION_DAYS` post Batch 3. Default 365.
- `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`, `LOGIN_LOCKOUT_SECONDS`, `JWT_EXPIRE_HOURS`, `DB_SCHEMA_WARN_GB`, `DB_SCHEMA_ERROR_GB`, `JWT_BLACKLIST_STALE_THRESHOLD_SEC` (soglie numeriche)
- `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION` (config deploy + lettura runtime in `admin_backups/service.py` per dashboard admin; convergenza su `BACKUP_BUCKET` tracciata in `runtime_config_audit.md §4 P1.4`), `BACKUP_SCHEDULE_CRON`, `TICKET_ATTACHMENT_MAX_BYTES` (config deploy)

Questi non sono toggle ma parametri di dominio; vivono vicini al consumatore. Non vanno promossi a `settings` se usati da un solo modulo, ma vanno documentati al primo uso.

---

## 4. Policy di uniformita'

### 4.1 Layer business (`backend/app/core/*`)

- **Dato critico** (write DB destinazione, encrypt, metadata di transazione, autorizzazione): **fail-fast**. Exception propaga fino al router, che la converte in HTTPException con `logger.error` prefisso `[CRASH]`.
- **Best-effort loggato** ammesso solo su:
  - cleanup (cursor close, engine dispose, connection return) → `logger.debug`
  - osservabilita' (metriche, tracing, audit secondari) → `logger.debug`/`info`
  - type detection / format probing prima del parse effettivo → `logger.debug` opzionale
- **Silent** (`except: pass` senza log) e' vietato ovunque tranne:
  - `app/utils/logger.py` stesso (no-recursion)
  - dipendenze opzionali (`except ImportError: pass` per feature flag-like: oracledb, shapely, ecc.)
  - ogni altro sito richiede commento + log.

### 4.2 Layer router (`backend/app/core/*/router.py` e `backend/app/routers/*`)

- Pattern: validazione input → chiamata service → `try/except` che mappa:
  - `ValueError` / eccezioni tipizzate del modulo → `HTTPException 400/404` con `str(e)` come detail
  - `Exception` generica → `logger.error(..., exc_info=True)` con prefisso `[CRASH]` + `HTTPException 500` con detail neutro (no leak stack a utente)
- `logger.warning` (non `error`) per rifiuti da policy lato utente (MIME invalido, file troppo grande, permesso insufficiente).

### 4.3 Shared e utils (`backend/app/shared/*`, `backend/app/utils/*`)

- **Fail-fast sui contratti**: validazione input solleva `TypeError`/`ValueError` con messaggi indirizzati allo sviluppatore.
- **Fallback retro-compat** ammessi solo se documentati nella docstring del modulo e presenti nella tabella §2.

### 4.4 Osservabilita'

- Logging, metriche, tracing, audit non devono mai propagare al business path. Se la scrittura di un log fallisce, l'operazione business prosegue. Questo vale per qualunque futuro telemetry hook.
- Al contrario, il business path non deve **dipendere** dall'osservabilita' per la correttezza: un log che non parte non cambia lo stato.

### 4.5 Documentazione codice

- Le docstring di modulo devono riflettere il codice attuale (post-migrazione SQLite→PG, post-consolidation). Docstring stale sono bug documentali.
- Nuovo codice: commenti solo dove il **perche'** non e' ovvio dal codice (invariant nascosto, workaround per bug esterno, vincolo legacy). I commenti `# TODO: remove X` devono indicare target (fase/data).

---

## 5. Regole di review applicate da Fase 1 in poi

Queste regole sono applicabili a ogni PR che apre codice in `backend/app/` o `frontend/src/`. Ogni PR che introduce **un nuovo branch, un nuovo fallback, un nuovo flag** deve rispondere per iscritto alle seguenti domande (review 1–7 del charter):

1. **Path canonico toccato?** Il cambio tocca un path elencato in §1? Se si', il nuovo path rispetta il principio dichiarato?
2. **Nuovo fallback?** Se introduci un `except` che non re-raise:
   - Motivo dichiarato (commento o PR description)?
   - Perimetro chiaro (quando scatta)?
   - Log esplicito al primo uso?
   - Piano di rimozione o giustificazione "permanente"?
   - Se le 4 sono sode → aggiornare §2.
3. **Nuovo flag?** Se introduci `os.getenv` o config toggle:
   - Appare in §3 con owner e rimozione attesa?
   - Non tiene vive due architetture parallele (charter base 7)?
4. **Duplicato introdotto?** Se il PR crea un `_v2`, `_new`, `_alt`, `_legacy`, risposta obbligata nel PR: perche' non unificare da subito? Giustificazione richiesta.
5. **Policy errore modulo coerente?** Se cambi il livello di log o la semantica di un except in un modulo, verifica la policy dominante in §1.6 e mantienila.
6. **Comportamento utente-visibile?** Se si', lo hai testato end-to-end (UI + E2E)? Se no, dichiaralo nel PR.
7. **Test invariant-preserving?** La suite backend+frontend passa sul branch? Se hai aggiunto un test di regressione, spiega quale invariant protegge.

Queste domande sono **bloccanti**: PR senza risposte esplicite su 2/3/4 va rimandata, non mergiata con "lo sistemiamo dopo".

---

## 6. Cosa NON e' invariant (lasciato a brief di fase)

Le seguenti decisioni **non** sono vincolate da questo documento e restano scope dei brief di fase o della governance di progetto:

- **Score target per stream Fase 1+ (FE, PERF, ACN, TESTS)**: definiti in `orchestrate/README.md` e nei brief di ogni stream.
- **Setup infrastruttura, worktree, CI/CD**: documentati in `orchestrate/phase_0_consolidation/setup.md` e affini.
- **Priorita' specifiche di refactor** (es. "prima i writer, poi i reader"): scelta per brief di fase, non invariant.
- **Target di performance, SLO, SLA**: scope Fase 1 PERF; non sono invariant architetturali.
- **Contratti API pubblici verso ICSC/RFI/ANIA**: governati fuori da questo doc (contratti commerciali + versioning API).
- **Decisioni aperte fascia D (residue dopo Phase 1)**:
  - **D3** IMDF loader/persist/storage boundaries — **chiuso Phase 2** (2026-04-23): design doc `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md` + execute plan `orchestrate/phase_2_hardening/d3_execute_plan.md` ratificati come `ADR-0016`. 5 commit impl atomici: `e86340e` (path_policy), `4d95a85` (hydrate validate-publish), `432b003` (site_id single source), `8593370` (persist decoupling), `0d1b851` (dichiarativi + invariant §9.6).
  - **D4** alias crypto retro-compat — **chiuso** 2026-04-23: `URBY_DISABLE_DEFAULT_KEY` rimosso in Phase 1 (`3924bfb`). `URBY_ENCRYPTION_KEY` + `CONN_ENCRYPT_KEY` rimossi in Phase 2 via O-D4-B (verifica SSH + rollout `.env` prod `PLENORA_ENCRYPTION_KEY` scenario A) + O-D4-C (commit `1005018` rimozione codice/test + commit doc). Tutti e tre gli alias crypto ora tombstoned nei contract test (`tests2/crypto/test_crypto_key_sourcing_contract.py`).
  - **D6** policy preview vs execute — **chiuso** post Phase 1 (micro-0bis): ratifica formale come `ADR-0012` in `backend/docs/architecture/decisions/0012-2026-04-21-preview-execute-policy.md`. Codice gia' allineato via S26 (`db9ef03` + `6db33c7`); design doc originale `orchestrate/phase_1_stabilization/d6_preview_execute_policy.md`. Le tre righe canoniche sono in §1.6 e il fallback autorizzato `_init_security_context(strict=False)` e' in §2.
  - D1 (`ENABLE_ASYNC_ENRICHMENT`) e D2 (`_read_csv_*`) chiusi in Fase 0; D5 chiuso in Phase 1 (`9fc330a` + `81ac385`); D4 chiuso Phase 2 (`3924bfb` + `1005018` O-D4-C); D6 chiuso post Phase 1 micro-0bis (`ADR-0012`); D3 chiuso Phase 2 (`ADR-0016`, 5 commit).
- **Triage residui Phase 1 — CHIUSI**:
  - S17 `app/services/file_service.py` silent → debug/warning logs (`91b7109`)
  - S18 `app/core/arricchisci/api_connector.py` retry loop (`60dadc7`)
  - S23 `app/core/carica/database_writer.py` cleanup silent → debug (`e006b56`)
  - S25 `app/core/eseguibili/router.py` (`ef3ba63`)
  - S26 preview swallow arricchisci+manipola (`db9ef03`, `6db33c7`)
  - S27 cluster IMDF — 5 commit (`92f0c31`, `9e1246b`, `be20d19`, `afe3aba`, `834bf91`)
  - S28 shared layer — triage conformita' 2026-04-20, no code change (dettaglio in `orchestrate/phase_1_stabilization/kickoff.md`)
- **Log lifecycle — RATIFICATO post cluster O-ARC Batch 1-4 (2026-04-21) + P-CLR (2026-04-22)** (ex carry-over Phase 2): il cluster O-ARC chiude la carry-over su append-only enforcement + retention + purge controllato di `plenora.logs` e `plenora.audit_actions`. Path canonico **archive-before-purge** implementato nel beat (§1.4), ratificato in `backend/docs/architecture/decisions/0014-2026-04-21-log-archive-pipeline.md` + addendum §A Batch 2 (env var `LOG_ARCHIVE_*` + `AUDIT_RETENTION_DAYS` + fail-fast `validate_storage_config`) + addendum §B Batch 4 (observability circolare via `pg_execute` diretta, §1.4). Il commit `a32c9f3` pre-esistente resta la genesi del bypass `SET LOCAL plenora.retention_cleanup='true'` — ora integrato nel writer come unico consumatore autorizzato. Flip `LOG_ARCHIVE_ENABLED=true` e' azione ops post-merge, documentata in `orchestrate/phase_2_hardening/o_arc_runbook.md` (local-only, non tracked). Nessun bypass manuale implicito della policy e' ammesso: il trigger `plenora.block_log_modifications` (migration `2026-04-21_log_immutable_triggers.sql`) resta l'enforcement; il bypass e' esposto unicamente al beat worker sullo stesso cursor di DELETE. **Cluster P-CLR chiuso 2026-04-22** (piano `orchestrate/phase_2_hardening/p_clr_finalization.md`, Option D pura ratificata): rimosso l'ultimo user path che toccava `DELETE plenora.logs` (`POST /logs/clear` + FE button + client + capability) — dettaglio §1.4. Cascade cleanup applicativi ora scoped a `log_runs`. Contract test anti-reintroduzione attivo. Nessun consumer applicativo residuo su DELETE `logs` al di fuori del beat. Rehydrate (lettura archive → ricostruzione riga DB) resta scope `P-VIEW`, non-Phase-2. Analogo rinvio esplicito 2026-04-23 per `P-RET` (dashboard retention admin superadmin, kickoff §2.3 — visibilita' job cron + dry-run + audit): resta non-Phase-2. Dati sottostanti gia' query-abili (observability beat `op='log_archive'` / `op='audit_archive'` in `plenora.logs` via ADR-0014 addendum §B; config `LOG_ARCHIVE_*` + `AUDIT_RETENTION_DAYS` leggibile via `app.config.settings`); estensione di `AdminSystemStatusPage` tramite `admin_system_status/service.py::get_system_status()` tecnicamente fattibile pre-deadline ma non giustificata da priorita' punto 3 bulk post-deploy 2026-05-05. Il dry-run richiederebbe parametro invasivo nei hooks archive di `backend/workers/beat_scheduler.py`, scoped a carry-over Phase 3.
- **A1 — CHIUSO Phase 2** (2026-04-23): track completata in 3 batch.
  - **A1-B1** bulk review chiamanti file storage — chiusa 2026-04-22, review `orchestrate/phase_2_hardening/a1_bulk_review.md`, 0 bypass (§1.7 + §1.7.bis aggiornati).
  - **A1-B2** provider MinIO P1 impl + boot check + unit tests — chiusa 2026-04-22, commit `a5956f7`, ADR-0015.
  - **A1-B3** contract parity matrix `policy x provider` — chiusa 2026-04-23 **a scope ridotto**, commit `141d894`. Baseline dual-path: 4 scenari forti (C1 roundtrip small, C3 list+prefix, C4 delete idempotent, C5 overwrite) × 2 provider (`AwsS3ObjectStorage`, `MinioObjectStorage`) via `moto` in-process (env `MOTO_S3_CUSTOM_ENDPOINTS` per intercept MinIO subclass); opt-in MinIO reale via env `MINIO_E2E_ENDPOINT_URL`. Esclusi dal baseline (documentati in `backend/tests2/shared/test_object_storage_contract.py`): C2 multipart >8MB — incompat nota boto3 >=1.40 + moto 5.x su flexible-checksum (validabile solo su opt-in real MinIO); C6 content_type default — non e' contract cross-provider (S3/moto/MinIO ritornano `binary/octet-stream`, non `application/octet-stream` della docstring). Nessun workaround applicato, zero tocchi runtime.
- **#7 Batch A dead helpers low-risk — CHIUSO** 2026-04-24: 10 commit atomici `23eba8d`, `b7fd332`, `815d2c2`, `f72cd4a`, `99a6ee0`, `5dd5738`, `2dad0bc`, `3d503fd`, `2335c80`, `d172f60` (sub `p7-a1..p7-a10`). **11 simboli rimossi** (9 backend + 2 frontend), **262 righe nette**. Scope §2.3.2/§2.3.3/§2.3.4/§2.3.8/§2.3.9 + §6.1 frontend utility dell'audit `orchestrate/phase_2_hardening/dead_code_and_oversized_modules_audit.md`. Metodologia: grep stretto + stringhe letterali + `git log -S` + inventario import + `ast.parse` + pytest mirato / vitest + vite build. Dettaglio operativo completo (tabella simboli, drift linee, evidenze 0-caller, decisioni operative) rinviato all'audit §8 off-repo. **Follow-up emerso ma NON eseguito** (vincolo zero-collaterali): `backend/app/utils/geo.py:397 geometry_to_wkt` top-level resta orfano a cascata dopo `[p7-a7]` rimozione `geodataframe_to_dataframe_wkt` (era suo unico caller); i match `_geometry_to_wkt` in `carica/database_writer.py` e `features/arcgis_client.py` sono metodi di classe distinti, live — candidato batch futuro dedicato, fuori scope `#7`. Batch B/C/D/E/F/G dell'audit §8 intatti.
- **#7 Batch B Features vertical slice — CHIUSO** 2026-04-24: 4 commit ratificati da **ADR-0017** (`backend/docs/architecture/decisions/0017-2026-04-24-features-vertical-status.md`). Split `[adr]` `a134637` (decisione) → `[p7-b1]` `47e32a3` (backend remove atomico) → `[p7-b2]` `9856fe7` (FE annotation doc) → `[invariants]` (questo). **Sub-A remove**: 7 Pydantic orfani in `backend/app/core/features/models.py` rimossi (`ServiceInfo`, `LayerInfo`, `FieldInfo` cascata, `LayerSchema`, `FeatureQueryRequest`, `FeatureQueryResult`, `FeatureWriteResult`), -86 righe nette. Zero perdita tipizzazione (gia' persa: handler usano `WriteRequest`/`CreateServiceRequest` inline in `routes.py:311+324` + `Query(...)` params + `response_model=dict`). **Sub-B keep**: 3 client FE (`queryLayer`, `writeFeatures`, `createFeatureService`) preservati con annotation doc pre-sezione `// QUERY` in `features.js`, contract test `tests/api/features.contract.test.js` intatto (3 scenari verdi). Perimetro lock: zero tocco a `routes.py`, `features_service.py`, `arcgis_client.py`, endpoint BE `/query/preview`/`/write`/`/create-service`, altri simboli live di `models.py` (5 `ArcGIS*` + `FeatureWriteRequest`), altre 11 export `features.js`. Dettaglio operativo (verifica pre-decisione, metodologia, follow-up post-launch, decisioni operative) rinviato all'audit §8 off-repo. Follow-up registrati in ADR-0017 §Follow-up: wiring UI 3 funzioni, migrazione inline models → models.py, decisione endpoint BE se nessun consumer emerge, `response_model` tipizzato 12 endpoint. Batch C/D/E/F/G dell'audit §8 intatti.
- **#7 Batch C DAL wrappers dead (settings/heartbeat/comparison_keys/list_all_projects) — CHIUSO** 2026-04-24: 2 commit `[p7-c1]` `d358069` (rimozione atomica 10 simboli) + `[invariants]` (questo). Nessun ADR (cleanup meccanico post-verifica 0-caller robusta, non decisione architettonica). **10 simboli rimossi** in `backend/app/db/config_db_service.py`: `list_all_projects` (L324), `get_comparison_keys`/`save_comparison_keys` (L440/L448), `update_beat_heartbeat`/`get_beat_heartbeat` (L822/L828), `get_setting`/`set_setting`/`get_app_setting`/`set_app_setting`/`delete_app_setting` (L837/L842/L849/L854/L861). Banner `# BEAT HEARTBEAT` + `# APP SETTINGS` rimossi perche' dedicati 1:1 ai simboli; banner `# COMPARISON KEYS` preservato (copre ancora `delete_comparison_keys` live in `project_service.py:447`). **-79 righe nette**. **Rischio medio audit sciolto da verifica extra-repo**: writer/reader `beat_heartbeat` live usano SQL raw diretto (`workers/tasks/schedule_task.py:155`, `workers/beat_scheduler.py:601`, `app/core/scheduler/router.py:71`); `db_service.py` facade importa `list_projects` scoped, non `list_all_projects`; scripts/deploy.sh puliti; settings v1+v2 confermate "due iterazioni storiche mai adottate, `.env` + `app.config.settings` canonical" (audit §2.3.1). Perimetro lock: **zero tocco** a schema DB (`config_schema.sql` tabelle `comparison_keys`/`beat_heartbeat`/`app_settings` intatte), zero tocco a path SQL raw live, zero tocco alle altre 49 funzioni di `config_db_service.py`, zero split architetturale (scope Batch G post-launch). 2 failure preesistenti `tests2/functional/projects/*` (file upload smoke + auth roundtrip) verificate su baseline `61e7799` via `git stash` — non correlate a `[p7-c1]`, candidate ops DB-test infrastrutturali fuori `#7`. Dettaglio operativo rinviato all'audit §8 off-repo. Follow-up registrati: decisione schema `app_settings` post-rimozione wrapper (Batch G/post-launch), split `config_db_service.py` 864 LOC (Batch G post-launch). Batch D/E/F/G dell'audit §8 intatti.
- **#7 Batch D frontend transforms fossil pre-registry — CHIUSO** 2026-04-24: 2 commit `[p7-d1]` `498e755` (rimozione verticale atomica) + `[invariants]` (questo). Nessun ADR. **Perimetro espanso vs audit §3.2 originale** (12 → 13 simboli + 2 file interi) motivato da scoperta barrel broken emersa in ricognizione. **File eliminati**: `frontend/src/components/transforms/transformOperations.js` (118 LOC, 11 export tutti 0-caller) + `frontend/src/components/transforms/index.js` (63 LOC, barrel broken con 22 default-import da `./base/*` + `./spatial/*` **cartelle inesistenti** + 1 `useTransformValidation` file inesistente + `configComponents` map + re-export di transformOperations). **Rimozioni puntuali**: `staticValuesNodeConfig` da `StaticValuesNode.jsx:272` + `validateCaricanConfig` (+ commento dedicato) da `CaricanNodeConfig.js:49`. **-237 righe nette**. **Path live confermato**: `TransformNode.jsx:14` + `ColumnBuilderModal.jsx:10` → `frontend/src/api/transforms.js` → `GET /api/transforms/registry` → `backend/app/core/manipola/transforms_registry.json` (2968 LOC, 70 entry). Perimetro lock: zero tocco a `TransformNode`/`ParameterInput`/`MultiJoinNode`/`PivotMappingBuilder`/`SourceSwapper`/`DynamicConfig`/`ColumnBuilderModal` (tutti live), zero tocco a `api/transforms.js` + registry BE, default export `memo(StaticValuesNode)` + `createStaticValuesNode` preservati live, altri 4 export di `CaricanNodeConfig.js` preservati. Metodologia (stile Batch A/B/C): grep stretto + stringhe letterali + `git log -S` + import inventory + `ls` verifica phantom paths + vitest full (174 passed) + vite build (tree-shaking pulito). Dettaglio operativo (scoperta barrel broken, metodologia, decisioni operative) rinviato all'audit §8 off-repo. **Follow-up registrati**: (a) `CaricanNodeConfig.js` file-level potenzialmente orfano (grep `from.*CaricanNodeConfig` → zero match; altri 4 export da verificare, fuori scope Batch D per vincolo utente); (b) audit generale altri barrel patterns nel FE per cicatrici architettoniche analoghe. Batch E/F/G dell'audit §8 intatti.
Queste aree restano aperte: se toccate in Fase 1+, il PR deve dichiarare la scelta con la stessa disciplina delle 7 regole in §5, anche se il punto non e' ancora in tabella §2/§3.

---

**Fine documento.** Ogni modifica a questo file richiede un commit dedicato con prefisso `[invariants]`. Cambiare un'invariant significa decidere esplicitamente che l'architettura cambia: non e' un refactor silenzioso.
