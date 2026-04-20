# CONSOLIDATION — Report Fase 0

**Status**: fase_d_completed — done salvo test suite + smoke E2E pre-chiusura
**Last updated**: 2026-04-20

## Fase A — Inventario
- [x] inventory_report.md committato (`backend/docs/architecture/inventory_report.md`)
- [x] Secondo triage completato in chat (shortlist S1–S28 + investigate D1–D19)
- [x] Checkpoint utente ricevuto: approvato subset esplicito per Fase B
- Numeri (pre-checkpoint):
  - **8** famiglie di path duplicati
  - **~152** siti grezzi di fallback (121 `except: pass` + 31 `except: return default`), **~10** cluster critici
  - **6** feature flag veri + **3** alias retro-compat + **3** config-not-flag
  - **24** moduli mappati per policy errore + **6** coppie cross-modulo anomale, **1** silent puro

## Checkpoint utente (2026-04-19) — subset approvato per Fase B

Fonte di verita': `backend/docs/architecture/inventory_report.md` Sezione 5.

### APPROVATI (5 item, da eseguire in Fase B appena ricevuto go esplicito)
- **S1** — `app/shared/crypto.py:213-218` — rimuovere silent fallback encrypt→plaintext, `raise RuntimeError`
- **S2** — `app/core/carica/database_writer.py:195-240` — 4 introspection: rimuovere `return False/[]` su errore, propagare eccezione o usare `Optional[T]`
- **S3** — `app/core/features/features_service.py:1066-1070` — silent geopandas→CSV: aggiungere log + validazione estensione
- **S4** — `app/core/features/{features_service.py, features_service_db.py}` — merge `_db` dentro `FeaturesService`, rimuovere file `_db.py`
- **S5** — `app/core/auth/auth.py:989-991` — silent swallow `delete_group_cascade`: rimuovere o motivare esplicitamente

### CONDIZIONALI (7 item, stesso batch solo se locali/meccanici senza allargare perimetro)
- **S9** — `app/core/carica/bulk_writers.py:208-212` — cursor close silent
- **S10** — `app/db/query_executor.py` (7 siti) — cleanup pool/session: aggiungere `logger.debug` uniforme
- **S11** — `app/core/logs/router.py:21-24` — JSON parse silent: `logger.debug` + commento
- **S12** — `app/db/pg_connection.py:242-245` — silent putconn: `logger.warning`
- **S13** — `app/core/scheduler/execution_manager.py:576-585` — Oracle `ALTER SESSION` silent: `logger.debug`
- **S15** — `app/core/admin_backups/service.py:134-135` — silent AttributeError: `logger.warning`
- **S16** — `app/core/files/router.py:153-154, 241-246` — uniformare log level

### NON APPROVATI (tutto il resto)
- **S6, S7, S8, S14, S17–S28**: richiedono decisione utente separata, perimetro troppo esteso o evidenza insufficiente.
- **D1–D19** (fascia investigate-only): include `ENABLE_ASYNC_ENRICHMENT`+`_execute_parallel*` unify, IMDF loader/persist/storage, alias crypto retro-compat, `SECRET_KEY`/`JWT_SECRET_KEY` fallback, policy preview vs execute cross-modulo. Nessuno in Fase B corrente.

## Fase B — Consolidation execute (2026-04-19 / 2026-04-20)

**Status**: eseguita sul subset approvato. Vincoli operativi hard (niente explore/subagents/parallelizzazione, commit atomici, no `git add -A`) rispettati.

Commit list (in ordine cronologico):
- `0845650` S2: introspection fail-fast in DatabaseWriter (4 metodi: `table_exists`, `get_existing_columns`, `get_schemas`, `get_tables`)
- `d25d00a` S5: log `[CRASH]` + HTTPException 500 su `delete_group_cascade` errors
- `af642c0` S3: fail-fast su `_load_dataframe`, no silent GeoJSON→CSV fallback (solo `.tsv/.txt` fallback autorizzato)
- `b9206ee` S4: merge `features_service_db.py` dentro `FeaturesService` (file `_db` rimosso)
- `a7beca5` S4: refresh docstring top-level `features_service.py`
- `b8bde62` S9: log debug su `cursor.close()` failure in bulk COPY
- `7d330c6` S11: log debug su JSON parse fallito in `logs/_row_to_dict`
- `f5c340f` S12: log warning su `putconn()` fallito in `pg_connection`
- `198289a` S15: log warning su `duration_sec` calc fallito in `admin_backups`
- `3c6ff2a` S16: uniforma log level (`warning`) su MIME rejection in `files/router` (preview + upload)
- `45e04ef` S13: close as inventory error — il path indicato non conteneva `ALTER SESSION`; coperto da S10 (aperto)

S1 (crypto encrypt fail-fast): applicato in `eab1c65` (reconcile snapshot pre-consolidation); verificato in Fase C che la `raise RuntimeError("crypto.encrypt broken — refusing to save plaintext")` e' live.

Totale: **10 item S-serie applicati** + 1 chiuso come inventory error. Subset 5.1 (5 item) + 5.2 parziale (5 item su 7 approvati: S9/S11/S12/S15/S16) eseguito; S10 (7 siti cleanup in `query_executor.py`) **rimandato** come da scope 5.2 ("locale/meccanico") — e' locale ma volume elevato, meglio fuori Fase 0 per non estendere il batch.

## Fase C — Invariants
- [x] `backend/docs/architecture/invariants.md` pubblicato (2026-04-20)
- Struttura: 6 sezioni (core path canonici, fallback autorizzati, feature flag vivi, policy di uniformita', regole review, cosa NON e' invariant)
- Principio guida: riflette il codice post-consolidation reale, non un brief astratto; ogni riga e' stata verificata nel sorgente
- Debito noto lasciato esplicito:
  - S10 (query_executor 7 siti silent cleanup) — da risolvere fuori Fase 0
  - `ENABLE_ASYNC_ENRICHMENT` + dual path `_execute_parallel*` — violazione charter base 7 aperta, rimozione obbligata prima di chiudere Fase 1
  - Alias retro-compat crypto (URBY_*, CONN_ENCRYPT_KEY, SECRET_KEY/JWT_SECRET_KEY) — decisione D-fascia post-deploy verification
  - Triage residui `file_service.py`, `api_connector.py`, `intermediate/writer.py`, stale docstrings

## Fase D — Brief Fase 1
- [x] 4 brief aggiornati in `orchestrate/phase_1_stabilization/`:
  - `brief_fe.md` — aggiunto punto 3 in "Lettura obbligatoria" + regola 7 in "Regole disciplina" (framework invariants applicato per simmetria al codice FE)
  - `brief_acn.md` — aggiunto punto 3 in "Lettura obbligatoria" con focus su §1.1/§2/§3 crypto + regola 7 (nuovi flag/fallback `@audit_action`/MFA/SSM devono essere registrati in §2/§3)
  - `brief_perf.md` — aggiunto punto 3 in "Lettura obbligatoria" con focus sul path canonico `DestinationWriter` e flag `CARICA_BULK_INSERT` + regola 7 (benchmark verde non basta se invariant violato)
  - `brief_tests.md` — aggiunto punto 3 in "Lettura obbligatoria" come fonte dei contract + regola 7 (test blindano invariants o annotano gap, no mock di fallback non autorizzati)
- Wiring uniforme: ogni brief cita `backend/docs/architecture/invariants.md` come lettura obbligatoria e richiama le 7 regole di review in §5 per nuovi branch/fallback/flag

## Test
- Backend: non eseguito in Fase C (sessione documentale). Esecuzione da considerare prima della chiusura complessiva di Fase 0
- Frontend: non eseguito (nessun file frontend toccato in Fase B)
- Smoke E2E manuale: non eseguito — da programmare pre-chiusura Fase 0

## Bug trovati fuori scope

### Chiusi in Fase B
- `backend/app/core/auth/auth.py:989-991` — silent swallow su `delete_group_cascade` → chiuso da **S5** (`d25d00a`)
- `backend/app/core/features/features_service_db.py` — file rimosso, logica integrata in `FeaturesService` (**S4** `b9206ee`); docstring top-level rigenerata (`a7beca5`)
- `backend/app/core/features/features_service.py:1-13` stale docstring storage JSON → aggiornata da `a7beca5`

### Ancora aperti (fuori Fase 0)
- Docstring stale `backend/app/services/connection_service.py:1-11` (parla di "SQLite tramite app.db.db_service") — cleanup documentale da fare fuori Fase 0
- `backend/app/core/features/features_service.py:1249-1253` claim "per evitare dipendenza circolare" non verificato — candidato per unify con `app/utils/dataframe._read_csv_safe` in fase successiva
- `backend/app/db/query_executor.py` 7 siti silent cleanup (S10 rimandato) — intervento meccanico ma volume elevato, dedicarci un batch separato
- `ENABLE_ASYNC_ENRICHMENT` + dual path `_execute_parallel` / `_execute_parallel_async` — violazione charter base 7, richiede decisione prod (vedi §3 di `invariants.md`)
- Eventuali bug del nodo `result` colonne (cross-modulo estrai/manipola/arricchisci) — gia' tracciato in memoria `project_arricchisci_result_columns_bug.md`, fuori scope consolidamento

## Deps nuove richieste
- Nessuna

## Proposte Fase 1
- (da definire a fine Fase 0)

## Addendum re-pass (2026-04-20)

Questo addendum aggiorna il report senza riscrivere la traccia storica del primo checkpoint.

- `S10` e' chiuso su `main` con `8df515a`
- `S13` resta chiuso come inventory error
- `692f532` pubblica `backend/docs/architecture/invariants.md` e rimuove artefatti backend stale

### Stato reale dopo il re-pass

- `Phase 0` non e' ancora chiusa
- resta un ultimo pass di sostanza
- dopo quel pass il residuo e' dominato da decisioni di prodotto/env, policy cross-modulo o rumore

### Shortlist ultimo pass Phase 0

- `connection_service.py:1-11` docstring stale
- `D2` unify `_read_csv_safe` / `_read_csv_with_encoding_fallback`
- `S19` `app/core/intermediate/writer.py`
- `S20` `app/core/datasources/sharepoint.py`
- `D1` `ENABLE_ASYNC_ENRICHMENT` come checkpoint separato (serve verifica env prod prima della scelta)

### Riaprire dopo / batch dedicato

- `S17` `file_service.py`
- `S18` `api_connector.py`
- `S23` `database_writer.py`
- `S25` `eseguibili/router.py`
- `S27` cluster IMDF

### Tenere per fase successiva

- `S26` preview swallow estrai/manipola/arricchisci
- `S28` shared layer mixed
- `D3` IMDF loader/persist/storage boundaries
- `D4` alias crypto retro-compat
- `D5` `SECRET_KEY`/`JWT_SECRET_KEY` come crypto fallback
- `D6` policy preview vs execute cross-modulo

### Declassati a rumore / inventory overcount

- `S21` `config_db_service.py`
- `S22` `features/routes.py`
- `S24` `admin_system_status/service.py`
- `S6`, `S7`, `S8`, `S14`
