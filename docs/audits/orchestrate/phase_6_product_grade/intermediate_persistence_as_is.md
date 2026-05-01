# Intermediate Persistence - AS-IS

Data: 2026-04-28

Scopo: descrivere cosa fa oggi Plenora quando salva dati intermedi di run,
prima di decidere la nuova policy performance. Questo documento non propone
ancora la soluzione finale: fotografa il comportamento attuale e i vincoli che
qualunque fix deve rispettare.

## Sintesi

Oggi gli intermedi vengono salvati in Postgres nello schema logico
`intermediate`, usando tre tabelle:

- `runs`: una riga per run, con summary e hash aggregati input/output.
- `node_meta`: una riga per nodo eseguito, con schema colonne, row count,
  status, tempi ed errori.
- `node_rows`: una riga Postgres per ogni riga del DataFrame del nodo, con
  `row_hash` e payload `row_data JSONB`.

Il punto critico performance non e' S3 in se'. S3 pesa sul caricamento sorgenti,
ma il blocco da minuti visto con `py-spy` e' in:

```text
ManipolaExecutor._applica_trasformazioni
  -> intermediate.writer.write_node
    -> intermediate.db.write_node_atomic
      -> DELETE node_rows per run/node
      -> executemany INSERT di una riga JSONB per ogni riga DataFrame
      -> UPSERT node_meta
```

Quindi, per un DataFrame da 2.37M righe, il sistema prova a trasformare milioni
di righe pandas in milioni di dict Python, calcolare un hash per riga,
serializzare ogni riga in JSON e inserirla in Postgres. Se succede dopo ogni
transform, il costo si moltiplica per il numero di nodi.

## Schema dati attuale

### `runs`

Fonte: `backend/app/db/intermediate_schema.sql`, `backend/app/core/intermediate/db.py`.

Contiene una riga per run:

- `run_id`
- `canvas_id` cioe' `project_id`
- `group_id`
- `timestamp`
- `status`: `running`, `completed`, `failed`
- `pinned`
- contatori: `total_rows_in`, `total_rows_out`, `total_rows_dropped`,
  `total_nodes`, `nodes_ok`, `nodes_error`, `nodes_skipped`
- `total_exec_time`
- `input_hash`, `output_hash`

Uso principale:

- lista run per progetto;
- confronto L1 fra run;
- idempotency guard: se una run con `run_id` e' gia' `completed`, alcuni task
  possono skippare la riesecuzione.

### `node_meta`

Una riga per nodo della run:

- `run_id`
- `node_id`
- `node_type`
- `row_count`
- `col_schema` JSON testuale
- `exec_time`
- `status`: esempi `ok`, `error`, `skipped`, `ok_sampled`, `ok_streamed`
- `error_msg`

Nota importante: oggi `node_meta` non contiene l'hash aggregato per singolo
nodo. `writer.write_node()` calcola e ritorna `node_hash`, ma il DB salva solo
metadata e righe. Gli hash persistiti a livello run sono `input_hash` e
`output_hash` in `runs`.

### `node_rows`

Tabella unica per tutti i nodi e tutte le run:

- `id BIGSERIAL`
- `run_id`
- `node_id`
- `row_idx`
- `row_hash`
- `row_data JSONB`

Indici:

- `(run_id, node_id)` per leggere le righe di un nodo;
- `(run_id, node_id, row_hash)` per confronto L2 fra due run.

Retention: `node_meta` e `node_rows` hanno FK verso `runs` con `ON DELETE
CASCADE`. Quando viene cancellata una run, i dati dei nodi vengono rimossi.

## Flusso di scrittura

### 1. Apertura run

I task creano una run con `intermediate.db.create_run(...)`.

Callsite principali:

- `workers/tasks/pipeline_task.py`: crea una run prima di chiamare
  `orchestrator.execute_pipeline(...)`.
- `workers/tasks/enrichment_task.py`: crea una run per arricchisci non
  eseguibile.
- `app/core/manipola/executor.py`: per esecuzioni ricorsive/orchestrate crea
  run quando necessario.

Gli errori di apertura intermediate sono trattati come non bloccanti in vari
punti: warning e pipeline che prova a continuare.

### 2. Scrittura nodo

Entry point comune:

```python
app.core.intermediate.writer.write_node(
    run_id=...,
    node_id=...,
    node_type=...,
    rows=df.to_dict(orient="records"),
    col_schema=df.dtypes.astype(str).to_dict(),
    exec_time=...,
    status="ok",
)
```

Dentro `write_node`:

1. Ordina i nomi colonna.
2. Calcola `_row_hash` MD5 per ogni riga, usando tutte le colonne.
3. Costruisce `rows_with_hash`.
4. Chiama `db.write_node_atomic(...)`.
5. Calcola `node_hash = compute_aggregate_hash(rows, col_schema)`.
6. Ritorna `node_hash` al caller.

Dentro `write_node_atomic`:

1. `_build_node_row_records(...)` trasforma ogni dict in:

   ```text
   (run_id, node_id, row_idx, row_hash, json.dumps(row_data))
   ```

2. In una singola transazione:

   ```sql
   DELETE FROM node_rows WHERE run_id = %s AND node_id = %s;
   INSERT INTO node_rows (...) VALUES (...);  -- executemany, una riga per record
   INSERT INTO node_meta (...) ON CONFLICT (...) DO UPDATE ...;
   ```

Questo e' atomico e idempotente per `(run_id, node_id)`, ma e' costoso su
DataFrame grandi.

## Dove viene chiamato oggi

### MANIPOLA

File: `backend/app/core/manipola/executor.py`.

Comportamento attuale:

- Dopo ogni nodo `transform` o `multi_join`, se esiste `run_id`, chiama
  `write_node(...)` con `current_df.to_dict(orient="records")`.
- Questa chiamata e' sincrona dentro `_applica_trasformazioni`.
- Il commento dice "non bloccante", ma in pratica blocca il worker finche'
  `write_node` non ritorna. L'errore e' non bloccante, non la scrittura.
- Il nodo `result` finale viene scritto con cap fisso a 10.000 righe.

Questo e' il punto osservato da `py-spy`:

```text
_applica_trasformazioni -> write_node -> write_node_atomic
```

Implicazione: su MANIPOLA, ogni transform grande puo' generare un full snapshot
Postgres.

### ESTRAI

File: `backend/app/db/query_executor.py`.

Comportamento attuale:

- Usa `INTERMEDIATE_SNAPSHOT_MAX_ROWS`, default 100.000.
- Se `df is None` per streaming: scrive solo metadata con `status="ok_streamed"`.
- Se `len(df) > cap`: scrive solo schema/count con `status="ok_sampled"` e
  `rows=[]`.
- Se sotto cap: scrive tutte le righe in `node_rows`.

ESTRAI ha gia' una guardia ragionevole contro lo snapshot completo di output
molto grandi.

### ARRICCHISCI Celery

File: `backend/workers/tasks/enrichment_task.py`.

Comportamento attuale:

- Per ogni nodo `api_enrich` con DataFrame non vuoto:
  - materializza `_rows = df.to_dict(orient="records")`;
  - avvia `threading.Thread(... daemon=True)` che chiama `write_node`.
- Anche il nodo `result` finale viene scritto in un thread daemon.
- `close_run` intermediate viene chiamato in thread daemon a fine task.

Questo riduce il blocco diretto della pipeline, ma non elimina il costo:

- la conversione `df.to_dict(...)` avviene prima di creare il thread;
- i thread daemon possono continuare a caricare Postgres;
- eventuali errori vengono loggati a debug/warning e non falliscono la run.

### ARRICCHISCI sync/helper

File: `backend/app/core/arricchisci/api_connection_support.py`.

Ha helper `_write_intermediate_snapshot(...)` best-effort che chiama
`write_node(...)` in modo sincrono. Va verificato quanto sia usato nel path
sincrono rispetto al task Celery.

### PIPELINE task

File: `backend/workers/tasks/pipeline_task.py`.

Il task:

- crea la run intermediate;
- passa `run_id` all'orchestrator;
- a fine pipeline chiama `intermediate.writer.close_run(...)`.

La scrittura dei singoli nodi avviene nei moduli delegati
(`estrai`, `manipola`, `arricchisci`).

## Flusso di lettura

Endpoint: `backend/app/core/intermediate/router.py`.

### Lista run

```text
GET /api/runs/{canvas_id}
```

Legge `runs` e restituisce summary.

### Preview dati nodo

```text
GET /api/runs/{run_id}/nodes/{node_id}/data?limit=...
```

Legge da `node_rows` ordinando per `row_idx`, espande `row_data JSONB` e
aggiunge `_row_hash`.

Default `limit=100`, massimo `5000`.

### Compare L1

```text
GET /api/runs/compare/l1?run_id_a=...&run_id_b=...
```

Legge solo `runs`, confronta:

- `input_hash`
- `output_hash`
- contatori righe
- tempo totale

Non legge `node_rows`.

### Compare L2

```text
GET /api/runs/compare/l2?run_id_a=...&run_id_b=...&node_id=...
```

Richiede `node_meta` presente in entrambe le run, poi legge `node_rows` e fa:

- righe rimosse: hash in A non presente in B;
- righe aggiunte: hash in B non presente in A;
- max 500 righe per lato nel payload.

Quindi il diff L2 oggi dipende dal fatto che `node_rows` contenga le righe
complete, non solo preview.

## Costi attuali

Per ogni nodo salvato full:

1. `df.to_dict(orient="records")`: materializza tutto il DataFrame in memoria
   come lista di dict Python.
2. `compute_row_hash` per ogni riga: loop Python su tutte le colonne.
3. `_to_jsonable` per ogni valore: normalizzazione di datetime, Decimal,
   float speciali, geometrie, bytes, liste/dict.
4. `json.dumps(... ensure_ascii=False)` per ogni riga.
5. `executemany` Postgres: una INSERT per ogni riga logica.
6. Indici su `node_rows` aggiornati per ogni riga.
7. `compute_aggregate_hash`: altro passaggio sui dati per hash aggregato.

Questo design e' corretto per inspection/diff fine-grained, ma non e'
sostenibile come default su milioni di righe e molti transform.

## Cosa viene salvato in S3 oggi

Il result finale usa `result_store`/`save_pipeline_result`: il payload finale
puo' finire in DB o S3 in base alle policy gia' esistenti. In Postgres resta la
metadata del result e l'eventuale `storage_key`.

Gli intermedi di cui parla questo documento NON seguono lo stesso modello:
oggi non sono blob su S3 con puntatore in Postgres. Sono righe JSONB in
`node_rows`.

Questa distinzione e' la causa della confusione: il result store e'
blob-oriented; intermediate store e' row-oriented.

## Cosa serve davvero conservare

Dalle API attuali emergono tre bisogni distinti:

1. Sapere se due run sono cambiate:
   - oggi: `runs.input_hash` / `runs.output_hash`;
   - non richiede salvare tutte le righe di tutti i nodi.

2. Vedere un campione di dati per debug UI:
   - oggi: `GET /runs/{run}/nodes/{node}/data`;
   - puo' funzionare anche con preview limitata.

3. Fare diff riga-per-riga fra due run su un nodo:
   - oggi: `compare/l2` usa `node_rows.row_hash`;
   - richiede full row hashes o almeno un indice di hash completo.

Il design attuale tratta sempre il caso 2/3 come se servisse full snapshot,
ma nella pratica il caso piu' frequente e' 1/2. Il caso 3 dovrebbe essere
opt-in o modalita' diagnostica, non default su ogni run.

## Invarianti da non rompere

Qualunque nuova policy deve preservare:

- `runs` sempre creato e chiuso, per audit/idempotency.
- `node_meta` sempre disponibile almeno con row count, schema, status.
- `input_hash` / `output_hash` persistiti quando disponibili.
- isolamento tenant via `group_id`.
- retention: cancellare una run deve cancellare i dati collegati.
- errori intermediate non devono far perdere il result finale, salvo modalita'
  debug esplicitamente strict.

## Problemi specifici AS-IS

1. MANIPOLA scrive full snapshot sincrono dopo ogni transform.

2. Il commento "non bloccante" in MANIPOLA e' fuorviante: la scrittura e'
   sincrona, solo l'errore viene ingoiato.

3. `node_hash` per nodo viene calcolato ma non persistito in `node_meta`.
   Questo limita una futura modalita' `preview_only`: per sapere se un nodo e'
   cambiato servirebbe salvare un hash aggregato per nodo.

4. ESTRAI ha gia' un cap globale (`INTERMEDIATE_SNAPSHOT_MAX_ROWS`), MANIPOLA
   no per i transform intermedi.

5. ARRICCHISCI usa thread daemon, ma materializza comunque `df.to_dict` nel
   thread principale prima del dispatch.

6. Il diff L2 dipende da `node_rows` full; se passiamo a preview-only, L2 deve
   dichiarare "non disponibile per questa run" oppure usare un artifact/hash
   dedicato.

## Schema AS-IS compatto

```text
pipeline_task / enrichment_task
  |
  +-- create_run(run_id, project_id, group_id)
  |
  +-- orchestrator.execute_pipeline(run_id)
        |
        +-- ESTRAI
        |     +-- result df
        |     +-- if rows <= INTERMEDIATE_SNAPSHOT_MAX_ROWS:
        |     |      write_node(full rows)
        |     +-- else:
        |            write_node(rows=[], status=ok_sampled)
        |
        +-- MANIPOLA
        |     +-- for each transform:
        |     |      current_df = transform(...)
        |     |      write_node(full current_df)    <-- hot path bloccante
        |     +-- result node:
        |            write_node(head(10000))
        |
        +-- ARRICCHISCI
              +-- for each api_enrich:
              |      rows = df.to_dict(...)
              |      thread(write_node(full rows))
              +-- result node:
                     thread(write_node(full rows))

write_node
  |
  +-- compute _row_hash per row
  +-- write_node_atomic
        |
        +-- DELETE node_rows(run_id,node_id)
        +-- INSERT N JSONB rows via executemany
        +-- UPSERT node_meta
  |
  +-- compute aggregate hash

close_run
  |
  +-- update runs summary/input_hash/output_hash
  +-- apply retention
```

## Conclusione operativa

La domanda "ha senso salvare l'intermedio di tutti i dati della catena?" oggi
ha una risposta pratica: no, non come default e non in forma row-per-row JSONB
sincrona.

La funzionalita' che serve davvero come default e':

- metadata sempre;
- schema sempre;
- row count sempre;
- hash aggregato per run e idealmente per nodo;
- preview limitata per UI;
- full rows solo opt-in/debug, oppure artifact separato.

Il passo successivo dovrebbe essere un design TO-BE con una policy globale, ad
esempio:

```text
INTERMEDIATE_PERSISTENCE_MODE=off | preview_only | full
INTERMEDIATE_PREVIEW_ROWS=500
```

Default consigliabile: `preview_only`.

In questa modalita' Postgres resta il catalogo e il posto dei metadati; S3 o
object storage diventano opzionali per artifact intermedi full, non dipendenza
obbligatoria per ogni run.
