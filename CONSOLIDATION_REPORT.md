# CONSOLIDATION — Report Fase 0

**Status**: **done** — codice, documentazione e gate chiusi. BE `pytest tests2/` verde (1945+8), FE `npm run test -- --run` verde (134), smoke E2E manuale eseguito senza regressioni. Caveat locali ambientali registrati come handoff non-blocker a Phase 1 (storage policy) in `orchestrate/phase_0_consolidation/integration_notes.md`.
**Last updated**: 2026-04-20 (closeout finale — gate verdi + smoke chiuso con handoff Phase 1)

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

**Batch iniziale (subset approvato 5.1 + 5.2)**:
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
- `45e04ef` S13: close as inventory error — il path indicato non conteneva `ALTER SESSION`; coperto da S10

**Re-pass 2026-04-20** (dopo pubblicazione invariants):
- `8df515a` S10: `logger.debug` uniforme sui 7 siti cleanup pool/session in `query_executor.py`
- `692f532` pubblicazione `backend/docs/architecture/invariants.md` + rimozione artefatti backend stale
- `cc5b89f` D1: harden `_execute_parallel_async` (rate limiter + error granularity)
- `fc18e35` D1: parity tests sync vs async enrichment
- `a365a82` S20: log debug/warning sui 5 silent in `datasources/sharepoint`
- `b5c2ce6` S19: log debug sui 2 silent cleanup in `intermediate/writer`
- `c97be5a` D2: unify `_read_csv_*` su `app.utils.dataframe._read_csv_safe`

S1 (crypto encrypt fail-fast): applicato in `eab1c65` (reconcile snapshot pre-consolidation); verificato in Fase C che `raise RuntimeError("crypto.encrypt broken — refusing to save plaintext")` e' live.

Totale: **14 item S-serie applicati** (S1–S5, S9–S13, S15–S16, S19–S20) + **2 D-serie** (D1, D2) + 1 chiuso come inventory error (S13). Subset 5.1 (5 item) + 5.2 completo (7 item) + re-pass 2026-04-20 (S10 + S19 + S20 + D1 + D2). Classificazione finale dei residui in sezione "Closeout Phase 0" sotto.

## Fase C — Invariants
- [x] `backend/docs/architecture/invariants.md` pubblicato (2026-04-20)
- Struttura: 6 sezioni (core path canonici, fallback autorizzati, feature flag vivi, policy di uniformita', regole review, cosa NON e' invariant)
- Principio guida: riflette il codice post-consolidation reale, non un brief astratto; ogni riga e' stata verificata nel sorgente
- Debito noto lasciato esplicito (aggiornato post re-pass):
  - `ENABLE_ASYNC_ENRICHMENT` + dual path `_execute_parallel*` — D1 chiuso: parity sync/async sanata (rate limiter + error granularity), flag promosso a kill-switch autorizzato in §2/§3 di `invariants.md`. Non piu' "violazione", ora safety valve documentato.
  - Alias retro-compat crypto (`URBY_*`, `CONN_ENCRYPT_KEY`, `SECRET_KEY`/`JWT_SECRET_KEY`) — decisione product/env post-deploy verification (D4/D5)
  - Triage residui `file_service.py` (S17), `api_connector.py` (S18) — batch Phase 1 dedicato
  - S10 (`query_executor.py` 7 siti) e S19 (`intermediate/writer.py`) chiusi nell'ultimo pass; rimossi dal debito noto

## Fase D — Brief Fase 1
- [x] 4 brief aggiornati in `orchestrate/phase_1_stabilization/`:
  - `brief_fe.md` — aggiunto punto 3 in "Lettura obbligatoria" + regola 7 in "Regole disciplina" (framework invariants applicato per simmetria al codice FE)
  - `brief_acn.md` — aggiunto punto 3 in "Lettura obbligatoria" con focus su §1.1/§2/§3 crypto + regola 7 (nuovi flag/fallback `@audit_action`/MFA/SSM devono essere registrati in §2/§3)
  - `brief_perf.md` — aggiunto punto 3 in "Lettura obbligatoria" con focus sul path canonico `DestinationWriter` e flag `CARICA_BULK_INSERT` + regola 7 (benchmark verde non basta se invariant violato)
  - `brief_tests.md` — aggiunto punto 3 in "Lettura obbligatoria" come fonte dei contract + regola 7 (test blindano invariants o annotano gap, no mock di fallback non autorizzati)
- Wiring uniforme: ogni brief cita `backend/docs/architecture/invariants.md` come lettura obbligatoria e richiama le 7 regole di review in §5 per nuovi branch/fallback/flag

## Test
- Backend: `pytest tests2/` verde (1945 passed + 8 skipped, 2026-04-20)
- Frontend: `npm run test -- --run` verde (134 passed, 2026-04-20)
- Smoke E2E manuale: residuo gate pre-chiusura formale — da programmare

## Deps nuove richieste

Nessuna.

## Proposte Fase 1

Brief in `orchestrate/phase_1_stabilization/` (`brief_fe.md`, `brief_acn.md`, `brief_perf.md`, `brief_tests.md`) gia' allineati a `invariants.md`. Residui micro-consolidamento listati in Closeout sotto.

## Closeout Phase 0

Dopo il re-pass 2026-04-20 il lavoro codice Phase 0 e' concluso. Classificazione finale dei residui in 4 bucket:

### Phase 0 closed items (tutti applicati su `main`)

- **S-serie**: S1 (crypto encrypt fail-fast), S2 (DatabaseWriter introspection), S3 (features `_load_dataframe`), S4 (features_service merge), S5 (`delete_group_cascade` log+raise), S9 (bulk cursor close debug), S10 (query_executor 7 siti cleanup debug), S11 (logs JSON parse debug), S12 (pg_connection putconn warning), S13 (chiuso inventory error), S15 (admin_backups duration warning), S16 (files MIME warning uniforme), S19 (intermediate/writer cleanup debug), S20 (datasources/sharepoint silent)
- **D-serie**: D1 (`ENABLE_ASYNC_ENRICHMENT` parity sync/async + kill-switch mantenuto), D2 (`_read_csv_*` unificato)
- **Deliverable documentali**: `backend/docs/architecture/inventory_report.md`, `backend/docs/architecture/invariants.md`, aggiornamento dei 4 brief Fase 1 (`brief_fe.md`, `brief_acn.md`, `brief_perf.md`, `brief_tests.md`)

### Residui riclassificati a Phase 1 (batch dedicato, scope ristretto)

- **S17** `app/services/file_service.py` — 21 `except: pass` sparsi, audit riga-per-riga
- **S18** `app/core/arricchisci/api_connector.py` — 25 except retry loop, triage pattern ripetitivo
- **S23** `app/core/carica/database_writer.py` — anomalie residue
- **S25** `app/core/eseguibili/router.py`
- **S26** preview swallow cross-modulo estrai/manipola/arricchisci (tocca D6)
- **S27** cluster IMDF (coordinato con D3)
- **S28** shared layer mixed

Questi finiscono in `invariants.md §6` come "triage residui rimandati a batch dedicato Phase 1" e vanno trattati con la disciplina delle 7 regole di review in §5.

### Residui product / env (non invariant, scope governance)

- **D3** IMDF loader/persist/storage boundaries — decisione architetturale, richiede allineamento con cliente
- **D4** alias crypto retro-compat (`URBY_*`, `CONN_ENCRYPT_KEY`) — rimozione coordinata con deploy prod `lab.plenora.it`
- **D5** `SECRET_KEY`/`JWT_SECRET_KEY` come crypto fallback — chiarimento intenzionalita' chain prima di toccare
- **D6** policy preview vs execute cross-modulo — decisione product, non refactor meccanico

### Rumore declassato / inventory overcount (non riapribile senza nuova evidenza)

- `connection_service.py:1-11` docstring stale (non invariant, non vincolante)
- `S6`, `S7`, `S8`, `S14` (triage chat: non attivabili)
- `S21` `config_db_service.py`, `S22` `features/routes.py`, `S24` `admin_system_status/service.py` (overcount inventory)

### Gate eseguiti (2026-04-20)

Il codice e la documentazione Phase 0 sono allineati. Gate check:

1. ✅ Test suite backend `pytest tests2/` verde (1945 passed + 8 skipped, 2026-04-20)
2. ✅ Test suite frontend `npm run test -- --run` verde (134 passed, 2026-04-20)
3. ✅ Smoke E2E manuale (login → project → Estrai → Manipola → preview → Carica): eseguito, nessuna regressione vs pre-Fase 0. Caveat locali ambientali (storage policy implicitamente accoppiato a object storage) annotati come handoff **non-blocker** a Phase 1 — vedi `orchestrate/phase_0_consolidation/integration_notes.md` §Handoff a Phase 1. Fix finali durante gate run: **test/harness only**, nessun codice prodotto toccato.

Phase 0 passa da `done-ready` a **`done`**. Lancio di Fase 1 autorizzato via `done_check.md`.

### File meta aggiornati (2026-04-20)

Aggiornati al post-Phase 0 in anticipo rispetto allo smoke E2E residuo, per coerenza documentale (cfr. `done_check.md` §11–13):

- `VALUTAZIONE_URBY_ETL.md` — sezione `Chiusi Fase 0 Consolidamento (2026-04-20)` con 14 S-serie + 2 D-serie applicati, residui riclassificati, gate BE/FE verde
- `orchestrate/STREAMS.md` — ownership matrix Phase 0 CHIUSA 2026-04-20
- `orchestrate/README.md` — `### Fase 0 — Consolidamento` + riga tempi realistici
