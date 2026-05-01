# Intermediate Persistence - TO-BE Design

Data: 2026-04-28

Documento collegato:

- AS-IS: `orchestrate/phase_6_product_grade/intermediate_persistence_as_is.md`
- Policy checkpoint MANIPOLA:
  `orchestrate/phase_6_product_grade/manipola_checkpoint_policy.md`
- Design precedente: `orchestrate/phase_6_product_grade/intermediate_persistence.md`

Questo documento aggiorna il design con una decisione importante: non basta
troncare le righe dentro `write_node`. Il problema nasce gia' prima, quando i
caller fanno `df.to_dict(orient="records")` sul DataFrame completo. Il TO-BE
deve evitare sia la scrittura row-per-row in Postgres sia la materializzazione
full in lista di dict Python.

## Obiettivo

Rendere la pipeline veloce e stabile su dataset grandi, senza perdere:

- audit della run;
- schema e row count per ogni nodo;
- possibilita' di sapere se i dati sono cambiati tra run;
- preview/debug ragionevole;
- opzione di salvare intermedi completi quando serve davvero.

## Non obiettivi

- Non trasformare S3 in dipendenza obbligatoria del core.
- Non fare delta storage nel primo batch.
- Non preservare come default il diff riga-per-riga completo su ogni nodo.
- Non introdurre setting per singolo progetto nel primo batch: prima policy
  globale di sistema.

## Decisione architetturale

Separiamo quattro concetti che oggi sono fusi in `write_node`:

1. Metadata del nodo: persistiti per result e per nodi checkpoint.
2. Fingerprint/hash del nodo: persistito per result e per nodi checkpoint,
   secondo policy.
3. Preview righe: persistita solo fino a un cap piccolo.
4. Payload completo intermedio: opzionale, asincrono, su artifact store.

Nota di coerenza: "sempre" significa sempre per gli snapshot che la policy ha
deciso di registrare. Non significa calcolare hash e metadata full per ogni
transform semplice quando `MANIPOLA_NODE_FINGERPRINTS=checkpointed`. Se in
futuro serve una timeline di tutti i nodi anche senza fingerprint, va aggiunta
una modalita' esplicita `skipped_meta`; non fa parte del primo batch.

Postgres resta il catalogo operativo. Gli object store, incluso S3, sono solo
backend per artifact grandi quando la policy lo richiede.

La soluzione deve essere uniforme: ESTRAI, MANIPOLA e ARRICCHISCI non devono
avere logiche custom sparse nei rispettivi executor. Tutti devono passare da
un unico layer `app.core.intermediate`, che decide cosa fare in base a:

- modulo (`estrai`, `manipola`, `arricchisci`);
- tipo snapshot (`final` o `checkpoint`);
- nodo;
- policy globale/progetto/nodo.

Gli executor devono diventare caller sottili: producono DataFrame e chiamano
un'API ad alto livello. Non devono sapere se la policy salva zero righe, preview
o artifact.

Per MANIPOLA c'e' una seconda decisione obbligatoria: la persistenza
intermedia non deve piu' stare nel critical path del loop trasformazioni.
Oggi il blocco reale e':

```text
for transform:
    current_df = transform(...)
    write_node(current_df.to_dict(...))   # blocca qui
```

Il TO-BE deve diventare:

```text
for transform:
    current_df = transform(...)
    enqueue_dataframe_snapshot(current_df)  # ritorna subito o quasi
```

Questo richiede un writer asincrono thread-safe: bounded queue, worker
dedicato, errori tracciati in `node_meta`, e nessuna mutazione del DataFrame
condiviso dopo l'enqueue.

Regola importante: il worker async non deve possedere un riferimento a un
DataFrame mutabile della pipeline. Prima dell'enqueue il layer snapshot deve
estrarre un payload immutabile e limitato dalla policy:

- schema;
- row count;
- hash, se richiesto dalla policy;
- preview gia' convertita in righe, al massimo `INTERMEDIATE_PREVIEW_ROWS`;
- eventuale riferimento a un artifact temporaneo, solo per `full_artifact`.

Se la policy dice "non registrare questo checkpoint", la funzione deve tornare
prima di calcolare hash o preview. Il no-op deve essere economico.

Terza decisione specifica per MANIPOLA: non ha senso salvare ogni nodo della
catena. Le trasformazioni semplici spesso sono passaggi tecnici; i checkpoint
utili per debug/change tracking sono pochi e riconoscibili. Il default deve
essere salvare solo nodi critici, non ogni transform.

## Policy globale

Nuove variabili di sistema:

```text
INTERMEDIATE_PERSISTENCE_MODE=preview_only
INTERMEDIATE_PREVIEW_ROWS=500
INTERMEDIATE_FULL_ARTIFACT_FORMAT=parquet
MANIPOLA_INTERMEDIATE_CHECKPOINTS=critical
ESTRAI_INTERMEDIATE_CHECKPOINTS=final_only
ARRICCHISCI_INTERMEDIATE_CHECKPOINTS=final_only
```

Valori ammessi per `INTERMEDIATE_PERSISTENCE_MODE`:

| Mode | Cosa salva | Uso |
|---|---|---|
| `metadata_only` | `runs` + `node_meta` + `node_hash`; zero `node_rows` | massima performance |
| `preview_only` | metadata + hash + prime N righe in `node_rows` | default consigliato |
| `full_artifact` | metadata + hash + preview + artifact completo async | debug/replay mirato |
| `full_pg` | comportamento legacy: tutte le righe in `node_rows` | compat/debug temporaneo |

Default consigliato in produzione: `metadata_only`.

Default possibile in sviluppo: `metadata_only`; `preview_only` o `full_pg` solo
quando si sta testando debug/diff legacy in modo esplicito.

Policy checkpoint MANIPOLA:

| Valore | Cosa salva in MANIPOLA | Uso |
|---|---|---|
| `none` | solo run/result metadata, niente nodi intermedi | massima velocita' |
| `critical` | nodi critici + result | default consigliato |
| `marked` | solo nodi con flag canvas `save_intermediate=true` + result | controllo manuale |
| `all` | tutti i transform/multi_join + result | debug legacy |

Default consigliato: `critical`.

La classificazione dettagliata vive in
`manipola_checkpoint_policy.md`. Regola sintetica: critici sono join/merge,
reshape, aggregazioni, filtri/dedup/cardinality-changing e operazioni GIS di
overlay/dissolve/explode. Transform locali come trim, normalize, rename e cast
non vengono salvati di default. Transform che aggiungono colonne semplici sono
`marked-only`: si salvano se il nodo ha `save_intermediate=true`.

Policy ESTRAI:

ESTRAI deve salvare solo il risultato finale. Non introduciamo checkpoint
intermedi nel modulo ESTRAI.

Nel TO-BE:

- il result finale continua a essere salvato nel result store;
- l'intermediate puo' registrare metadata/hash/preview del result finale se
  serve alla UI run;
- nessuno snapshot aggiuntivo oltre al finale;
- il cap legacy `INTERMEDIATE_SNAPSHOT_MAX_ROWS` viene ricondotto alla policy
  comune, ma senza estendere il numero di checkpoint.

Policy ARRICCHISCI:

ARRICCHISCI deve salvare solo il risultato finale. Non salviamo piu' snapshot
per ogni nodo `api_enrich` come default.

Nel TO-BE:

- il result finale continua a essere salvato nel result store;
- l'intermediate puo' registrare metadata/hash/preview del result finale;
- i nodi `api_enrich` non producono snapshot intermedi;
- eventuale salvataggio di un nodo `api_enrich` sara' futuro e solo se il nodo
  viene marcato esplicitamente dal frontend con `save_intermediate=true`.

Questa scelta semplifica molto: MANIPOLA e' l'unico modulo con vera catena di
checkpoint intermedi; ESTRAI e ARRICCHISCI restano final-output-only.

## Perche' non solo S3

Spostare tutto su S3 risolve il bloat Postgres, ma ci lega comunque a I/O
remoto se viene fatto nel critical path. Il punto corretto e':

- metadata e hash sync, piccoli, in Postgres;
- preview piccola sync, in Postgres;
- full artifact async, usando il backend configurato.

Il backend artifact deve restare astratto:

```text
local filesystem in dev
S3 in prod se configurato
altro backend futuro possibile
```

Quindi il sistema non e' "mani e piedi su S3": dipende da un object store
astratto solo per chi abilita `full_artifact`.

## Object storage come capability globale

L'object storage non deve essere un requisito implicito degli intermedi, ne'
una dipendenza sparsa nei singoli moduli. Deve essere una capability globale di
piattaforma.

Settings trasversali:

```text
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=s3        # s3 | minio | local | webdav | none
OBJECT_STORAGE_BUCKET=plenora-runtime-artifacts-112011134642-eu-south-1
OBJECT_STORAGE_PREFIX=
```

Regola:

```text
Se OBJECT_STORAGE_ENABLED=false, nessun modulo deve tentare chiamate S3.
```

Effetti:

- `result_store`: non prova S3; usa solo backend ammessi dalla policy locale/DB.
- intermediate `full_artifact`: non disponibile; richiesta esplicita del nodo
  produce errore operativo oppure degrada solo se una policy lo consente
  esplicitamente.
- `admin_backups`: endpoint S3 disabilitato oppure risposta operativa chiara,
  senza tentare boto3.
- `imdf bundles`: niente lookup S3; solo cache locale o feature dichiarata non
  disponibile.
- nuovi artifact runtime: devono passare da un layer object storage comune,
  mai da boto3 diretto nei moduli business.

Backend supportabili:

| Backend | Uso | Note |
|---|---|---|
| `s3` | AWS produzione | Backend cloud principale. |
| `minio` | On-prem serio / S3-compatible | Raccomandato per clienti che vogliono object storage open ma robusto. Riusa semantica S3. |
| `local` | Installazioni semplici / NAS mount | Nessun servizio remoto; path condiviso tipo `/mnt/plenora-artifacts`. |
| `webdav` | Nextcloud / ownCloud | Possibile per clienti che lo hanno gia'. Meno adatto a carichi pesanti e alta concorrenza. |
| `none` | Tutto DB/local app | Object storage disabilitato. |

Esempi on-prem:

```text
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=minio
OBJECT_STORAGE_BUCKET=plenora-runtime-artifacts
MINIO_ENDPOINT_URL=http://minio.internal:9000
```

```text
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=local
OBJECT_STORAGE_LOCAL_ROOT=/mnt/plenora-artifacts
```

```text
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=webdav
WEBDAV_BASE_URL=https://cloud.cliente.it/remote.php/dav/files/plenora
WEBDAV_USERNAME=...
WEBDAV_PASSWORD=...
```

Priorita' implementativa consigliata:

1. `s3` / `minio` tramite factory esistente S3-compatible.
2. `local` per installazioni senza object service.
3. `webdav` come backend futuro per Nextcloud/ownCloud, solo se richiesto da
   un cliente reale.

Questo supporta installazioni cliente on-prem o air-gapped:

```text
OBJECT_STORAGE_ENABLED=false
INTERMEDIATE_PERSISTENCE_MODE=preview_only
```

Se un cliente vuole davvero tutto su Postgres:

```text
OBJECT_STORAGE_ENABLED=false
INTERMEDIATE_PERSISTENCE_MODE=full_pg
```

`full_pg` resta una modalita' costosa/legacy/debug, non il default consigliato.

### Inventario callsite object storage attuali

Situazione attuale nel backend:

| Area | File principali | Come accede oggi | Note |
|---|---|---|---|
| Result store | `app/utils/result_store.py`, `app/db/results_db_service.py` | `get_object_storage()` | Salva/legge result grandi via `storage_key`. Oggi usa il bucket generico della factory. |
| File source upload/read/delete | `app/services/file_service.py`, `app/db/config/file_sources.py` | `get_object_storage()` | Upload sopra soglia, download su temp file, delete best-effort. |
| Eseguibili output | `app/core/eseguibili/storage.py`, `workers/tasks/enrichment_task.py`, `app/core/orchestrator/router.py` | `get_object_storage()` | Salva output grandi e li streamma al download. |
| IMDF bundle | `app/core/imdf/storage.py`, `app/core/imdf/router.py`, `workers/tasks/imdf_publish_task.py` | `get_object_storage()` | S3 usato come fonte persistente cross-node, cache locale come materializzazione. |
| Log archive | `app/services/log_archive.py`, `app/shared/cloud_backends.py` | `get_log_archive_storage()` | Gia' factory separata, bucket dedicato richiesto. |
| Admin backups | `app/core/admin_backups/service.py` | `boto3.client("s3")` diretto | Bypass dell'astrazione. Usa bucket backup e prefix `plenora_`. |
| Admin system status | `app/core/admin_system_status/service.py` | indiretto via `admin_backups.list_backups()` | Stato backup dipende dallo stesso path S3 admin backup. |

Gap attuale:

- `get_object_storage()` usa ancora `BACKUP_BUCKET` / `BACKUP_S3_BUCKET` come
  bucket generico. Questo mischia concettualmente backup, result, file source,
  IMDF ed eseguibili.
- `admin_backups` usa boto3 diretto, quindi non puo' rispettare automaticamente
  un futuro `OBJECT_STORAGE_ENABLED=false`.
- alcuni moduli trattano S3 come feature opzionale graceful (`imdf list_bundles`),
  altri come requisito della policy (`result_store` quando policy dice S3).

Target:

- introdurre un layer/factory comune con capability globale:

  ```python
  object_storage_enabled() -> bool
  get_runtime_object_storage() -> ObjectStorage
  get_backup_object_storage() -> ObjectStorage
  get_log_archive_storage() -> ObjectStorage
  ```

- separare i bucket:

  ```text
  OBJECT_STORAGE_BUCKET / PLENORA_ARTIFACT_BUCKET -> runtime artifact/result
  BACKUP_BUCKET                                  -> admin backup DB
  LOG_ARCHIVE_BUCKET                             -> archive log
  ```

- nessun nuovo codice business deve chiamare `boto3.client("s3")` direttamente;
- quando `OBJECT_STORAGE_ENABLED=false`, result/file_source/eseguibili/IMDF non
  devono tentare S3; backup S3 deve risultare feature disabilitata o errore
  operativo chiaro.

## Nuovo modello dati

### `runs`

Invariato nel primo batch.

Resta il posto per:

- stato run;
- contatori totali;
- `input_hash`;
- `output_hash`;
- idempotency;
- retention.

### `node_meta`

Estensione proposta:

```sql
ALTER TABLE node_meta ADD COLUMN node_hash TEXT;
ALTER TABLE node_meta ADD COLUMN hash_algo TEXT;
ALTER TABLE node_meta ADD COLUMN persistence_mode TEXT;
ALTER TABLE node_meta ADD COLUMN persisted_rows_count INTEGER;
ALTER TABLE node_meta ADD COLUMN preview_truncated BOOLEAN;
ALTER TABLE node_meta ADD COLUMN artifact_uri TEXT;
ALTER TABLE node_meta ADD COLUMN artifact_format TEXT;
ALTER TABLE node_meta ADD COLUMN artifact_status TEXT;
ALTER TABLE node_meta ADD COLUMN artifact_error TEXT;
```

Significato:

- `row_count`: righe reali del DataFrame.
- `persisted_rows_count`: righe effettivamente presenti in `node_rows`.
- `preview_truncated`: `true` se `persisted_rows_count < row_count`.
- `node_hash`: fingerprint del DataFrame completo.
- `hash_algo`: versione algoritmo, per migrazioni future.
- `persistence_mode`: mode attivo quando la run e' stata prodotta.
- `artifact_uri`: puntatore opzionale al full artifact.
- `artifact_status`: `pending`, `complete`, `failed`, `none`.

Compat:

- record legacy con colonne nuove `NULL` vengono trattati come `full_pg`.

### `node_rows`

Schema invariato.

Nel TO-BE non e' piu' il posto del full dataset di default. Diventa il posto
della preview:

- `metadata_only`: zero righe;
- `preview_only`: max N righe;
- `full_artifact`: max N righe;
- `full_pg`: tutte le righe, legacy/debug.

## Nuova API interna

### Moduli proposti

```text
app/core/intermediate/policy.py
app/core/intermediate/snapshot.py
app/core/intermediate/async_writer.py
app/core/intermediate/writer.py      # compat + API pubblica
app/core/intermediate/db.py          # SQL low-level
```

Responsabilita':

- `policy.py`: calcola la policy effettiva.
- `snapshot.py`: costruisce metadata, hash e preview senza `to_dict` full.
- `async_writer.py`: queue bounded + worker thread-safe.
- `writer.py`: espone API pubbliche stabili e mantiene compat wrapper.
- `db.py`: rimane il layer SQL/transaction low-level.

### API pubbliche

Gli executor devono usare solo queste:

```python
record_final_snapshot(
    run_id: str,
    module: str,             # "estrai" | "manipola" | "arricchisci"
    node_id: str,
    node_type: str,
    df,
    exec_time: float = 0.0,
    status: str = "ok",
    node_data: dict | None = None,
) -> SnapshotHandle

record_checkpoint_snapshot(
    run_id: str,
    module: str,
    node_id: str,
    node_type: str,
    df,
    exec_time: float = 0.0,
    status: str = "ok",
    node_data: dict | None = None,
) -> SnapshotHandle

flush_snapshots(run_id: str, timeout: float | None = None) -> FlushResult
```

Semantica:

- `record_final_snapshot`: per output finale del modulo.
- `record_checkpoint_snapshot`: per step intermedi.
- Entrambe leggono la policy centralizzata.
- Entrambe evitano `df.to_dict(full)` salvo `full_pg`.
- Se la policy non registra lo snapshot, ritornano
  `SnapshotHandle(recorded=False, reason=...)` senza materializzare il
  DataFrame.
- Metadata/hash/preview che servono a `close_run` devono essere scritti sync o
  flushati prima della chiusura della run.
- Solo il payload completo `full_artifact` puo' restare asincrono dopo
  `close_run`.
- `flush_snapshots`: chiamata a fine run per drenare metadata/preview entro
  timeout ragionevole; non deve aspettare upload artifact pesanti.

Compat:

```python
write_node(...)
```

resta per callsite legacy/list-of-dicts, ma i nuovi callsite DataFrame non
devono usarlo.

### Policy centralizzata

```python
resolve_intermediate_policy(
    module: str,
    snapshot_kind: str,      # "final" | "checkpoint"
    node_type: str,
    node_data: dict | None,
    transform_id: str | None,
) -> IntermediatePolicy
```

Output:

```python
IntermediatePolicy(
    should_record_meta=True,
    should_record_rows=True,
    should_record_artifact=False,
    persistence_mode="preview_only",
    preview_rows=500,
    async_mode=True,
    reason="manipola_critical_join",
)
```

Ordine di precedenza:

```text
node explicit override
  > project/canvas policy futura
  > module policy
  > global system policy
  > built-in default
```

Regole module-level:

- ESTRAI: `final_only`.
- ARRICCHISCI: `final_only`.
- MANIPOLA: checkpoint policy (`critical` default) + final sempre.

Questa struttura evita patch cerotto: quando cambiamo una policy, la cambiamo
in `policy.py`, non nei tre executor.

Oggi i caller fanno:

```python
write_node(
    rows=df.to_dict(orient="records"),
    col_schema=df.dtypes.astype(str).to_dict(),
)
```

Questo va sostituito nei path DataFrame con:

```python
write_dataframe_snapshot(
    run_id=run_id,
    node_id=node_id,
    node_type=node_type,
    df=df,
    exec_time=exec_time,
    status="ok",
)
```

La nuova funzione decide internamente:

1. legge la policy globale;
2. calcola `row_count` e `col_schema`;
3. calcola `node_hash` senza convertire tutto il DataFrame in lista di dict;
4. converte in dict solo `df.head(N)` se serve preview;
5. scrive `node_meta`;
6. scrive `node_rows` solo per preview/full_pg;
7. se `full_artifact`, accoda la persistenza completa fuori dal critical path.

`write_node(...)` resta come compat wrapper per callsite che hanno gia' una
lista di dict.

Per MANIPOLA serve anche una variante non bloccante:

```python
enqueue_dataframe_snapshot(
    run_id=run_id,
    node_id=node_id,
    node_type=node_type,
    df=df,
    exec_time=exec_time,
    status="ok",
)
```

Contratto:

- ritorna senza attendere la scrittura completa;
- prende una snapshot sicura del minimo necessario per la policy attiva;
- non passa un DataFrame mutabile a un thread se il caller puo' modificarlo;
- usa una queue con dimensione massima per evitare accumulo infinito RAM;
- se la queue e' piena, applica backpressure breve e poi degrada in modo
  esplicito: o scrittura sync della preview, o snapshot marcato `failed`.
  Non deve droppare silenziosamente metadata/hash;
- su errore marca il nodo come `artifact_status="failed"` o
  `persistence_error`, senza far fallire la trasformazione gia' completata;
- a fine run il task deve poter fare `flush(timeout=...)` solo per metadata
  critici, non per full artifact pesanti.

In MANIPOLA l'enqueue viene chiamato solo se il nodo supera la policy
checkpoint:

```python
if should_checkpoint_manipola_node(node, result_node, policy):
    enqueue_dataframe_snapshot(...)
```

Questo evita lavoro inutile prima ancora della parte async.

## Hash / change detection

Il requisito prodotto e': "capire se i dati tra due run sono cambiati".

Non serve salvare tutte le righe per questo. Serve un fingerprint persistente
del dataset completo.

### Algoritmo v1 proposto

Nuovo helper:

```python
compute_dataframe_fingerprint(df) -> str
```

Semantica:

- include schema colonne e dtypes;
- include row count;
- include contenuto di tutte le celle;
- evita `df.to_dict()` full;
- usa hashing vettoriale pandas per ridurre loop Python.

Implementazione candidata:

```python
ordered_cols = sorted(df.columns)
normalized = df[ordered_cols]
row_hashes = pd.util.hash_pandas_object(
    normalized,
    index=False,
    categorize=True,
).to_numpy(dtype="uint64", copy=False)
digest = sha256()
digest.update(schema_payload)
digest.update(row_count_payload)
digest.update(row_hashes.tobytes())
return digest.hexdigest()
```

Note:

- Per colonne geometry, convertire prima a WKT/WKB stabile.
- Per float speciali, assicurare normalizzazione coerente con il writer.
- L'algoritmo e' versionato: `hash_algo="pandas_hash_v1"`.

### Ordine righe

Decisione v1: hash order-sensitive.

Motivo: e' piu' veloce e piu' semplice. Se una query SQL senza `ORDER BY`
produce righe in ordine diverso, puo' dare falso positivo "changed". Questo e'
accettabile nel primo batch: e' meglio rilevare troppo che non rilevare.

Futuro: aggiungere `hash_order_insensitive_v2` ordinando i row hashes prima del
digest. Ha costo extra ma riduce falsi positivi.

## Full artifact

Mode `full_artifact`:

1. la pipeline non aspetta l'upload completo;
2. `node_meta.artifact_status="pending"`;
3. il caller serializza un artifact temporaneo locale oppure consegna al
   worker un riferimento gia' stabile;
4. non si passa mai un DataFrame grande nel messaggio async;
5. un job background salva il file in Parquet;
6. a successo aggiorna `artifact_uri`, `artifact_format`, `artifact_status`;
7. a fallimento aggiorna `artifact_status="failed"` e `artifact_error`.

Formato default: Parquet.

Motivi:

- piu' piccolo del CSV;
- conserva tipi meglio;
- ricarica piu' veloce;
- adatto a DataFrame grandi.

Per GeoDataFrame: valutare GeoParquet se dipendenze presenti; altrimenti
Parquet con geometria WKB/WKT e metadata CRS.

## Struttura object store / S3

Il codice deve parlare con un artifact store astratto, ma la struttura S3 va
standardizzata subito. Non deve essere decisa dai singoli executor.

Prefix base configurabile:

```text
INTERMEDIATE_ARTIFACT_PREFIX=intermediate
```

Bucket runtime dedicato deciso per produzione:

```text
PLENORA_ARTIFACT_BUCKET=plenora-runtime-artifacts-112011134642-eu-south-1
```

Non usare i bucket esistenti:

- `plenora-terraform-state-112011134642`: riservato allo state Terraform.
- `plenora-backup-112011134642-eu-south-1-an`: riservato ai backup
  amministrativi.

Motivo: artifact runtime/intermedi, backup e Terraform state hanno retention,
permessi e semantiche diverse. Tenerli separati evita lifecycle policy
pericolose e pulizia accidentale.

Layout proposto:

```text
s3://<bucket>/<prefix>/
  group=<group_id>/
    project=<project_id>/
      run=<run_id>/
        module=<estrai|manipola|arricchisci>/
          node=<node_id>/
            kind=<final|checkpoint>/
              data.<format>
              manifest.json
```

Esempio:

```text
s3://plenora-artifacts/intermediate/
  group=default/
    project=test_imdf_20260413_034050_bee2e47a/
      run=run_20260428_0412_abcd/
        module=manipola/
          node=join_catasto/
            kind=checkpoint/
              data.parquet
              manifest.json
```

`node_meta.artifact_uri` punta al file dati principale:

```text
s3://.../data.parquet
```

`manifest.json` e' opzionale nel primo batch `preview_only`, ma obbligatorio
quando `full_artifact` viene attivato. Contenuto minimo:

```json
{
  "artifact_version": 1,
  "run_id": "run_...",
  "project_id": "test_...",
  "group_id": "default",
  "module": "manipola",
  "node_id": "join_catasto",
  "node_type": "join",
  "snapshot_kind": "checkpoint",
  "format": "parquet",
  "row_count": 2370000,
  "column_count": 42,
  "node_hash": "sha256...",
  "hash_algo": "pandas_hash_v1",
  "created_at": "2026-04-28T02:00:00Z"
}
```

Regole:

- path deterministico per run/node: una run non sovrascrive mai artifact di
  un'altra run;
- niente nomi file derivati da label utente non sanitizzate;
- `group_id` e `project_id` nel path aiutano retention e audit, ma
  l'autorizzazione resta sempre in Postgres/API, non nel path S3;
- cancellare una run deve cancellare anche il prefix
  `.../project=<project_id>/run=<run_id>/`;
- `artifact_uri` in Postgres e' la fonte operativa per leggere il dato;
- `manifest.json` serve per debug/ops e per future migrazioni, non sostituisce
  `node_meta`.

Per sviluppo locale lo stesso layout viene replicato sotto una directory:

```text
<local_artifact_root>/intermediate/group=.../project=.../run=.../...
```

In questo modo il sistema non e' legato mani e piedi a S3: cambia il backend
artifact, non la semantica dei path e dei manifest.

## Async persistence

La persistenza full non deve stare nel critical path.

Opzione consigliata:

- Celery task dedicato `intermediate.persist_artifact`;
- payload minimo: `run_id`, `node_id`, `artifact_temp_path` oppure riferimento
  a file locale temporaneo;
- non passare DataFrame gigante nel messaggio Celery;
- il task aggiorna `node_meta`.

Per primo batch si puo' rimandare `full_artifact` e implementare solo
`metadata_only/preview_only`. Questo taglia subito il collo di bottiglia.

Decisione aggiornata dopo validazione runtime 2026-04-28:

- `preview_only` su Postgres non deve essere il default: anche 500 righe per
  checkpoint hanno prodotto `db_ms` troppo alto nel run reale.
- I dati intermedi, quando l'utente sceglie di conservarli, vanno salvati come
  artifact su object storage.
- Postgres resta catalogo leggero: metadata, schema, row count, hash,
  `artifact_uri`, status.
- `node_rows` resta per compatibilita' legacy/debug (`full_pg`) e per eventuali
  preview temporanee esplicite, non per il path normale.

## Contratto close_run

Questo e' il punto da non sbagliare.

Oggi `close_run` legge `node_meta` per calcolare summary della run. Se gli
snapshot metadata/preview restano in una queue async e `close_run` parte prima
che siano scritti, la run viene chiusa con contatori/hash incompleti.

Regola TO-BE:

```text
execute pipeline
  -> record_*_snapshot(...)
  -> flush_snapshots(run_id, kinds=["metadata", "preview"], timeout=...)
  -> close_run(...)
  -> eventuali artifact full continuano async e aggiornano solo artifact_status
```

Quindi:

- metadata/hash/preview dei nodi registrati sono consistenti prima di
  `close_run`;
- full artifact puo' continuare dopo, perche' non serve a calcolare il summary
  della run;
- se il flush metadata fallisce, `close_run` puo' comunque chiudere la run ma
  deve registrare `persistence_error` esplicito, non produrre summary
  apparentemente completo.

## Compatibilita' legacy

`write_node(...)` resta disponibile per i callsite legacy che hanno gia' una
lista di dict. Nel primo batch mantiene semantica `full_pg` legacy, per evitare
regressioni nascoste.

I nuovi callsite DataFrame non devono usarlo. Devono passare da
`record_final_snapshot` / `record_checkpoint_snapshot`, cosi' la policy resta
centrale.

Record storici con nuove colonne `NULL` vengono interpretati come legacy
`full_pg` quando si leggono API e diff.

## Validazione settings

Le nuove variabili devono essere validate fail-fast all'avvio:

- mode non riconosciuta -> errore di configurazione;
- preview rows <= 0 -> errore di configurazione;
- checkpoint mode MANIPOLA non riconosciuta -> errore di configurazione;
- richiesta frontend di `full_artifact` quando il sistema non lo permette ->
  errore operativo sul save/run, non fallback silenzioso.

Il frontend vince sulla policy solo quando richiede un mode ammesso dal sistema.
Il backend valida vincoli e permessi, ma non sostituisce la scelta del nodo con
un'altra scelta "piu comoda".

## Read API TO-BE

### `GET /api/runs/{run_id}/nodes/{node_id}/data`

Risposta estesa:

```json
{
  "run_id": "...",
  "node_id": "...",
  "row_count": 2370000,
  "persisted_rows_count": 500,
  "persistence_mode": "preview_only",
  "preview_truncated": true,
  "col_schema": {},
  "rows": [],
  "truncated": true
}
```

Compat:

- `rows` resta presente.
- `truncated` resta presente.
- i nuovi campi sono additivi.

### `GET /api/runs/compare/l1`

Resta veloce e diventa piu' utile se aggiungiamo anche compare per
`node_meta.node_hash` in un endpoint futuro.

### `GET /api/runs/compare/l2`

Semantica nuova:

- `full_pg`: diff completo legacy.
- `preview_only`: diff parziale sulle righe preview, con warning nel payload.
- `metadata_only`: 409 o 422 operativo: "diff riga-per-riga non disponibile
  per questa run; usa full_artifact/full_pg".
- `full_artifact`: diff completo possibile solo se artifact `complete`.

Non deve restituire un diff parziale fingendo che sia completo.

## UI / UX

Admin/system setting:

- prima fase: env/config, non UX;
- poi pagina Admin Settings se serve.

Nella UI run/node:

- badge: `metadata only`, `preview`, `full artifact`, `full PG`;
- se preview: "Mostro 500 righe su 2.370.000";
- se diff L2 non disponibile: messaggio operativo, non errore generico;
- se artifact pending: stato "salvataggio intermedio completo in corso".

## Piano implementativo

### Batch 1 - Schema + config + writer nuovo

File probabili:

- `backend/app/config.py`
- `backend/app/db/intermediate_schema.sql`
- nuova migration SQL
- `backend/app/core/intermediate/policy.py`
- `backend/app/core/intermediate/snapshot.py`
- `backend/app/core/intermediate/db.py`
- `backend/app/core/intermediate/writer.py`
- test su writer/db

Obiettivo:

- aggiungere settings;
- aggiungere colonne `node_meta`;
- introdurre API uniforme `record_final_snapshot` /
  `record_checkpoint_snapshot`;
- introdurre `resolve_intermediate_policy`;
- implementare `metadata_only` e `preview_only`;
- lasciare `write_node` compatibile.

Test:

- `metadata_only`: zero `node_rows`, `node_meta.row_count` reale, `node_hash`
  presente.
- `preview_only`: max N righe in `node_rows`, `row_count` reale,
  `persisted_rows_count=N`, `preview_truncated=true`.
- `full_pg`: comportamento legacy.
- hash uguale fra `metadata_only` e `preview_only` sullo stesso DataFrame.
- checkpoint non registrato: `record_checkpoint_snapshot` ritorna
  `recorded=False` senza chiamare `df.to_dict`, senza calcolare hash e senza
  scrivere DB.
- settings invalidi: startup/config validation fallisce in modo esplicito.

### Batch 2 - MANIPOLA hot path

File:

- `backend/app/core/manipola/executor.py`
- `backend/app/core/intermediate/async_writer.py` (nuovo, se serve)
- `backend/app/core/intermediate/policy.py`

Obiettivo:

- sostituire i callsite `current_df.to_dict(...) + write_node` con
  `record_checkpoint_snapshot(df=current_df, module="manipola", ...)`;
- far decidere checkpoint selezionati (`critical` default) al layer policy,
  non all'executor;
- eliminare full `to_dict` dal critical path;
- result node usa `record_final_snapshot`.
- garantire thread-safety: niente DataFrame condiviso mutato dal thread di
  persistenza, queue bounded, connessioni DB aperte dentro il worker thread.

Test:

- canvas manipola con DataFrame grande mocked: verifica che non chiama
  `to_dict` full;
- verifica che `enqueue_dataframe_snapshot` ritorna subito e accoda lavoro;
- verifica che il worker usa una copia/snapshot limitata, non il DataFrame
  mutabile originale;
- verifica che `flush_snapshots(...metadata/preview...)` viene chiamato prima
  di `close_run` e che `close_run` legge metadata gia' persistiti;
- verifica che queue piena non droppa metadata/hash silenziosamente;
- verifica policy `critical`: `multi_join` salvato, transform semplice non
  salvato, result salvato;
- verifica policy `all`: tutti i transform salvati;
- verifica policy `none`: nessun nodo intermedio salvato, result/metadata run
  preservati secondo decisione;
- pipeline continua se persistence fallisce best-effort.

### Batch 3 - ESTRAI / ARRICCHISCI uniform

File:

- `backend/app/db/query_executor.py`
- `backend/workers/tasks/enrichment_task.py`
- `backend/app/core/arricchisci/api_connection_support.py`

Obiettivo:

- uniformare la policy;
- rimuovere caps ad hoc duplicati dove possibile;
- ESTRAI: solo result finale, niente checkpoint intermedi aggiuntivi;
- ARRICCHISCI: solo result finale, rimuovere/spegnere snapshot per-nodo
  `api_enrich` di default;
- entrambi chiamano `record_final_snapshot`, non `write_node`;
- evitare `df.to_dict` full prima del thread in ARRICCHISCI per snapshot che
  non devono piu' essere prodotti.

Test:

- ESTRAI sopra cap produce metadata/hash e preview coerente;
- ARRICCHISCI `final_only`: non salva snapshot per ogni `api_enrich`;
- ARRICCHISCI salva solo metadata/hash/preview del result finale.
- ARRICCHISCI non materializza `_df.to_dict(full)` per nodi `api_enrich`
  quando la policy e' `final_only`.

### Batch 4 - Artifact intermedi su object storage

File:

- `backend/app/core/intermediate/snapshot.py`
- `backend/app/core/intermediate/artifact_store.py` (nuovo)
- `backend/app/core/intermediate/policy.py`
- `backend/app/core/intermediate/db.py`

Obiettivo:

- cambiare il default produzione a `metadata_only`;
- implementare `full_artifact` per nodi esplicitamente salvati;
- salvare dati completi intermedi su runtime object storage, non in
  `node_rows`;
- scrivere `data.parquet` + `manifest.json`;
- aggiornare `node_meta.artifact_uri`, `artifact_format`,
  `artifact_status`;
- se `OBJECT_STORAGE_ENABLED=false`, `full_artifact` ritorna errore operativo
  chiaro o viene rifiutato dalla policy;
- nessuna duplicazione default su Postgres.

Test:

- `metadata_only`: zero `node_rows`, zero artifact, metadata/hash presenti.
- `full_artifact`: zero `node_rows`, artifact Parquet + manifest scritti,
  `node_meta.artifact_uri` valorizzato.
- object storage disabled + richiesta `full_artifact`: errore operativo.
- artifact path rispetta layout
  `intermediate/group=.../project=.../run=.../module=.../node=.../kind=...`.
- nessun `df.to_dict(full)` nel path artifact.

### Batch 5 - API read/diff su metadata e artifact

File:

- `backend/app/core/intermediate/router.py`

Obiettivo:

- esporre nuovi campi metadata;
- `GET /runs/.../nodes/.../data`:
  - se `node_rows` legacy esistono, leggerli e sanitizzarli;
  - se `artifact_uri` esiste, leggere preview dall'artifact;
  - se solo `metadata_only`, rispondere con metadata e messaggio operativo
    "dati intermedi non salvati";
- rendere L2 esplicito su diff parziale/non disponibile;
- fixare il caso `NaN`/`inf` -> `null` nelle response JSON.

Test:

- `get_node_data` ritorna `persistence_mode`, `artifact_status`,
  `artifact_uri`, `persisted_rows_count`.
- `get_node_data` non va in 500 con `NaN`/`inf`.
- `compare/l2` in `metadata_only` ritorna errore operativo.
- `compare/l2` in `full_pg` resta legacy completo.
- `compare/l2` in `full_artifact` dichiara supporto solo se artifact
  `complete` (implementazione diff artifact puo' restare follow-up).

### Batch 6 - Async artifact + retention

Obiettivo:

- spostare upload artifact fuori dal critical path se necessario;
- queue/worker async per artifact completi;
- `flush_snapshots` non aspetta artifact pesanti, solo metadata critici;
- cleanup retention anche su artifact;
- metriche `[PERF]` artifact upload.

## Rollback

Rollback semplice per batch 1-4:

```text
INTERMEDIATE_PERSISTENCE_MODE=full_pg
```

Questo ripristina il comportamento legacy senza revert immediato del codice.

Le nuove colonne sono additive e nullable.

## Decisioni aperte

1. Default produzione: `metadata_only` o `preview_only`?

   Decisione aggiornata: `metadata_only`.

   Motivo: il run reale 2026-04-28 ha mostrato che anche preview-only con 500
   righe per checkpoint puo' dominare il runtime. La UX debug dati deve passare
   da artifact espliciti su object storage, non da `node_rows` di default.

2. Preview rows: 500 o 1000?

   Non e' piu' un default produzione. Per modalita' debug `preview_only`,
   raccomandazione: 100 o 500 in base al caso. In produzione normale:
   `metadata_only`.

3. Hash order-sensitive o order-insensitive?

   Raccomandazione: order-sensitive v1, piu' veloce. Documentare possibile
   falso positivo se manca `ORDER BY`.

4. Full artifact subito o dopo?

   Decisione aggiornata: anticipare `full_artifact` rispetto alla vecchia
   roadmap API/diff. Se l'utente vuole vedere/conservare intermedi completi, il
   dato deve stare su object storage; Postgres non deve duplicare preview
   row-per-row come default.

5. Per-progetto/per-nodo override?

   Raccomandazione: non nel primo batch. Prima setting globale. Il design
   pero' deve lasciare spazio all'evoluzione futura: ogni nodo potra'
   dichiarare `save_intermediate` / `intermediate_mode`. La scelta esplicita
   del nodo vince sulla policy globale/progetto; il backend valida solo mode
   ammessi e permessi per mode costosi. Vedi `manipola_checkpoint_policy.md`.

## Raccomandazione finale

Implementare subito Batch 1 + Batch 2.

Sono i due batch che eliminano il blocco reale osservato:

```text
ManipolaExecutor._applica_trasformazioni
  -> current_df.to_dict(full)
  -> write_node
  -> write_node_atomic full JSONB
```

Il nuovo path deve diventare:

```text
ManipolaExecutor._applica_trasformazioni
  -> write_dataframe_snapshot(df)
       -> compute dataframe fingerprint
       -> persist node_meta
       -> persist first N rows only
```

Questo mantiene audit e change detection, ma toglie milioni di INSERT JSONB
dal critical path.

Per MANIPOLA, il path finale desiderato e' ancora piu' selettivo:

```text
for node in sorted_nodes:
    current_df = execute_transform(...)
    if should_checkpoint_manipola_node(node):
        enqueue_dataframe_snapshot(current_df)
```

Default: checkpoint solo su nodi critici e result. Questo evita sia il costo
del full snapshot sia il rumore diagnostico di decine di passaggi tecnici.
