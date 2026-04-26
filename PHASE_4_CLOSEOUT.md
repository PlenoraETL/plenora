# PHASE 4 AUDIT — Closeout

**Data chiusura**: 2026-04-26
**Stato**: CHIUSA
**Scope**: deep audit fixes (residui Phase 3 + finding dei 5 audit
report) + data correctness su `manipola/transforms` + chiusura del
contratto FE/API boundary frontend + cleanup estrattivo di
`DatabaseWriter.write`.
**Mirror operativo (tracking fase, fuori repo)**:
`C:\Users\Marco\Desktop\int_efm\orchestrate\phase_4_audit\TRIAGE.md`
**Deadline produzione**: 2026-05-05 (rispettata, chiusura anticipata).

Pattern simmetrico a `PHASE_1_CLOSEOUT.md` / `PHASE_2_CLOSEOUT.md` /
`PHASE_3_CLOSEOUT.md`. Il dettaglio batch-by-batch (review note,
follow-up declassati, motivazioni tecniche puntuali) vive nel
TRIAGE fuori repo. In caso di divergenza, questo file e' fonte
di verita' per lo stato formale della fase.

---

## 1. Sintesi

Phase 4 chiude con:

- **4 commit Batch A** — chiusura residui Phase 3 + quick wins.
- **3 commit Batch B** — runtime safety + data correctness + FE upload UX.
- **6 commit Batch C** (C1, C2, C3.1-C3.4) — manipola data correctness
  + chiusura del contratto "src/api/ unico punto HTTP" frontend.
- **3 commit Batch C4** (C4.1-C4.3) + **1 no-op ratificato** (C4.4) —
  refactor estrattivo di `DatabaseWriter.write` (290 → ~25 righe).
- **0 P0 aperti**, **0 regressioni**, **0 nuovi skip**.
- **Boundary FE/API completamente chiuso**: zero `import http` o
  `http.<verb>(` o `fetch(` fuori da `frontend/src/api/`.

Nessun fake closure: ogni commit ha review batch-by-batch documentata
in TRIAGE; due rollback parziali (C3.3 → C3.4 per NL2Pipeline morta;
B3 secondo giro per cap ticket 10MB) sono annotati esplicitamente.

---

## 2. Commit Phase 4 (cronologia per batch)

### Batch A — quick wins

| Commit | Tema |
|---|---|
| `ed18fd9` | A1 — formula AST validator pre-pandas.eval; `op_aggregate(dropna=False)`; 2 test N4 fail-loud |
| `d103e7f` | A2 — redact secrets nei report errore enrichment |
| `c6e5859` | A3 — intermediate node writes atomic/idempotent (DELETE+INSERT app-side) |
| `6c33900` | A4 — frontend API contracts apiConnections/eseguibili |

### Batch B — runtime safety + data correctness + FE upload UX

| Commit | Tema |
|---|---|
| `08150b6` | B1 — upload size cap esteso a `eseguibili/analyze-upload` via shared helper backend |
| `6da6e59` | B2 — `op_geo_reproject` fail-loud su source_crs mancante e nessun CRS rilevabile |
| `a9e4130` | B3 — frontend upload guards (100MB files/eseguibili, 10MB tickets, 413 propagation) |

### Batch C — manipola data correctness + FE/API boundary

| Commit | Tema |
|---|---|
| `751b881` | C1 — `op_join` previene match falsi NaN==NaN (sentinel substitution + warning telemetria) |
| `e8e96b0` | C2 — `op_fill_na` rifiuta fail-loud type demotion silente su colonne numeriche |
| `892f7a9` | C3.1 — `ApiEnrichNode` upload via `api/files.uploadFile` |
| `bc28b3a` | C3.2 — connection calls (CRUD + test + openapi + datasource schema) dietro boundary api/ |
| `40cb7c0` | C3.3 — auth (login/logout) dietro boundary api/, `authFetch` rimosso |
| `c45a591` | C3.4 — rimossa surface stale NL2Pipeline + ApiEnrichNode `getApiConnection` + ExecutionModal `downloadProjectFile` |

### Batch C4 — DatabaseWriter.write helper extraction

| Commit | Tema |
|---|---|
| `01e342d` | C4.1 — preflight helpers (`_build_initial_result`, `_assert_safe_write_config`) |
| `bac8569` | C4.2 — schema hints (`_compute_schema_hints`) — dedup REPLACE+CREATE |
| `df98b6f` | C4.3 — mode dispatch (`_dispatch_write_mode` con flag `early_return`) |
| **no-op** | C4.4 — ratificato no-op: residual `write()` ~25 righe e' al livello giusto di astrazione, ulteriori estrazioni 0-3 righe avrebbero ROI negativo |

**Totale**: 13 commit atomici + 1 no-op documentato.

---

## 3. Test evidence

| Suite | Esito |
|---|---|
| `tests2/manipola` (post C1+C2) | **118 passed** |
| `tests2/manipola/unit/test_op_join_nan_key_contract.py` | 13 passed |
| `tests2/manipola/unit/test_op_fill_na_type_contract.py` | 13 passed |
| `tests2/manipola/unit/test_spatial_reproject_source_crs_contract.py` (B2) | 5 passed |
| `tests2/security/unit/test_n4_formula_validator_contract.py` (A1) | 69 passed |
| `tests2/security/unit/test_eseguibili_size_cap_contract.py` (B1) | 4 passed |
| `tests2/security/unit/test_files_router_size_cap_contract.py` (regress B1) | 4 passed |
| `tests2/eseguibili` (regress B1) | 20 passed |
| `tests2/intermediate` (post A3) | 47 passed |
| `tests2/arricchisci` (post A2) | 178 passed |
| FE `tests/api` (post C3.4 boundary cleanup) | **135 passed / 21 files** |
| FE `tests/api/uploadGuards.contract.test.js` (B3) | 11 passed |
| FE `tests/api/connections.boundary.contract.test.js` (C3.2) | 12 passed |
| FE `tests/api/auth.boundary.contract.test.js` (C3.3) | 7 passed |
| FE `tests/api/nl2.cleanup.contract.test.js` (C3.4) | 8 passed |
| FE `tests/api/executionModal.boundary.contract.test.js` (C3.4) | 7 passed |
| FE `tests/api/apiEnrichNode.boundary.contract.test.js` (C3.1+C3.4) | 8 passed |
| `tests2/carica` (post C4.1 → C4.3) | **163 passed** (invariato a ogni step) |

Tutto verde nelle suite eseguite, zero nuovi skip rilevati. Default
CI path non ha mai chiamato `psycopg2.connect`.

---

## 4. C4.4 no-op rationale

`write()` post-C4.3 e' ~25 righe e legge top-to-bottom come public
orchestrator: setup → empty short-circuit → validazione config →
try/dispatch → except con error recording → finalize. I tre candidati
estrattivi (`_mark_empty_write_success`, `_mark_write_error`,
`_finalize_write_result`) sono tutti single-call-site con body 0-3
righe; ogni helper costerebbe piu' linee del risparmio (def + docstring
~10 righe vs 3 righe inline) e nascondrebbe il control flow al lettore
del metodo pubblico. La scelta no-op e' ratificata e documentata in
TRIAGE; C4 e' completo con i tre micro-step C4.1+C4.2+C4.3 che hanno
gia' ridotto `write()` da ~290 a ~25 righe (-91%).

---

## 5. Carry-over verso Phase 5

- **Phase 5 structural slimming**: file >2000 righe rimasti
  (`database_writer.py` 2443, `arcgis_uploader.py` ~1100+, ecc.) da
  valutare per estrazione in modulo per dialect/strategy.
- **Post-Phase-5 runtime behavior validation on real flows**: smoke
  end-to-end su canvas produzione per validare che le modifiche di
  data correctness (C1 op_join, C2 op_fill_na, B2 op_geo_reproject)
  non rompano canvas legacy con dati edge. Triage con utenti reali.
- **Consolidated technical debt ledger Phase 0-4**: aggregare
  follow-up declassati di tutte le fasi (P2 di Phase 1, ADR
  numbering, full observability stack, status page, maintenance
  window policy + A3 UNIQUE constraint + ApiEnrichNode legacy
  defenses) in un unico ledger consultabile pre-kickoff Phase 5.

---

## 6. Note operative

- `TRIAGE.md` completo (review note batch-by-batch, motivazioni di
  rollback, dettaglio dei follow-up) vive in
  `C:\Users\Marco\Desktop\int_efm\orchestrate\phase_4_audit\TRIAGE.md`
  **fuori repo**. Non e' duplicato qui per mantenere il closeout
  sintetico, allineato al pattern Phase 1/2/3.
- Path policy: codice/test in `URBY-ETL/`, audit/triage in
  `C:\Users\Marco\Desktop\int_efm\orchestrate\phase_4_audit\`.
  Nessuna cartella `orchestrate/` dentro il repo (rimossa la stale
  copia all'inizio di Phase 4).
- Working tree del repo `URBY-ETL` atteso pulito dopo il commit di
  questo closeout.
- Phase 4 non ha aperto nuovi P0. Lo stato del repo a fine Phase 4
  e' piu' robusto del baseline pre-Phase 4 su 4 dimensioni:
  data correctness manipola (C1+C2 + A1), runtime safety (B1+B2),
  FE/API boundary (C3.1-C3.4), code structure carica (C4.1-C4.3).
- Nessuna modifica a runtime/test/CI/conftest in questa chiusura
  formale: solo bookkeeping documentale.
