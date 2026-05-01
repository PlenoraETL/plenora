# Phase 0.5f — Performance Baseline

Status: **analysis only — no code change, no test run, no invasive prod benchmark.**
Scope: baseline numerica e qualitativa derivata SOLO da artefatti già
disponibili (BUG.md, scripts read-only, log markers documentati,
intermediate persistence policy).

Read together with:
- `BUG.md` (cleanup + rigenerazione + chiusi recenti)
- `delta_post_storage_2026_04_28.md` (storage/intermediate batch)
- `hot_file_deep_dive_0_5a.md` (5 hot file backend)

---

## 0. Sorgenti di evidenza usate

| Sorgente | Path | Cosa fornisce |
| --- | --- | --- |
| BUG.md | `int_efm/BUG.md` | numeri concreti #22, #24, Wave 1/2 + cleanup |
| Intermediate run report | `URBY-ETL/backend/scripts/intermediate_run_report.py` | tabellina per-run di node_meta + COUNT(node_rows) + jsonb_bytes per drift detection |
| Bulk verdict | `URBY-ETL/backend/scripts/perf_bulk_verdict.py` | template di verdict-on-rates con soglie esplicite (§9.2 MySQL, §9.3 PG) — **non ha ancora numeri raccolti**, è il framework di lettura |
| `[PERF]` markers | grep `\[PERF\]` su `backend/app/` | 7 file con marker già emessi: `dataframe.py`, `result_store.py`, `cloud_backends.py`, `artifacts/resolver.py`, `intermediate/snapshot.py`, `db/results_db_service.py`, `arricchisci/api_connection_source_loading.py` |
| Intermediate policy | `backend/app/core/intermediate/policy.py:39–45` | enum mode: `metadata_only` / `preview_only` / `full_pg` / `full_artifact` |
| Perf tests esistenti | `backend/tests2/performance/` | 12 file test (`test_baseline_*`, `test_bench_*`, `test_correctness_*`) — gating su regressione, non baseline assoluta |

---

## 1. Baseline numerica disponibile oggi

### 1.1 Numeri concreti raccolti

Tutti i numeri citati di seguito sono **già documentati** in `BUG.md`.
Niente è derivato da nuovi run.

| Voce | Valore | Provenienza | Note |
| --- | --- | --- | --- |
| **#22 node_rows bulk insert** | **154s → 42s** (~3.7× speedup) | BUG.md riga 59 | `executemany` → `psycopg2.extras.execute_values` (`backend/app/core/intermediate/db.py:499`) |
| **#24 fingerprint sample** | **969–1469 ms → 16 ms** (~70× speedup su DF grandi) | BUG.md riga 61 | `compute_dataframe_fingerprint` con algo `pandas_sample_hash_v1` (sample-based, default 1000 righe) — `intermediate/snapshot.py:140`, `policy.py:123` |
| **Wave 2 manipola finale** | run `c73adfff-…`, **32.2s** end-to-end, **6'819 rows out** | BUG.md riga 31 | dataset `v1geo_strato_finale_v2`, 5 project_source upstream, intermediate `metadata_only` (20 node_meta, 0 node_rows persistite) |
| **Wave 1 estrai upstream** | **5/5 success** rigenerati su object storage | BUG.md riga 25 | `v1geo_strato_0..4` con `data IS NULL`, `storage_key NOT NULL` |
| **Result distribution post-cleanup** | **6/6 object_storage, 0/6 db_legacy** | BUG.md riga 42 | sui 6 project target |
| **S3 bucket size** | ~601 MB cumulativi (6 oggetti freschi) | BUG.md riga 45 | `plenora-runtime-artifacts-…` |
| **Cleanup volumi rimossi** | 20 results + 22 runs + 73 node_meta + **20'000 node_rows** + 12 oggetti S3 (~2.21 GB) | BUG.md righe 15–16 | scope: 6 project target |
| **`log_runs` preservato** | 35 righe audit trail intatto | BUG.md riga 46 | nessun reset audit |

### 1.2 Inferenze indirette consentite (NON benchmark)

- **Wave 2: 32.2s / 6'819 rows ≈ 4.7 ms/riga end-to-end** per pipeline manipola completa con 5 sorgenti upstream + 20 nodi.
  Caveat: include estrai upstream già rigenerati (cache caldo).
- **Result blob fetch via object storage** ha ora un breakdown loggato:
  `[PERF] s3_download op=get_bytes ... duration_ms=...` (`results_db_service.py:106`)
  + `[PERF] get_result_data op=fetch_blob backend=object_storage ... duration_ms=...` (`results_db_service.py:149`).
  **Numeri non raccolti** (i log esistono ma non c'è dashboard che li aggreghi).
- **Intermediate `metadata_only` default**: 0 byte node_rows, solo node_meta + node_hash + col_schema. Wave 2 → 20 node_meta scritti, 0 righe in node_rows. Costo I/O DB intermedio ≈ 20 × INSERT su `node_meta` = trascurabile.

### 1.3 Numeri NON disponibili

| Cosa manca | Perché |
| --- | --- |
| Latency p50/p95/p99 endpoint API | nessuna istrumentazione lato API gateway / FastAPI middleware |
| RPS / throughput lato HTTP | idem |
| Memory peak per task Celery | non tracked; solo logger output (no resident set size) |
| Postgres query time / lock contention | non aggregato; solo `[PERF] db_ms=` puntuali |
| `[PERF] s3_download` / `get_result_data` numeri reali | log emessi ma non aggregati |
| Frontend bundle size / TTI / FCP | nessun framework metrics (no Lighthouse CI, no Sentry web vitals) |
| Celery queue depth / wait time | non esposto |
| Object storage retention/cleanup cost | nessuna lifecycle policy attiva (vedi BUG.md backlog) |

---

## 2. Cosa è migliorato rispetto a prima

### 2.1 Riduzione DB pressure intermedio

| Asse | Prima | Dopo | Riferimento |
| --- | --- | --- | --- |
| `node_rows` default produzione | sempre full DataFrame in JSONB | **0 righe** in `metadata_only` (default) | `policy.py:11–14`, `policy.py:154` |
| Bulk insert | `executemany` riga-per-riga | `execute_values` con espansione `VALUES %s` | BUG.md #22, `intermediate/db.py:470–499` |
| Run intermediate per pipeline 6'819 rows | full_pg = ~6'819 INSERT × 20 nodi = ~136'000 INSERT JSONB | metadata_only = ~20 INSERT su `node_meta` | inferenza dalla policy + Wave 2 numeri |

### 2.2 Riduzione costo fingerprint

| Asse | Prima | Dopo |
| --- | --- | --- |
| Hash DF per dataset 100k+ righe | 969–1469 ms (full hash riga-per-riga) | 16 ms (sample-based 1000 righe) |
| Algoritmo | implicito via pandas | `pandas_sample_hash_v1` esplicito + versione `pandas_hash_v1` per full mode |

Caveat: **`pandas_sample_hash_v1` è probabilistic** (sample 1000 righe, hash colonne aggregate). Collisioni teoriche possibili su dataset divergenti che condividono il sample. Trade-off accettato (BUG.md #24 chiusura): non è un hash crittografico, serve a deduplicare.

### 2.3 Storage path cleaner

| Asse | Prima | Dopo |
| --- | --- | --- |
| Result writes con `STORAGE_POLICY=object_storage` | hybrid threshold-based (BYTEA fallback sotto soglia) | **fail-closed** su capability off, **mai** BYTEA fallback (BUG.md A1 batch) |
| Result reads | implicito BYTEA o S3 | log esplicito `backend=object_storage` vs `backend=db_legacy` (`results_db_service.py:149,165`) |
| BACKUP_BUCKET fallback | consentito per runtime | **rifiutato in policy `object_storage`** (vedi `validate_storage_config`, ratificato in 0.5e §0) |

### 2.4 Path streaming (no OOM)

- `query_executor.py:619–841`: chunk-write CSV/Parquet con `fetchmany(CHUNK_SIZE)` reale (post-C11 fix il chunking era cosmetico).
- `cloud_backends.py:91, 275, 283, 301–303`: streaming upload/download (boto3 `multipart_chunksize=8MB`, iter_chunks=64KB).
- `orchestrator/router.py:758–782`: `StreamingResponse` + `_stream_chunks` per download endpoint.
- `arricchisci/api_connection_rest_file.py:343–349`: `iter_content(chunk_size=65536)` per file processor remoti.

Tutto questo riduce peak memory rispetto a una baseline "carica tutto in RAM".

---

## 3. Hotspot runtime ancora sospetti

Lista derivata da lettura codice + commenti documentati. **Sospetti, non misurati.**

### 3.1 Boundary I/O ad alto fan-out

| Hotspot | Path | Sospetto | Marker disponibile |
| --- | --- | --- | --- |
| `_load_source_project` | `arricchisci/api_connection_source_loading.py:434` | latency dominata da S3 GET (1 download per project_source) — Wave 2 = 5 sorgenti upstream | `[PERF] load_source_project op=load_total ... duration_ms=` |
| `_load_file_source` | idem riga 489 | analogo per FileSourceService blob fetch | `[PERF] load_file_source op=load_total ... duration_ms=` |
| `s3_download` per result | `results_db_service.py:106` | concat chunks in memoria via `b"".join(chunks)` — peak memory == size del result | `[PERF] s3_download op=get_bytes size_bytes=... duration_ms=` |
| `get_result_data fetch_blob` | `results_db_service.py:149,165` | tot DB+S3 path | `[PERF] get_result_data op=fetch_blob backend=...` |

### 3.2 ApiEnrichNode HTTP loop

- `enrichment_execution_engines.py:538–540`: `asyncio.gather(*chunk, return_exceptions=True)` con CHUNK fixed.
- Throughput limitato da rate_limit del provider esterno (`options.rate_limit` per connection).
- **Nessun marker `[PERF]` aggregato sul totale enrichment task** — solo progress callback.
- Fingerprint via sample = **già fix**, batch parallel guard fail-hard = già fix.

### 3.3 Frontend ApiEnrichNode anti-loop

`ApiEnrichNode.jsx:287–303` — il `useEffect propagate columns` con `JSON.stringify(normalize(...))` compare ad ogni render. Per nodi con 100+ colonne → JSON.stringify O(N) ad ogni input change upstream. **Sospetto perf**, non misurato.

### 3.4 ResponseConfigBuilder analyzeJsonStructure ricorsivo

`ResponseConfigBuilder.jsx:733` — `analyzeJsonStructure` senza depth cap su oggetti (cap solo su array primitivi `slice(0,10)`). Sample API molto profondo → tempo cubico. **Sospetto perf** (non misurato), già citato in 0.5b §2.4.

### 3.5 ProjectList loadData parallel

`ProjectList.jsx:96–106` — `Promise.all` su 8 endpoint. Latency totale = max delle 8, ma se un endpoint è 2s gli altri completano e l'utente aspetta. **Sospetto UX**, non misurato.

### 3.6 query_executor con `rows.extend` su result set

`query_executor.py:631` — `rows.extend(tuple(row) for row in chunk)` accumula tutto in lista `rows`. Documentato in commento: "C11 il chunking era cosmetico". Path `fetchmany` esiste ma il caller a volte usa `fetchall` equivalente. Verificare se chiamato per result set grandi.

### 3.7 ArcGIS write_batch loop

`features/arcgis_client.py:1715–1807` — batch_size default 1000 features. Per 100k features = 100 round-trip HTTP. Already token-aware (refresh mid-batch). **Latency dominante = rete a ArcGIS**, non locale.

### 3.8 Intermediate full_artifact write

`intermediate/snapshot.py:417–453` — quando `persistence_mode=full_artifact`:
- `df.to_parquet(BytesIO)` serializzazione in RAM
- `cloud_backends.upload_bytes(...)` con multipart chunking
- `artifact_ms` loggato in `[PERF] op=record_*_snapshot ... artifact_ms=...`

**Non in produzione di default** (default = `metadata_only`). Hot solo se l'operatore abilita `full_artifact` per audit/diff L2.

---

## 4. Metriche mancanti

### 4.1 Per area

| Area | Metrica mancante | Perché serve |
| --- | --- | --- |
| **Storage** | p50/p95 `s3_download op=get_bytes duration_ms` | dimensionare timeout, individuare key lente |
| **Storage** | p50/p95 `get_result_data op=fetch_blob backend=object_storage` | confronto S3 vs db_legacy |
| **Intermediate** | `[PERF] op=record_*_snapshot fingerprint_ms / db_ms / artifact_ms` aggregati | identificare nodi con `db_ms` outlier |
| **Intermediate** | drift `persisted_rows_count != COUNT(node_rows)` | catched solo da `intermediate_run_report.py` quando lanciato manualmente |
| **Project source loading** | p50/p95 `load_source_project duration_ms` per project_id | identificare chain pesanti |
| **Frontend** | bundle size, TTI, FCP, INP web vitals | nessun framework attivo |
| **Frontend** | render time per nodo Canvas (ReactFlow profiler) | sospetti §3.3, §3.4 non misurati |
| **Deploy** | restart time service, gap di disponibilità | `deploy.sh` resta opaco |
| **Celery** | queue depth `pending`, p95 wait time | `update_state(state="PROGRESS")` esiste ma non aggregato |
| **HTTP API** | p50/p95 latency per endpoint | manca middleware metric |
| **DB** | slow query log, lock wait events | non aggregato |
| **Memoria** | peak RSS per worker Celery | non tracked |

### 4.2 Coverage `[PERF]` markers attuali

Marker emessi (verificati):

| Marker | File | Op coperta |
| --- | --- | --- |
| `load_dataframe op=parse fmt=...` | `utils/dataframe.py:164` | parse file (csv/xlsx/geojson/gpkg/shp/gdb) |
| `load_source_project op=load_total` | `arricchisci/api_connection_source_loading.py:434` | load DataFrame da progetto upstream |
| `load_file_source op=load_total` | `api_connection_source_loading.py:489` | load DataFrame da file_source |
| `s3_download op=get_bytes` | `db/results_db_service.py:106` | S3 GET roundtrip |
| `get_result_data op=fetch_blob backend=...` | `results_db_service.py:149,165` | DB+S3 totale |
| `write_temp_blob op=materialize` | `core/artifacts/resolver.py:394` | scrittura blob in temp file |
| `op=record_*_snapshot ... fingerprint_ms db_ms artifact_ms` | `core/intermediate/snapshot.py:482` | snapshot intermedio (final/checkpoint) |
| `op=skip_checkpoint` | `intermediate/snapshot.py:366` | policy-resolve only su skip path |

Non ancora coperti da marker:
- enrichment task end-to-end (no marker su `execute_enrichment_task`)
- carica task end-to-end (no marker su `execute_carica_task`)
- ArcGIS `query_features` paginato (loop interno)
- ArcGIS `_write_batch` loop
- ReactFlow render time

---

## 5. Dashboard / log query raccomandate

### 5.1 Query SQL su `logs.details` (PG JSONB, già abilitato dal whitelist Batch 3.10)

Tutte read-only. Pattern dichiarato in `perf_bulk_verdict.py` come template.

| ID | Scopo | Query (forma) |
| --- | --- | --- |
| Q-PERF-1 | p50/p95 `s3_download` per chiave | `SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY (details->>'duration_ms')::float), percentile_disc(0.95) ... FROM logs WHERE details->>'op' = 'get_bytes' AND ts BETWEEN $1 AND $2` |
| Q-PERF-2 | distribuzione `backend=object_storage` vs `db_legacy` | `SELECT details->>'backend', COUNT(*), AVG((details->>'duration_ms')::float) FROM logs WHERE details->>'op' = 'fetch_blob' GROUP BY 1` |
| Q-PERF-3 | top 20 `record_final_snapshot` per `db_ms` | `... ORDER BY (details->>'db_ms')::float DESC LIMIT 20` |
| Q-PERF-4 | `load_source_project` p95 per `project_id` | `... GROUP BY details->>'project_id' HAVING COUNT(*) >= 5` |
| Q-PERF-5 | drift `persisted_rows_count` cross-run | invocazione manuale `python scripts/intermediate_run_report.py <run_id>` |
| Q-PERF-6 | full_artifact `artifact_ms` distribution | `... WHERE details->>'persistence_mode' = 'full_artifact' AND details->>'artifact_status' = 'complete'` |

**Pre-requisito**: il logger PostgresLogHandler deve persistere `details` in JSONB (verificato dal modello esistente in `app/utils/logger.py` + whitelist `_BULK_TELEMETRY_EXTRA_WHITELIST`). Niente nuovi schema.

### 5.2 Dashboard suggerite (non implementate)

Lista di pannelli che farebbero valore con i marker già emessi:

1. **Storage backend split** — pie/timeline `backend=object_storage` vs `db_legacy` su finestra ultimi 7/30 giorni.
2. **S3 GET latency** — istogramma `duration_ms` di `op=get_bytes`, filtrabile per `size_bytes` bucket (small/medium/large).
3. **Intermediate snapshot mix** — bar chart per `persistence_mode` e `op`.
4. **Top slow loaders** — top-N `load_source_project` e `load_file_source` per p95.
5. **Result fetch breakdown** — confronto `s3_download` interno vs `get_result_data` totale → quanto è DB lookup vs S3.

Tool consigliato: **dashboard SQL su PG con Grafana o Metabase** sopra `logs` table. Niente APM nuovo.

---

## 6. Performance risks per area

### 6.1 Storage (object storage)

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| S3 GET latency variabile (cross-region o cold storage) | UX: result download > 5s | nessuna misura aggregata, marker disponibile | Streaming `get_object_stream` (no full-buffer) per upload; lettura ancora `b"".join(chunks)` |
| Bucket retention non gestita | costi crescita lineare | BUG.md backlog "Retention / cleanup artifact storage" | nessuna; lifecycle policy proposta in `backend/deploy/terraform/lifecycle.tf` ma non confermato applicato |
| Migration legacy globale fuori 6 progetti | row con `storage_key=NULL AND data NOT NULL` | BUG.md backlog | rinviata "solo se serve operativamente" |
| `BACKUP_BUCKET` fallback in `hybrid` policy | drift bucket runtime/admin | A1 batch lo blocca solo in `object_storage` | warning log su fallback, non block |

### 6.2 Intermediate persistence

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| Switch `metadata_only → full_artifact` per audit causa carico Parquet+S3 | snapshot lento, peak memory `df.to_parquet(BytesIO)` | `snapshot.py:434` | best-effort: errori → `artifact_status=failed`, run prosegue |
| Diff L2 cross-run con `full_artifact` non ancora implementato | UX: 501 al user | `router.py:449`, BUG.md backlog | semantica esplicita 422/501, no fake response |
| `pandas_sample_hash_v1` collision teorica | falso positivo cache hit | `policy.py:123`, BUG.md #24 | trade-off accettato (sample 1000 righe + col aggregate) |
| Async writer artifact mancante | scrittura sync blocca task | BUG.md backlog "Async writer artifact: solo se i numeri runtime lo richiedono" | dichiarato non urgente |
| `node_rows` legacy in `full_pg` mode | bloat pg_class evitato (singola tabella JSONB), ma rows × nodi grande | `intermediate_schema.sql:78–117` | mode default è `metadata_only`, full_pg = legacy/debug |

### 6.3 Project source loading

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| Catena Manipola = N source × N S3 GET sequenziali | Wave 2: 5 sorgenti = 5 round trip serial | `load_source_project` chiamata sequenziale nel loop pipeline | nessuna prefetch / parallelizzazione |
| Fallback `_filesystem_fallback_load_project` | retention DB scaduta → load da disco locale | `api_connection_source_loading.py:422–426` | fallback documented, ma silenzioso se path mancante |
| Recompute upstream on-demand `_recompute_upstream_project` | pipeline ricorsiva → moltiplicazione lavoro | `api_connection_source_loading.py:411` | trigger solo on-failure, non a freddo |
| `filter_visible_columns` post-load | costo trascurabile, ma applicato sempre | `:430, 485` | OK |

### 6.4 Frontend

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| `ApiEnrichNode` JSON.stringify normalize O(N×col) per render | jank UI su nodi con molte colonne | `ApiEnrichNode.jsx:287–303` | anti-loop guard (no infinite loop), ma jank possibile |
| `ResponseConfigBuilder.analyzeJsonStructure` ricorsivo no-cap | freeze UI su sample profondo | `ResponseConfigBuilder.jsx:733–786` | cap solo su array primitivi (`slice(0,10)`) |
| `ProjectList.loadData` parallel di 8 endpoint | TTI bloccato fino al più lento | `ProjectList.jsx:96–106` | `.catch(() => default)` per evitare hard fail |
| Bundle size, code-splitting | non misurato | nessun web-vitals | nessuna |
| `useHexCanvas` re-paint su ogni render | redraw canvas hex inutile | `utils/hexCanvas.js` (100 LOC) | nessuna; trascurabile |

### 6.5 Deploy

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| systemd restart concatenato `pip install -q + restart 3 unit` | gap di disponibilità non quantificato | `URBY-ETL/deploy.sh` (vedi 0.5e §1.1) | nessuna; deploy = downtime breve |
| Cold start FastAPI/Celery dopo restart | primi request più lenti per warmup | nessuna misura | uvicorn 2 workers, celery concurrency=4 |
| Frontend pre-swap distruttivo `rm -rf dist/*` | finestra 404 utenti | `deploy.sh` (0.5e R5) | proposto atomic swap (post-prod), non in atto |

### 6.6 DB / Postgres

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| `node_rows` con 20'000 righe pre-cleanup → bloat | INSERT/DELETE lenti su pg_catalog | BUG.md cleanup numbers | resolved: cleanup eseguito + default `metadata_only` |
| FK ON DELETE CASCADE `runs` → `node_rows`, `node_meta` | DELETE pesante per run grandi | `intermediate_schema.sql:54,94` | OK ma test su scala mancante |
| Lock contention su `runs` aggiornati da scheduler + worker | `set_runtime_status('running')` + heartbeat refresh | `execution_manager.py:391, 159` | retry wrapper transient (psycopg2.OperationalError + DeadlockDetected, 0.5a §5.5) |
| Nessun `EXPLAIN ANALYZE` periodico su top query | regression silente | nessuno | missing |

### 6.7 Boundary esterni (ArcGIS / API enrichment)

| Risk | Impatto | Evidenza | Mitigation in place |
| --- | --- | --- | --- |
| ArcGIS rate limit 429 | retry 3× con backoff | `arcgis_client.py:436–588` | gestito; budget bounded |
| ArcGIS token expiry mid-batch | pre-C12 falliva tutto, post-fix refresh per-batch | `arcgis_client.py:1761` | C12 chiuso |
| Provider API esterno rate_limit per connection | enrichment lento | `options.rate_limit` configurabile | OK |

---

## 7. Cosa misurare prima di ottimizzare

Ordine di priorità (chi guadagna evidenza, non chi promette speedup):

1. **Aggregare i `[PERF]` marker già emessi** — Q-PERF-1..Q-PERF-6 su PG `logs` table. Effort ~1–2h SQL, costo zero su prod. **Senza questo, qualsiasi "ottimizzazione" è fede.**
2. **Eseguire `intermediate_run_report.py` sui 6 project target** dopo qualche giorno di run — confermare drift = 0 e jsonb_bytes ≈ 0 (`metadata_only`).
3. **Logger middleware HTTP** per p50/p95 endpoint API — feature nuova, ~3h, basso rischio. Senza non vediamo cosa l'utente percepisce.
4. **Frontend web vitals** — Lighthouse CI su frontend build. Effort ~2h.
5. **Postgres slow query log** — abilitare `log_min_duration_statement = 500ms` su prod. Read-only sul DB, zero codice.
6. **Bench shortlist su scale prod-like** — `tests2/performance/test_bench_*` esistono ma sono micro-bench geo. Mancano end-to-end pipeline su dataset > 100k righe.

Solo dopo (1)–(5) ha senso parlare di ottimizzazioni: query lente, hot path, refactor ad alto payoff.

---

## 8. Raccomandazione per area

| Area | Stato evidenza | Hotspot identificato | Raccomandazione |
| --- | --- | --- | --- |
| **Storage object_storage path** | numeri storici #22/#24 + Wave 1/2; marker emessi | latency S3 non aggregata | **MONITOR**: aggrega Q-PERF-1/2/6 → dashboard. No optimize finché numeri non parlano |
| **Intermediate persistence** | mode policy chiara, drift detection on-demand via script | `full_artifact` write sync | **NO-OP**: default `metadata_only` è già il path produzione. `full_artifact` ottimizzazione (async writer) è backlog dichiarato "solo se i numeri lo richiedono" |
| **Project source loading (Manipola)** | marker emessi, non aggregati | catena 5 sorgenti seriale | **MONITOR**: aggrega Q-PERF-4. Solo se p95 > 10s/source → considerare prefetch parallelo |
| **Result fetch (download)** | marker emessi, no aggregato; size 601 MB su 6 result | `b"".join(chunks)` peak memory == size | **MONITOR**: aggrega Q-PERF-1. Streaming-to-response è già in `orchestrator/router.py:758`, ma `get_result_data` materializza in memoria |
| **Frontend rendering** | nessun framework metrics | ApiEnrichNode JSON.stringify, ResponseConfigBuilder recursion | **MONITOR**: aggiungere web-vitals + ReactFlow profiler stitch. No-op senza dati |
| **Deploy / cold start** | nessuna misura | `deploy.sh` restart cascade | **NO-OP** sul perf, MONITOR su availability (vedi 0.5e §6 critical path 1–7) |
| **DB / lock contention** | retry wrapper sano; bloat node_rows risolto | `EXPLAIN` su top query mancante | **MONITOR**: enable `log_min_duration_statement` PG, raccolta 7 giorni |
| **Boundary ArcGIS / external API** | retry/refresh chiusi (C12) | rate_limit dipendente dal provider | **NO-OP**: gestito a livello connection settings |
| **Celery workers** | concurrency=4, no queue metric | wait time non visibile | **MONITOR**: minimo logging queue depth |
| **`pandas_sample_hash_v1` collision** | 0 incidenti registrati | teorico | **NO-OP**: trade-off accettato e documentato |

### 8.1 Sintesi

- **MONITOR è la maggioranza**: i marker `[PERF]` esistono ma non sono aggregati. Il primo investimento è la lettura, non la riscrittura.
- **NO-OP** dove la decisione architetturale è già stata presa (intermediate default, sample fingerprint, retry wrappers).
- **OPTIMIZE: nessuno** raccomandato senza prima MONITOR. Coerente con la regola di Phase 0 ("misurare prima di toccare").

---

## 9. Stato Phase 0.5f

**Status: analysis only. Nessun nuovo run, nessun benchmark, nessun codice modificato.**

Output prodotti:
- baseline numerica derivata da BUG.md (#22, #24, Wave 1/2, cleanup, distribuzione storage)
- inventario marker `[PERF]` già emessi (8 marker su 7 file)
- mapping coverage marker → metriche mancanti
- Q-PERF-1..6 query SQL su `logs.details` (read-only, niente schema change)
- 7 aree di rischio con mitigation in place + raccomandazione MONITOR/NO-OP/OPTIMIZE
- prerequisiti misura (6 step) prima di qualsiasi optimize

Output **NON** prodotti (deliberatamente):
- nessun nuovo run su prod
- nessun benchmark invasivo
- nessuna proposta di feature flag o code change
- nessuna scadenza temporale

Pending Phase 0:
- 0.5c — Q1–Q4 SQL queries (`compat_residue_evidence_0_5c.md`)
- 0.5g — UX / product readiness

---

*End of Phase 0.5f — Performance Baseline.*
