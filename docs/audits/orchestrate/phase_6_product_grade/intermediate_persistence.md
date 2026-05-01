# Intermediate Persistence Policy — Design

**Phase:** 6 — Product Grade
**Status:** design (no code)
**Date:** 2026-04-28
**Owner:** Claude (design), Marco (decision)

## 0. Problema

Oggi la pipeline persiste **ogni riga di output di ogni nodo intermedio** in
PostgreSQL `plenora.node_rows` (tabella JSONB unificata). Per dataset reali
da centinaia di migliaia o milioni di righe questo significa:

- N nodi × M righe × ~K bytes JSONB → bloat PG, IO saturo, autovacuum sotto
  pressione.
- Tempo di scrittura dominante sul tempo di esecuzione del nodo.
- `delete_all_runs_for_canvas` e cleanup retention diventano lenti.
- L'unico cap esistente (`INTERMEDIATE_SNAPSHOT_MAX_ROWS = 100_000`) e' un
  placeholder ignorato dal writer.

Vincolo che NON vogliamo perdere:
- **Sapere se un nodo e' cambiato tra run** (input_hash / output_hash
  stabili). Questo guida cache invalidation, replay/dedup, diff UI.

L'obiettivo e' separare **persistenza righe** (UI/debug) da **change
detection** (hash), con una policy globale gating la prima.

---

## 1. Stato attuale (mapping)

### Schema

`app/db/intermediate_schema.sql`:
- `runs` (root): run_id, canvas_id, group_id, status, input_hash,
  output_hash, totali, user_id. Indici su canvas/group/timestamp/pinned.
- `node_meta` (run_id, node_id) — schema, row_count, exec_time, status,
  error_msg. **ON DELETE CASCADE** da runs.
- `node_rows` — `(id BIGSERIAL PK, run_id FK, node_id, row_idx, row_hash,
  row_data JSONB)`. Indici `(run_id, node_id)` e `(run_id, node_id,
  row_hash)`. **ON DELETE CASCADE** da runs.

Final output e' separato: `results.db` → tabella `results` (BLOB <50MB,
S3 oltre). **Indipendente da `node_rows`.**

### Write path

- `app/core/intermediate/writer.py:166` — `write_node(run_id, node_id,
  node_type, rows, col_schema, exec_time, status, error_msg)`:
  1. Per ogni row calcola `_row_hash` via `compute_row_hash` (line 150).
  2. Chiama `db.write_node_atomic` (line 195).
  3. Calcola `compute_aggregate_hash(rows, col_schema)` (line 207) e lo
     ritorna.
  4. Branch error/empty: solo `upsert_node_meta`, hash = `sha256(b"empty")`.
- `app/core/intermediate/db.py:412` — `write_node_atomic`:
  DELETE + executemany INSERT + UPSERT node_meta in **singola transazione**.
- `app/core/intermediate/db.py:374` — `insert_node_rows`: variante senza
  upsert meta, stessa atomicita'.

Caller principali: `manipola/executor.py`, `estrai/router.py`,
`arricchisci/api_connector.py`, `carica/executor.py`.

### Hash

- `compute_row_hash(row, col_names_sorted) -> md5` (writer.py:150).
  Stored in `node_rows.row_hash`. Usato da L2 diff via `NOT EXISTS`
  sull'indice `(run_id, node_id, row_hash)`.
- `compute_aggregate_hash(rows, col_schema) -> sha256` (writer.py:23).
  Per-colonna stats (count/sum/min/max numeric, distinct hash string,
  geom counters), concatenati ordinati, SHA256 finale. Stored in
  `runs.input_hash` / `runs.output_hash`.

**Quando si calcolano:** entrambi durante `write_node`, sul **full rows
list in memoria**, prima della persistenza.

### Read path

- **UI preview** — `router.get_node_data` GET
  `/api/runs/{run_id}/nodes/{node_id}/data`: legge da `node_rows`,
  default limit 100, max 5000. Ricostruisce via `_from_jsonable_value`.
- **L2 diff completo** — `router.compare_runs_l2`: NOT EXISTS su row_hash
  fra due run, limit 500 per direzione.
- **L1 compare** — `router.compare_runs_l1`: usa solo `runs.input_hash` /
  `runs.output_hash` + `node_meta.row_count`. **Niente lettura node_rows.**
- **Downstream nodes (CRITICO):** la pipeline NON ri-legge `node_rows`
  per alimentare il nodo successivo. L'executor passa DataFrame
  in-memory; project_source legge il `results` blob upstream via
  `ArtifactResolver.materialize_latest`. **Disabilitare `node_rows` non
  rompe l'esecuzione end-to-end.**

### Final output

`results` table in `results.db`. Hybrid: BLOB <50MB inline, S3 oltre.
Persistenza completamente separata dalla policy intermediate.

### Settings esistenti

`app/config.py`:
- `ESTRAI_MAX_ROWS = 5_000_000` — cap su SELECT, non su persistenza.
- `INTERMEDIATE_SNAPSHOT_MAX_ROWS = 100_000` — placeholder, **non
  applicato dal writer**.

### Performance signals

Nessun `[PERF]` tag in writer/db. Solo `logger.info("Nodo scritto: ...")`
a fine write. Per il design ci serve telemetria (Step 1).

---

## 2. Contratti da preservare

| Contratto | Oggi | Vincolo |
|---|---|---|
| Preview nodo (UI) | `GET /runs/{rid}/nodes/{nid}/data`, limit≤5000 | **Mantenere fino a N righe**, dove N = preview cap |
| Run changed/not changed | L1 compare via `runs.*_hash` + `node_meta.row_count` | **Sempre disponibile**, anche in mode `off` |
| Diff completo (L2) | NOT EXISTS su `node_rows.row_hash` | **Best-effort**: full solo in mode `full`, sample in `preview_only`, indisponibile in `off` |
| Replay / debug | Lettura `node_rows` di un run vecchio | **Best-effort**: dipende da policy attiva al run-time del progetto |
| Final output | `results.db` separato | **Invariato** |

---

## 3. Policy proposta

Settings (default conservativo, switch via env):

```
INTERMEDIATE_PERSISTENCE_MODE = preview_only   # off | preview_only | full
INTERMEDIATE_PREVIEW_ROWS     = 500            # solo se mode=preview_only
```

Risoluzione: env > config default. Esponiamo lettura via
`app.config.settings.INTERMEDIATE_PERSISTENCE_MODE` e
`INTERMEDIATE_PREVIEW_ROWS`.

Override per-progetto in fase 2 (campo opzionale in `projects` o per-run
flag): **fuori scope primo step**.

---

## 4. Semantica dei tre mode

### `full`
- Comportamento attuale, byte-per-byte.
- Tutti i `rows` finiscono in `node_rows`.
- L2 diff completo disponibile.
- Hash row+aggregate calcolati come oggi.

### `preview_only` (default proposto)
- Persiste in `node_rows` solo le **prime N righe** (`INTERMEDIATE_PREVIEW_ROWS`).
- `node_meta.row_count` = **lunghezza reale** del dataset, non N.
- `runs.input_hash` / `runs.output_hash` = `compute_aggregate_hash` sul
  **full set** (calcolato in memoria prima del troncamento).
- `node_rows.row_hash` = `compute_row_hash` solo sulle righe persistite
  (le N preview).
- Marker su `node_meta`: nuovo flag `preview_truncated BOOL` (o
  `persisted_rows_count INT`) per la UI.
- L2 diff: limitato alle prime N righe; UI mostra warning "diff parziale
  (preview_only)".

### `off`
- Nessun INSERT in `node_rows`. Solo `upsert_node_meta`.
- `node_meta.row_count` = lunghezza reale.
- `runs.input_hash` / `runs.output_hash` = aggregate sul full set.
- L2 diff disabilitato (warning UI).
- Preview UI: 200 (vuoto) con header `X-Persistence-Mode: off`.
- Replay/debug: solo via `runs.*_hash` + node_meta.

---

## 5. Hashing — separare da persistenza

### Stato attuale

Hash row e aggregate sono calcolati **dentro `write_node`**, sul `rows`
list che e' anche destinato alla persistenza. Funziona perche' la lista
e' in memoria al momento della call.

### Cambio

Separare logicamente:

1. `compute_aggregate_hash(rows, col_schema)` — invariato, sempre chiamato
   sul full set. **Non dipende da policy.**
2. `compute_row_hash` — chiamato solo per le righe da persistere.
   - `full`: tutte
   - `preview_only`: prime N
   - `off`: nessuna
3. La firma di `write_node` puo' restare uguale; il branching avviene al
   suo interno.

### Costo aggregate hash

`compute_aggregate_hash` **scorre tutto** `rows`, conta non-null, somma
numerici, hasha distinct strings. O(n) memoria + O(n) tempo. Per 10M
righe puo' essere significativo, ma resta drasticamente piu' economico
del JSONB write attuale.

### Streaming hash (futuro)

Se aggregate diventa bottleneck, valutare:
- Reduce per-chunk (count, sum, min, max, hash incrementale dei distinct)
  invece di in-memory list comprehension.
- Aggregato compatibile bit-per-bit col compute attuale (richiede
  sort dei distinct → non trivialmente streaming per le colonne stringa).

**Non in scope primo step.** Step 1 misura, decide.

---

## 6. Storage layout

### Postgres (sempre)

- `runs` — invariato.
- `node_meta` — aggiunta opzionale `persisted_rows_count INT` (NULL =
  comportamento legacy = full). Mantiene `row_count` reale.
- `node_rows` — invariato come schema; volume regolato da policy.

### ArtifactStore (opzionale, `full` mode in fase 2)

- Cartella per `(canvas_id, run_id, node_id)`.
- File Parquet con schema e righe complete. Compresso (snappy).
- Pointer in `node_meta.artifact_uri` (nuova colonna nullable).
- Backend: usa `app.shared.cloud_backends.get_object_storage()` (gia'
  astratto S3/local). **Niente S3 hardcoded nel core.**

Beneficio: utenti che vogliono diff completo lo abilitano per progetto
specifico, scaricando il Parquet quando serve, senza inquinare PG.

---

## 7. Delta (futuro — non primo step)

Idea: invece di full snapshot per run, salvare snapshot baseline +
delta per run successive (rows added / removed / modified per row_hash).

Componenti:
- Base snapshot: ogni N run (compaction).
- Delta: diff vs snapshot piu' recente.
- Reader L2: ricostruisce on-demand applicando delta su base.

Costi: complessita' significativa, retention non triviale, diff calc
costoso al write. **Decisione: prima vediamo se preview_only + artifact
parquet on-demand bastano. Delta solo se la pressione PG resta alta.**

---

## 8. Patch plan

Step incrementali, ognuno commit indipendente verificabile.

### Step 1 — Telemetria (no behavior change)

- Aggiungere `[PERF]` log in `write_node` e `write_node_atomic`:
  `op=write_node node_id=... rows=... bytes_jsonb=... duration_ms=...`.
- Log su `compute_aggregate_hash` e `compute_row_hash` totali.
- Misurare 5-10 run reali su dataset rappresentativi (Geosafe + Plenora
  large) **prima** di toccare la policy.

**Outcome:** ground truth quantitativo. Se `node_rows` write e' davvero
il bottleneck come ipotizzato, conferma il piano. Se invece e' compute,
si re-prioritizza.

### Step 2 — Settings + policy switch

- Aggiungere `INTERMEDIATE_PERSISTENCE_MODE` e `INTERMEDIATE_PREVIEW_ROWS`
  in `app/config.py` con default `preview_only` / `500`.
- Branch in `write_node`:
  - `off` → solo `upsert_node_meta`.
  - `preview_only` → tronca a N, `write_node_atomic` con N rows.
  - `full` → comportamento attuale.
- Aggregate hash sempre sul full `rows` (prima del troncamento).
- Test: 3 unit test (uno per mode) + 1 integration verificando che
  `runs.output_hash` e' identico fra `full` e `preview_only`.

**Outcome:** behavior change controllato; gating nuovo, comportamento
default invariato (deploy con `MODE=full` e flip dopo verifica).

### Step 3 — Schema marker (opzionale)

- Aggiungere colonna `persisted_rows_count INT` su `node_meta` (NULL =
  legacy/full).
- Migration `2026-XX-XX_node_meta_persisted_rows_count.sql`.
- Writer popola il campo. UI lo legge per badge "preview parziale".

**Outcome:** chiarezza UI. Non necessario per la policy ma utile per
disambiguare run vecchi vs nuovi.

### Step 4 — Artifact Parquet opzionale (`full` upgrade)

- Quando `MODE=full`, scrivere Parquet su ArtifactStore in parallelo a
  `node_rows` (o sostitutivo: `MODE=full_artifact` come 4° valore).
- Pointer su `node_meta.artifact_uri`.
- Nuovo endpoint `/runs/{rid}/nodes/{nid}/artifact` per download.

**Outcome:** UI diff completo via Parquet on-demand, senza pressione PG.

### Step 5 — Async persistence (opzionale)

- Spostare `write_node` su Celery task separato (fire-and-forget) per
  togliere I/O dal critical path dell'executor.
- Garantire ordering: aggregate hash gia' calcolato sync (per
  `runs.output_hash`), persistenza righe async.
- Risk: se l'async fallisce, `node_meta.row_count` puo' non riflettere
  realta'. Mitigation: marker `pending_persistence` o write inline retry.

**Outcome:** miglior UX su run lunghe; complessita' aggiuntiva.
Da considerare solo se Step 2-4 non bastano.

---

## 9. Rischi

| Rischio | Mitigazione |
|---|---|
| **UI diff completo non disponibile in preview_only/off** | Banner UI "modalita' preview, diff parziale". Step 4 abilita Parquet on-demand per progetti che richiedono full. |
| **Replay parziale** | Documentare: replay completo richiede `MODE=full` o artifact Parquet. Per `preview_only/off`, replay si basa su sorgente + canvas + parametri (deterministic). |
| **Compatibilita' canvas/run storici** | Run pre-Step 2 sono `full`. La policy si applica solo a run nuovi. `node_meta.persisted_rows_count = NULL` = legacy = full. UI tratta NULL come full. |
| **Retention cleanup** | Invariata: `ON DELETE CASCADE` su `node_rows`. In mode `off` non c'e' nulla da pulire (vantaggio collaterale). Per `artifact_uri`, aggiungere best-effort delete su S3 / local fs su run delete. |
| **Hash drift** | Aggregate hash sempre sul full → identico fra mode. Test integration assert su questo invariante. |
| **Memoria executor** | `compute_aggregate_hash` richiede `rows` in memoria. Stessa pressione di oggi (gia' in memoria). Nessun peggioramento. |
| **`preview_only` row sample non rappresentativo** | Doc UI: preview = prime N righe (deterministic, ordinato come arriva dal nodo). Se la UI serve per diagnosi statistica → upgrade a `full` o artifact. |

---

## 10. Raccomandazione finale + primo commit

### Raccomandazione

Procedere a fasi. **Step 1 (telemetria) prima**, come gate sull'ipotesi.
Se il `[PERF]` log conferma `write_node` come dominante, procedere
Step 2 (`preview_only` default) come singolo cambio sicuro.

Step 3-5 sono incrementi indipendenti, prioritizzabili dopo aver visto
il comportamento in produzione di Step 2.

**Posizione condivisa con il tuo punto:** preview_only globale default
+ aggregate hash sul full set e' il primo passo giusto. Non rompe
nessun contratto critico (downstream nodes non leggono `node_rows`,
final output e' separato, hash invariato), e taglia subito il bloat PG.

### Primo commit proposto (sicuro)

**Commit 1: telemetria — Step 1**
- File: `app/core/intermediate/writer.py`, `app/core/intermediate/db.py`.
- Aggiungere `[PERF]` log:
  - `write_node`: `op=write_node node_id=X rows=N status=S
    hash_compute_ms=H persist_ms=P total_ms=T`.
  - `write_node_atomic`: `op=node_rows_insert rows=N
    bytes_estimated=B duration_ms=D`.
- Niente modifiche di comportamento. Niente nuovi setting.
- Test: nessuno aggiunto (solo log).
- Smoke: 1 run su un canvas reale, verifica che i log compaiano e
  abbiano i campi attesi.

**Commit message:**
```
perf(intermediate): timing logs on write_node + write_node_atomic

Step 1 di intermediate persistence policy: prima di gating la persistenza
servono numeri. Aggiunti [PERF] log su write_node (hash compute + persist
totale) e write_node_atomic (rows count + bytes JSONB stimati + tempo
INSERT executemany).

Niente cambio di comportamento. Output: telemetria per decidere se
write_node e' davvero il bottleneck su dataset grandi (ipotesi del
design doc), o se il costo e' altrove (compute hash, executor, ecc.).
```

Dopo Step 1 e raccolta evidenze (5-10 run reali), Step 2 con default
`preview_only` e singola feature flag.

---

## Appendix — domande aperte

1. **Cap implicito per `preview_only`:** 500 e' un buon default? UI mostra
   ~50 righe per pagina, 500 da margine senza saturare. Da rivedere se
   utenti chiedono campioni piu' grandi.
2. **Mode per ambiente:** stesso default in dev/staging/prod? Probabile
   `full` in dev (utile per test diff), `preview_only` in prod. Setting
   per env, niente cambio codice.
3. **Override per-canvas:** valutare in fase 2 un campo
   `projects.intermediate_mode` opzionale che sovrascrive il default,
   per progetti dove diff completo e' parte del workflow.
4. **Retention pinned runs:** una pinned run in `preview_only` resta
   "incompleta" per sempre. Decisione: pinned implica `full` automatico?
   O accettiamo il trade-off (pinned conserva preview, hash e meta)?
   Propongo accettare per semplicita'; chi vuole replay completo pinna
   con `full` esplicito.

