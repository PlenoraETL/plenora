# URBY-ETL — Architecture Invariants

**Riferimento vincolante**: code charter del progetto (regole base 1/4/5/6/7/8/9, review 1–7, arch 1/2/3/4/5). Questo documento e' il ponte tra il charter astratto e il codice concreto post-Fase 0.

**Stato**: pubblicato al termine di Fase 0 consolidation e aggiornato al termine di Fase 1 stabilization. Phase B + re-pass chiusi: S1-S5, S9-S13, S15-S16, S19-S20 applicati; D1 (`ENABLE_ASYNC_ENRICHMENT`) parity sync/async sanata + kill-switch mantenuto; D2 (`_read_csv_*`) unificato. Phase 1 chiusi: S17, S18, S23, S25, S26, S27 (commit atomici), S28 (triage conformita' 2026-04-20); D5 (SECRET_KEY/JWT_SECRET_KEY fallback) rimosso; Architecture A1 storage policy validate + dispatch simmetrici (commit `2a80970`+`1176c32`+`c5578db`). Residui veri classificati in §6.

**Aggiornato**: 2026-04-21 (post Phase 1 stabilization)

**Ambito**: le invarianti qui elencate valgono per il codice `backend/app/` e vincolano ogni nuovo commit da Fase 1 in poi. Le scelte di prodotto o di performance restano fuori scope.

---

## 1. Core path canonici

Per ogni operazione fondamentale dichiariamo **il principio**, non l'implementazione specifica. L'implementazione concreta puo' evolvere (rename, refactor) senza violare il principio, purche' resti uno solo il path di default e le ottimizzazioni vivano isolate.

### 1.1 Scrittura su DB destinazione

- **Principio**: il path canonico passa per la strategia `DestinationWriter` (`backend/app/core/carica/writer_base.py`), la cui unica implementazione attiva e' `DatabaseWriter` (`backend/app/core/carica/database_writer.py`). Un solo entry point, una sola policy di transazione, un solo modello di errore.
- **Ottimizzazioni native isolate (charter arch 1/5)**: `bulk_writers.py` (COPY Postgres, LOAD DATA MySQL) viene tentato solo quando il dialect lo supporta; il fallback a `executemany` e' governato da `BulkInsertNotSupportedError` con log esplicito. Il kill-switch operazionale e' `CARICA_BULK_INSERT` (vedi §3.3).
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

### 1.5 Config loading

- **Principio**: l'entry point canonico e' `app.config.settings` (`backend/app/config.py`, classe `Settings` con singleton `settings`). I valori ambientali transitano tutti da qui, con default, type coercion e validazione centralizzate.
- **Eccezioni documentate**: alcuni moduli leggono direttamente `os.getenv` per:
  - feature flag locali (`ENABLE_ASYNC_ENRICHMENT`, `CARICA_BULK_INSERT`, `MSSQL_FAST_EXECUTEMANY`, `URBY_ALLOW_LOOPBACK_OUTBOUND`) — il flag vive vicino al codice che lo consuma per ridurre indirezione; deve comunque comparire nella tabella §3
  - crypto key sourcing (`app/shared/crypto.py:_get_raw_key`) — la chain di 5 env var e' deliberata (alias retro-compat post-migrazione), documentata nella docstring del modulo
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
| `app/core/estrai/`, `manipola/`, `arricchisci/` | Misto preview-vs-execute | Convenzione (non ancora invariant): execute = fail-fast; preview = best-effort loggato. Unificazione rimandata (D-fascia) |
| `workers/` | Fail-fast su raise Celery, best-effort su cleanup intermediate | Invariato in Fase 0 |
| `app/utils/` | Fail-fast | Pattern uniforme; `logger.py` e' l'unica eccezione motivata (no-recursion) |

**Vincolo**: una PR che cambia policy errore di un modulo deve dichiararlo e argomentare perche'. Sposare "silent" non e' mai ammesso.

### 1.7 Storage dispatch (result / file)

- **Principio**: ogni scrittura/lettura di artefatti di risultato e file sorgente passa per un dispatch policy-aware. La policy e' `STORAGE_POLICY` con valori `db | object_storage | hybrid`, caricata via `app/config/storage_config.py` e validata fail-fast all'avvio da `validate_storage_config()` (ADR A1, commit `2a80970`).
- **Entry point canonici**:
  - `app/core/*/result_store.py` — dispatch risultati per modulo (result_store policy-aware, commit `1176c32`)
  - `app/services/file_service.py` — dispatch file sorgente (pattern FS-4 simmetrico, commit `c5578db`)
- **Provider disponibili**: DB locale (Postgres) + AWS S3. MinIO come provider P1 designato (implementazione in Phase 2). Azure/GCP fuori scope (A1 §8).
- **Vincolo**: nessun accesso diretto a bucket S3 / filesystem / DB bytea fuori da questi entry point. Se un modulo legge/scrive artefatti bypassando il dispatch, e' una violazione della policy e va corretta, non normalizzata via fallback locale.
- **Config-not-flag**: STORAGE_POLICY non e' un toggle (niente `on`/`off`) ma un enum di deployment. E' registrato in §3 Config-not-flag per trasparenza.

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
| `URBY_ALLOW_LOOPBACK_OUTBOUND` | `app/shared/api_utils.py` | 2026-04-10 | Test-only: consente loopback in outbound validation | Attivo solo in test harness | Mai (test infra) |
| `PLENORA_DISABLE_DEFAULT_KEY` | `app/shared/crypto.py` | 2026-04-10 | Dev opt-in per replicare fail-fast di prod | Attivo | Mai (dev ergonomy) |
| `ENABLE_ASYNC_ENRICHMENT` | `app/core/arricchisci/enrichment_service.py` | 2026-04-10 (security audit) | Routing tra `_execute_parallel` (sync) e `_execute_parallel_async` (async) | Kill-switch operazionale post-D1. Verifica prod 2026-04-20: `true` in `/home/ubuntu/plenora-etl/backend/.env`. Parity rate-limiter + error granularity sanata (D1); divergenze residue accettate e documentate (early stop, progress) | Rimozione non prevista (safety valve analogo a `CARICA_BULK_INSERT`) |

### Env var crypto-key (coda di §1.5)

La chain di 3 nomi per la chiave Fernet e' deliberata ma in uscita.

| Env var | Tipo | Stato |
|---------|------|-------|
| `PLENORA_ENCRYPTION_KEY` | Canonico | Attivo, preferito in tutti i nuovi deploy |
| `URBY_ENCRYPTION_KEY` | Alias retro-compat | In uscita, D4 parziale in Phase 1 — **bucket operation** post-Phase 1: richiede verifica `.env` su `lab.plenora.it` via SSH prima della rimozione codice |
| `CONN_ENCRYPT_KEY` | Alias legacy pre-migrazione | In uscita, D4 parziale in Phase 1 — **bucket operation** post-Phase 1: richiede verifica SSH `.env` prod prima della rimozione codice |
| `URBY_DISABLE_DEFAULT_KEY` | Alias ridondante | **Rimosso** 2026-04-21 (commit `3924bfb`) — la funzionalita' e' gia' in `PLENORA_DISABLE_DEFAULT_KEY` |

Il fallback su `SECRET_KEY` / `JWT_SECRET_KEY` (D5) e' stato **rimosso** il 2026-04-20 (commit `9fc330a` + doc `81ac385`): quei nomi appartengono al dominio auth JWT e condividerli con crypto-at-rest rendeva la rotazione `JWT_SECRET_KEY` un'operazione silently distruttiva sui record cifrati. Review futuri che vedessero tentativi di re-introdurre quel fallback devono essere respinti: un secret, un consumatore, un nome canonico.

### Config-not-flag (registrati per trasparenza, non soggetti a §3)

- `ENRICHMENT_POOL_SIZE` (tuning numerico enrichment async)
- `FILE_SOURCE_S3_THRESHOLD_BYTES`, `TEMP_ORPHAN_CLEANUP_HOURS` (operational knobs)
- `CLOUD_PROVIDER` (provider enum architetturale)
- `STORAGE_POLICY` (enum `db | object_storage | hybrid`, ADR A1 — validato fail-fast all'avvio, vedi §1.7)
- `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`, `LOGIN_LOCKOUT_SECONDS`, `JWT_EXPIRE_HOURS`, `DB_SCHEMA_WARN_GB`, `DB_SCHEMA_ERROR_GB`, `JWT_BLACKLIST_STALE_THRESHOLD_SEC` (soglie numeriche)
- `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`, `BACKUP_SCHEDULE_CRON`, `TICKET_ATTACHMENT_MAX_BYTES` (config deploy)

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
  - **D3** IMDF loader/persist/storage boundaries — design doc `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md` consolidato, **nessun refactor codice applicato in Phase 1**. Carry-over **bucket phase_2** (refactor boundaries dedicato).
  - **D4** alias crypto retro-compat — parziale: `URBY_DISABLE_DEFAULT_KEY` rimosso in Phase 1 (`3924bfb`). `URBY_ENCRYPTION_KEY` + `CONN_ENCRYPT_KEY` → **bucket operation**: bloccato su verifica `.env` produzione via SSH su `lab.plenora.it` prima della rimozione codice.
  - **D6** policy preview vs execute — design doc `orchestrate/phase_1_stabilization/d6_preview_execute_policy.md` proposto; la parte tattica "no silent swallow" e' gia' applicata via S26 (`db9ef03` + `6db33c7`). **Bucket micro_0bis**: ratifica formale come ADR in `backend/docs/architecture/decisions/`.
  - D1 (`ENABLE_ASYNC_ENRICHMENT`) e D2 (`_read_csv_*`) chiusi in Fase 0; D5 chiuso in Phase 1 (`9fc330a` + `81ac385`); D4 parziale (`3924bfb`).
- **Triage residui Phase 1 — CHIUSI**:
  - S17 `app/services/file_service.py` silent → debug/warning logs (`91b7109`)
  - S18 `app/core/arricchisci/api_connector.py` retry loop (`60dadc7`)
  - S23 `app/core/carica/database_writer.py` cleanup silent → debug (`e006b56`)
  - S25 `app/core/eseguibili/router.py` (`ef3ba63`)
  - S26 preview swallow arricchisci+manipola (`db9ef03`, `6db33c7`)
  - S27 cluster IMDF — 5 commit (`92f0c31`, `9e1246b`, `be20d19`, `afe3aba`, `834bf91`)
  - S28 shared layer — triage conformita' 2026-04-20, no code change (dettaglio in `orchestrate/phase_1_stabilization/kickoff.md`)
- **Carry-over architetturale Phase 2**: log lifecycle complessivo (append-only enforcement formale, retention policy, purge controllato). Il commit `a32c9f3` applica `SET LOCAL plenora.retention_cleanup` al beat job di retention — risolve **solo** la coesistenza con i trigger log-immutable per il job autorizzato, **non** il lifecycle complessivo. Nessun bypass manuale implicito della policy e' ammesso.
- **Carry-over architetturale Phase 2**: A1 follow-up — provider MinIO P1 implementation + contract test per matrice policy/provider + bulk review dei chiamanti file storage per allineamento al dispatch canonico §1.7.
- **Strategia di rimozione alias retro-compat** (`URBY_*` → `PLENORA_*`): richiede coordinamento con deploy prod (`lab.plenora.it`) prima di rimuovere dal codice. Scope product/env, non invariant.

Queste aree restano aperte: se toccate in Fase 1+, il PR deve dichiarare la scelta con la stessa disciplina delle 7 regole in §5, anche se il punto non e' ancora in tabella §2/§3.

---

**Fine documento.** Ogni modifica a questo file richiede un commit dedicato con prefisso `[invariants]`. Cambiare un'invariant significa decidere esplicitamente che l'architettura cambia: non e' un refactor silenzioso.
