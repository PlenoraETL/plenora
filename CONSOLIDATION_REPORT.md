# CONSOLIDATION — Report Fase 0

**Status**: checkpoint_completed — in attesa di go esplicito per Fase B
**Last updated**: 2026-04-19

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

## Fase B — Consolidation execute
- **Status**: bloccata — attendo go esplicito su subset 5.1 (+5.2 se mechanical) dal checkpoint
- **Nota S13 (2026-04-20)**: chiuso come **inventory error**. Il riferimento originale `app/core/scheduler/execution_manager.py:576-585` non contiene `ALTER SESSION`; i siti reali pertinenti sono già coperti da **S10** in `app/db/query_executor.py`. Possibile candidato separato fuori scope: `backend/app/core/carica/database_writer.py:2198-2204`.
- Vincoli operativi hard (dichiarati dall'utente):
  - Niente explore agents, niente subagents, niente parallelizzazione
  - Commit atomici piccoli, prefisso `[consolidation]`
  - Niente `git add -A`: worktree ha 1230 entries pre-esistenti dalla migrazione `backend/`; commit solo dei file toccati
  - Ogni item condizionale (5.2) che si riveli non-locale o non-meccanico va segnalato e rimandato, non esteso

## Fase C — Invariants
- [ ] invariants.md pubblicato (da redigere dopo Fase B)

## Fase D — Brief Fase 1
- [ ] 4 brief aggiornati

## Test
- Backend: non eseguito (Fase A read-only)
- Frontend: non eseguito (Fase A read-only)
- Smoke E2E manuale: non eseguito

## Bug trovati fuori scope (NON fissati in Fase A, S5 ora in scope Fase B)
- Docstrings stale post-migrazione PG:
  - `backend/app/services/connection_service.py:1-11` — parla di "SQLite tramite app.db.db_service"
  - `backend/app/core/features/features_service.py:1-13` — descrive filesystem JSON storage
  - `backend/app/core/features/features_service_db.py:1-7` — descrive "patch" verso SQLite (rilevante per S4)
- `backend/app/core/features/features_service.py:1076-1078` — claim "per evitare dipendenza circolare" non verificato (non in Fase B corrente)
- `backend/app/core/auth/auth.py:989-991` — silent swallow su `delete_group_cascade` → **promosso a S5, incluso in Fase B**
- Eventuali bug del nodo `result` colonne (cross-modulo estrai/manipola/arricchisci) — gia' tracciato in memoria `project_arricchisci_result_columns_bug.md`, fuori scope consolidamento

## Deps nuove richieste
- Nessuna

## Proposte Fase 1
- (da definire a fine Fase 0)
