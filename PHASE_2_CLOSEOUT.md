# PHASE 2 HARDENING — Closeout

**Data chiusura**: 2026-04-23
**Stato**: CHIUSA
**Scope**: `orchestrate/phase_2_hardening/kickoff.md`
**Mirror interno (tracking fase)**: `orchestrate/phase_2_hardening/kickoff.md §9.1-§9.6`
**Deadline**: 2026-05-05 (rispettata, chiusura anticipata 12 giorni)

Questo file e' la versione **git-tracked** del closeout di Phase 2. Pattern simmetrico a `PHASE_1_CLOSEOUT.md`. Il dettaglio operativo delle disposizioni di chiusura vive in `kickoff.md §9.1-§9.6` (cartella `orchestrate/` non sotto version control); in caso di divergenza, questo file e' fonte di verita' per lo stato formale della fase, mentre `§9.x` e' fonte di verita' per le motivazioni tecniche puntuali.

---

## 1. Sintesi

Phase 2 si chiude con:

- **3 track architetturali chiusi**: A-D3 (IMDF boundaries), A-A1b (provider MinIO), A-LOG (log lifecycle). Tutti con ADR ratificato + commit mergiati su `main`.
- **3 track operation chiusi**: O-D4 (alias crypto rimossi), O-BULK (inventario chiamanti file storage), O-ARC (archive log operativo).
- **1 track product/UX chiuso**: P-CLR (endpoint `POST /logs/clear` rimosso via Option D). P-VIEW e P-RET rinviati esplicitamente a non-Phase-2.
- **Cluster bulk speciale chiuso definitivamente**: MySQL LOAD DATA e Postgres COPY **rimossi** 2026-04-23 (decisioni dirette registrate in `§9.5` e `§9.6` con deviazione esplicita dal no-go `performance_bulk_review.md §5.1`, accettando regressione ~19x su PG).
- **Performance shortlist punto 3 misurata** ma nessun refactor runtime promosso pre-deadline (carry-over documentato in `§9.4`).
- **D3 + D4 carry-over Phase 1 chiusi** in Phase 2 (registrato in `invariants.md §6`).

Nessun fake closure. Quello che non e' stato completato e' classificato esplicitamente come carry-over (§7) o rinvio dichiarato (§6).

---

## 2. Done criterion — verifica §6 kickoff

Checklist puntuale del `§6 kickoff`:

| # | Done criterion | Stato | Evidenza |
|---|---|---|---|
| 1 | A-D3 refactor IMDF mergiato + ADR | ✓ chiuso | ADR-0016 + 5 commit impl |
| 2 | A-A1b provider MinIO wirato + contract test + ADR | ✓ chiuso | ADR-0015 + 3 batch A1-B1/B2/B3 |
| 3 | A-LOG ADR log lifecycle ratificato | ✓ chiuso | ADR-0014 + addendum + O-ARC + P-CLR |
| 4 | O-D4 alias rimossi + `.env` prod allineato + ADR | ✓ chiuso | 3 commit O-D4-B/C (scenario A rollout SSH) |
| 5 | O-BULK inventario chiamanti classificato | ✓ chiuso | `a1_bulk_review.md` + §1.7 invariants |
| 6 | O-ARC archive log operativo | ✓ chiuso | cluster Batch 1-4 + runbook |
| 7 | P-CLR endpoint aderente policy | ✓ chiuso | commit `698ff9d` Option D + contract test anti-reintroduzione |
| 8 | P-VIEW log viewer live vs archived | RINVIATO non-Phase-2 | `invariants.md §6` riga 258 (pre-batch, disposizione esplicita) |
| 9 | P-RET dashboard retention | RINVIATO non-Phase-2 | commit `2741b6b` (disposizione esplicita con G1 fix simmetrico) |
| 10 | `invariants.md` aggiornato (D3/A1/log/D4) | ✓ verde | aggiornamenti ratificati nei commit rispettivi |
| 11 | `inventory_report.md §5.5` roll-up | ✓ verde | G1 D3 stale risolto in `2741b6b` |

**7 item tecnicamente chiusi** + **2 rinviati esplicitamente** + **2 verifiche invariants/inventory verdi** = **11/11 done criterion in stato finale**. Zero item silently skipped.

---

## 3. Item tecnicamente chiusi

| ID | Output | Evidenza canonica |
|---|---|---|
| **A-D3** | IMDF boundaries refactor: `site_id_resolver` unico, hydration unificata, ownership `tmp_dir` dichiarata, inversione dep `persist → storage` rimossa | ADR-0016 (`0016-2026-04-23-imdf-boundaries.md`) + 5 commit impl atomici: `e86340e`, `4d95a85`, `432b003`, `8593370`, `0d1b851`. Ratifica `invariants.md §6 riga 246` |
| **A-A1b** | Provider MinIO P1 wirato nel dispatch FS-4/FS-5 + contract test matrice `policy x provider` | ADR-0015 (`0015-2026-04-22-minio-object-storage-provider.md`). 3 batch: A1-B1 review `a1_bulk_review.md` 2026-04-22; A1-B2 impl commit `a5956f7`; A1-B3 contract parity matrix commit `141d894` (scope ridotto). Ratifica `invariants.md §6 riga 259-262` |
| **A-LOG** | ADR log lifecycle (append-only, retention, purge, archive) | ADR-0014 (`0014-2026-04-21-log-archive-pipeline.md`) + addendum §A (env contract) + addendum §B (observability circolare). Ratifica post O-ARC + P-CLR in `invariants.md §6 riga 258` |
| **O-D4** | Alias crypto `URBY_ENCRYPTION_KEY`, `CONN_ENCRYPT_KEY`, `URBY_DISABLE_DEFAULT_KEY` rimossi dal codice + `.env` prod allineato a `PLENORA_ENCRYPTION_KEY` | Phase 1 parziale: `3924bfb` (alias dev flag). Phase 2 completamento: O-D4-B rollout SSH scenario A 2026-04-23 + O-D4-C runtime/test commit `1005018` + doc commit `2274fd5`. Ratifica `invariants.md §3 tabella + §6 riga 247` |
| **O-BULK** | Inventario chiamanti file storage + classificazione `keep / keep optional / remove` | Review `orchestrate/phase_2_hardening/a1_bulk_review.md` 2026-04-22. 0 bypass. Ratifica `invariants.md §1.7 + §1.7.bis + §6 riga 260` |
| **O-ARC** | Target archive log operativo (bucket, retention, cadenza) + job eseguito | Cluster Batch 1-4 (2026-04-21): pipeline skeleton `39b2267`, env contract `1a57d4b`/`861604d`/`b739c11`/`ef6ae2c`, factory+hooks `ebb5ec2`/`7819406`/`afcb185`/`165ed7f`, closure `50058a1`/`ce58b65`/`73e85a1`/`0cf18ad`. Runbook `o_arc_runbook.md` (on-disk) per flip prod `LOG_ARCHIVE_ENABLED=true`. Ratifica `invariants.md §1.4 + §6 riga 258` |
| **P-CLR** | `POST /logs/clear` rimosso (Option D pura): endpoint BE + FE button + api client + capability + cascade cleanup cross-modulo scoped a `log_runs` | 5 commit atomici 2026-04-22: `698ff9d` (C1 endpoint+FE), `5361fce` (C2 orchestrator), `ae29f99` (C3 config_db_service), `7fd8d7c` (C4 regression test anti-reintroduzione), `3e7c370` (invariants). Ratifica `invariants.md §1.4 + §6 riga 258` |

---

## 4. Cluster bulk speciale — rimozione definitiva 2026-04-23

Secondo sotto-blocco del punto 3 del kickoff originale (`performance_bulk_review.md`). Chiuso via **rimozione diretta di entrambi i path bulk native**, con deviazione esplicita da review e kickoff criteria registrata in `§9.5` e `§9.6`.

### 4.1 Catena di preparazione pre-rimozione (3.0 → 3.12)

Telemetria + aggregator + criteri + script verdetto, 11 batch atomici pre-rimozione:

| Batch | Tipo | Commit / Artefatto |
|---|---|---|
| 3.0 | doc-only scope scaduto cluster F (Parquet intermedio) | `kickoff.md §9.1` |
| 3.1 | telemetria `event=bulk.fallback` 10 siti | `a357ec7` |
| 3.2 | telemetria `event=bulk.success` MySQL | `9803ca6` |
| 3.3 | doc-only criterio `success_rate_mysql` in `§9.2` | on-disk |
| 3.4 | correlation IDs + `event=stage_swap.dispatched` | `0bde15d` |
| 3.5 | telemetria `event=bulk.success` PG (simmetria) | `cdf9a8d` |
| 3.6 | doc-only criterio `success_rate_pg` in `§9.3` | on-disk |
| 3.7 | bench shortlist A (spatial) + B (WKT) | `ca967b4` |
| 3.8 | bench shortlist C (text_normalize) | `cd087bd` |
| 3.9 | doc-only `§9.4` shortlist registrata | on-disk |
| 3.10 + retrofix | aggregator unblocker (whitelist in `logs.details`) | `f36a2b1` + `cdb87f1` |
| 3.11 | doc-only execute plan rimozione MySQL | `mysql_load_data_removal_execute_plan.md` |
| 3.12 | script `perf_bulk_verdict.py` + test soglie | `52d96f5` |

### 4.2 Rimozioni eseguite

| Ramo | Disposizione review | Stato finale | Commit runtime+test | Commit doc |
|---|---|---|---|---|
| MySQL LOAD DATA | `§4 A2` keep optional, `§5.2` ROI basso | rimosso via decisione diretta (`§9.5`) senza verdetto telemetrico post-deploy | `54d6e44` | `f829019` |
| Postgres COPY | `§4 A1` keep + `§5.1` **no-go esplicito** | rimosso via **deviazione esplicita** dal no-go accettando regressione ~19x (`§9.6`) | `7256940` | `ee4f600` |

**Stato complessivo cluster bulk**:
- Path bulk native: **nessuno residuo**
- Dispatcher `bulk_insert`: scaffolding che raise sempre `BulkInsertNotSupportedError(reason="dialect_not_supported")` per tutti i dialect
- Fallback universale: `executemany` via `conn.execute(sql, params_list)`
- Boost residuo: solo MSSQL `fast_executemany=True` sull'engine factory (driver-level, non coinvolto)
- Kill-switch `CARICA_BULK_INSERT`: preservato come scaffolding (no-op post-rimozione)
- Telemetria stage+swap (`event=stage_swap.dispatched`): attiva (Batch 3.4)
- Contract test tombstone attivi: `test_dispatcher_mysql_tombstoned_emits_dialect_not_supported` + `test_dispatcher_postgres_tombstoned_emits_dialect_not_supported`
- File test eliminati: 4 (`test_bulk_copy_types_roundtrip.py`, `test_bench_bulk_vs_executemany_clientside.py`, `test_bench_bulk_vs_executemany_pg_endtoend.py`, `test_baseline_bulk_insert.py`)

**Rischio residuo dichiarato** (commit 2/2 rimozione PG, `§9.6`): ogni pipeline CARICA PG con INSERT > 10k righe subisce regressione attesa ~19x. PG e' il DB primario URBY, impatto su quasi tutto il traffico CARICA produttivo. Accettato esplicitamente dalla disposizione utente.

---

## 5. Performance shortlist punto 3 — numeri misurati

Primo sotto-blocco del punto 3 (non-bulk). Shortlist esplorata con bench mirato `test_bench_phase_2_perf_shortlist.py` (marker `perf_bench` + skip-gate `URBY_RUN_PERF_SHORTLIST=1`):

| # | Candidato | Sito runtime | Speedup | Disposizione |
|---|---|---|---|---|
| A | `op_geo_clean_topology` Step 1 | `spatial.py:1555-1567` | 2.75x | non promosso pre-deadline (< 3x) |
| B | WKT parse per-riga | `features_service.py:1214` | 2.10x | non promosso pre-deadline (< 3x) |
| C | `op_text_normalize` `ops='lower'` | `base.py:1694-1722` | 11.99x | non promosso per mancanza evidenza distribuzione `ops` |
| C | `op_text_normalize` `ops='full'` | idem | 1.39x | scartato (collo `strip_accents` NFKD Python) |
| D | `execute_raw` accumulate-in-list | `query_executor.py:629-634` | non misurato | residuo Phase 3 |

**Verdetto del sotto-blocco**: nessun refactor runtime promosso pre-deadline. Dettaglio + pre-condizioni per rivalutazione Phase 3 in `kickoff.md §9.4`. Ratifica: `ca967b4` (3.7) + `cd087bd` (3.8).

---

## 6. Item rinviati esplicitamente non-Phase-2

Nessun silently skipped. Ogni rinvio e' documentato con motivazione:

| ID | Tipo | Motivazione rinvio | Registrato in |
|---|---|---|---|
| **P-VIEW** | rehydrate archive → ricostruzione riga DB | scope distinto da O-ARC + P-CLR, richiede UX separato | `invariants.md §6 riga 258` (dichiarazione emersa dal flow O-ARC/P-CLR) |
| **P-RET** | dashboard retention admin superadmin | priorita' post-deploy su bulk, dati e pattern admin pronti ma UI non bloccante per closeout | commit `2741b6b` + `invariants.md §6 riga 258` estesa |

Entrambi restano scope Phase 3. Dati sottostanti gia' disponibili (ADR-0014 addendum §B observability + `LOG_ARCHIVE_*` env); nessun blocker tecnico residuo.

---

## 7. Carry-over dichiarati Phase 3

Tutti registrati in `kickoff.md §9.x` o superati dalle decisioni operative dirette di `§9.5`/`§9.6`:

- **§9.1 Parquet intermedio (cluster F review)**: scope scaduto, candidato Phase 3 con pre-condizione profilo memoria 5M righe
- **§9.2 criterio `success_rate_mysql`**: **superato** dalla rimozione diretta MySQL in §9.5 (criterio resta come pattern metodologico storico)
- **§9.3 criterio `success_rate_pg`**: **superato** dalla rimozione diretta PG in §9.6 (idem)
- **§9.4 shortlist performance non-bulk**: candidati A/B/C con pre-condizioni per rivalutazione Phase 3 (bench aggressivo shapely ufunc, dataset 500k-1M, telemetria `ops` distribution, mock sqlite `execute_raw`)
- **§6.2.5 review end-to-end PG bench**: assorbito dalla rimozione PG COPY (test `test_bench_bulk_vs_executemany_pg_endtoend.py` eliminato con §9.6)
- **§6.2.6 review revisione `_mssql_replicate_schema_to_shadow`**: non toccato Phase 2, invariato
- **Script `perf_bulk_verdict.py`** + test soglie: preservati come scaffolding storico (post-rimozione producono `PERIODO INVALIDO` su entrambi i rami)
- **`mysql_load_data_removal_execute_plan.md` 3.11**: sigillato come riferimento storico (deviazione dal gating registrata in §9.5)

---

## 8. Commit history rappresentativi

Head commits di Phase 2 per track. Lista non esaustiva.

### Architecture

- **A-D3**: `8593370`, `0d1b851`, `4f458aa`, `e86340e`, `4d95a85`, `432b003` (5 commit impl)
- **A-A1b**: `a5956f7` (MinIO impl), `141d894` (contract matrix), `c425f60` (invariants §6 chiusura)

### Operation

- **O-D4**: `3924bfb` (Phase 1 dev flag), `1005018` (runtime+test O-D4-C), `2274fd5` (doc O-D4-C)
- **O-ARC**: cluster Batch 1-4 (~15 commit); head symbolic `0cf18ad` (invariants post O-ARC)
- **A1-B1/O-BULK**: `a1_bulk_review.md` doc on-disk + `§1.7 invariants` update

### Product/UX

- **P-CLR**: `698ff9d`, `5361fce`, `ae29f99`, `7fd8d7c`, `3e7c370` (5 commit)

### Cluster bulk speciale

- **Preparazione** (3.0-3.12): `a357ec7`, `9803ca6`, `0bde15d`, `cdf9a8d`, `ca967b4`, `cd087bd`, `f36a2b1`, `cdb87f1`, `52d96f5`
- **Rimozione MySQL** (§9.5): `54d6e44` + `f829019`
- **Rimozione PG** (§9.6): `7256940` + `ee4f600`

### Doc closeout intermedi

- `2741b6b` (P-RET rinvio + G1 D3 stale fix)
- Questo commit (chiusura formale)

---

## 9. No fake closure

Niente chiuso per "quasi". Ogni item del `§6 done criterion` e' registrato con:
- **chiuso tecnicamente** → ADR + commit + ratifica `invariants.md §6`
- **rinviato esplicitamente non-Phase-2** → motivazione + registro doc
- **superato da decisione diretta** (cluster bulk) → `§9.5` / `§9.6` con deviazione esplicita da review/criteri

Zero item lasciati in limbo ambiguo. La ricognizione 2026-04-23 ha identificato 1 sola ambiguita' residua (P-RET silently skipped) e la disposizione esplicita l'ha chiusa prima del closeout.

---

## 10. Stato finale

- **Phase 2 formalmente chiusa** il 2026-04-23, 12 giorni prima della deadline 2026-05-05.
- **Punto 3 bulk** (MySQL + PG) chiuso definitivamente via rimozione; non in attesa post-deploy.
- **Punto 3 shortlist performance non-bulk** (spatial/WKT/text_normalize) chiuso con numeri misurati e pre-condizioni Phase 3 documentate.
- **Catena critica A-LOG → O-ARC → P-CLR** completata.
- **Track operation O-D4 SSH** eseguita end-to-end (runbook ratificato, rollout `.env` prod, rimozione codice).
- **9/9 item del `§6 done criterion` kickoff** in stato finale (7 tecnicamente chiusi + 2 rinviati esplicitamente).
- **D3 + D4 carry-over Phase 1** chiusi in Phase 2.
- **Rischio residuo user-visible post-deploy**: regressione ~19x su pipeline CARICA PG con INSERT > 10k righe (accettato esplicitamente).

Eventuale Phase 3 potra' raccogliere i carry-over di `§9.x` + `§6.2.*` review; nessun blocker tecnico trasferito.
