# Refactor I/O e Storage RAM-first per Plenora Backend

## Contratto runtime RAM-first (standing reference)

**Stato: Closed (2026-05-26).** Il refactor RAM-first ha chiuso i pezzi
runtime principali. Quello che segue NON e' piu' un piano di migrazione
ma il **contratto** che il codice applicativo deve rispettare. Le sezioni
analytical successive ("Obiettivo", "Architettura proposta", "Piano di
migrazione", ecc.) restano come reference storico del percorso e delle
decisioni prese; non vanno consultate per "cosa fare oggi".

### Sintesi stato chiuso

| Workflow | Stato runtime | Riferimento commit |
|---|---|---|
| ETL pipeline (estrai/manipola/arricchisci/carica) | Zero disco | pre-refactor + Phase4 |
| REST connector (input/output) | Bytes / `result_id` end-to-end | `ece07689` |
| Scheduler download/archive | DB-only via `schedule_logs` | `330c21f4` |
| ArcGIS routes (input) | Dataframe-first | `8728d6fa` |
| ResultBlobLoader | `mode="bytes"` only (path mode tombstoned) | `598f2be5` |
| IMDF ingest (bytes-first) | Zero disco transient | IMDF-1..6 |
| IMDF persist | Storage canonical (no local cache) | `58859082` |
| IMDF GET metadata + /bundles list | Storage-first | `b32f73f0` / `7def5e40` |
| IMDF publish/validate | Fully-RAM via GDAL `/vsimem` ctypes | `012072f6` |
| IMDF extract_imdf_zip_bytes | Fully-RAM (no tmp dir) | `43ef5dde` |
| Query streaming output | S3 multipart end-to-end (no tempfile) | `b720c958` |

### Regola 1 — codice applicativo non scrive su filesystem

Il codice in `app/core/*` (escluse le directory boundary dichiarate sotto)
e in `workers/tasks/*` **NON** deve usare:

- `tempfile.NamedTemporaryFile` / `mkdtemp` / `mkstemp` / `TemporaryDirectory`
- `pathlib.Path.write_bytes` / `write_text`
- `open(..., "w"|"a"|"x")` per scrivere
- `zipfile.ZipFile.extractall` / `safe_extractall`
- `shutil.move` / `shutil.copy*`
- `FileResponse(...)` (era usata per scheduler legacy)
- `.to_csv(path)` / `.to_excel(path)` / `.to_parquet(path)` / `.to_json(path)` / `.to_file(path)`
- `storage.upload(path, key)` / `storage.download(key, path)`

**Enforcement:** `backend/tests2/io_boundary/test_no_direct_filesystem_contract.py::test_no_active_legacy_callsites_remain`.
Il test fallisce su qualunque nuovo callsite fuori dal boundary set.

### Regola 2 — boundary autorizzati (motivazione per file)

Le seguenti location HANNO il permesso di toccare il filesystem perche'
sono primitive che esistono PER esporre quella capability al resto del
codice. La motivazione e' inline nel `scripts/_gen_io_allowlist.py`
(`_BOUNDARY_DIRS` + `_BOUNDARY_FILES`).

**Boundary primitives (intentional disk capability):**
- `app/core/io/` — materializer + helper geospaziali (escape hatch tracciato).
- `app/core/storage/` — storage backend abstraction.
- `app/core/results/temp_paths.py` — tempdir centralizzato per path-only adapters.
- `app/utils/zip_safe.py` — zip-bomb-safe extract impl.
- `app/core/files/service_deps.py` — `DefaultFileSystem.extract_zip` adapter.
- `app/core/resources/loaders/result_blob.py` — Resource Engine loader (post-Phase5 solo `mode="bytes"`).

**IMDF subsystem (format-as-directory + cache hydration):**
- `app/core/imdf/` — interamente nel boundary. Razionale: OGC IMDF e'
  format-as-directory (`.gdb` letto da pyogrio via filesystem).
  Post-IMDF-5 il path runtime publish/validate e' `/vsimem` via
  `gdal_vsi.py` (ctypes); restano callsite per:
  - `extract_imdf_zip(path)` legacy (test/script);
  - `persist_imdf_bundle_to_disk` legacy (test/script);
  - `ensure_local_bundle` cache locale (DELETE endpoint).

**Scripts (one-off tooling):**
- `scripts/` esplicitamente fuori dal sweep applicativo
  (`_EXCLUDED_ROOTS = ("scripts",)`). Backup/restore/openapi-export/imdf-e2e-check
  sono tool, non runtime.

### Regola 3 — ratchet bidirezionale del count

Il count per-file dei residui inside-boundary e' congelato in
`backend/tests2/io_boundary/test_no_direct_filesystem_contract.py::_BOUNDARY_FROZEN_SNAPSHOT`.
Stato corrente: **23 entries su 12 file**.

Il test `test_report_inside_boundary_frozen` fallisce se:
- **Cresce**: un file aggiunge callsite (accidentale o deliberato).
- **Cala**: un file rimuove tutte le entries (legitimate cleanup ma da
  documentare).
- **Nuovo file**: appare nel boundary set senza essere dichiarato.

Modifiche legittime al count richiedono **aggiornamento contestuale**
del doc (questa sezione) + dello snapshot in `_BOUNDARY_FROZEN_SNAPSHOT`,
nello stesso commit.

### Regola 4 — legacy tombstone schedule

I seguenti residui sono dichiarati tombstone-pending. Possono essere
rimossi quando i loro consumer migrano:

| Item | Consumer attuale | Tombstone quando |
|---|---|---|
| `imdf.loader.extract_imdf_zip(path)` | `scripts/imdf_real_e2e_check.py`, `tests2/.../test_imdf_persist_bundle_contract.py` | Script + test migrano a `extract_imdf_zip_bytes(bytes)`. |
| `imdf.persist.persist_imdf_bundle_to_disk` | `scripts/imdf_real_e2e_check.py`, `tests2/.../test_imdf_persist_bundle_contract.py`, `tests2/.../test_plenora_metadata_schema.py`, `tests2/.../test_site_id_resolution_single_source.py` | Script + test migrano a `persist_imdf_bundle` (storage-direct). |
| `imdf.storage.ensure_local_bundle` | Solo `DELETE /imdf/bundles/{id}` per cleanup cache locale eventuale | DELETE migra a `storage.delete_bundle` puro. |

Quando rimossi: aggiornare snapshot + questa tabella. Break compat per
operatori che dipendono dallo script `imdf_real_e2e_check.py` e' atteso
(re-script via il nuovo entry point).

### Regola 5 — bundle legacy (break compat IMDF-4 step 4)

Bundle IMDF persistiti **prima** di IMDF-3 (`58859082`, layout zip
monolitico via `save_bundle(local_dir, ...)`) non sono piu' accessibili
dal runtime corrente. Il fallback `_zip_key` in `ensure_local_bundle` e'
stato tombstoned in `f494cd38`. Operator action richiesta: re-upload
via `persist_imdf_bundle` (IMDF-3 per-file layout) per ripristinare
accesso.

### Quando NON e' necessario seguire le regole

- Test (`tests2/`, `backend/test-e2e-v2/`): possono creare tempfile per
  fixture.
- Migration files (`backend/migrations/`): script SQL ad-hoc.
- Dev tooling (`.agent-temp/`, `.claude/`, doc).

### Storia analitica

Le sezioni seguenti ("Obiettivo", "Architettura proposta", ecc.) restano
come reference storico del processo decisionale: probe OSGeo/GDAL,
valutazione driver, scelte di shape `ImdfBundle`, reversal della
decoupling persist/storage, ecc. Sono utili per capire **perche'** le
regole sopra hanno la forma che hanno.

---

## Obiettivo

Ridurre drasticamente, e dove possibile eliminare, le scritture su filesystem locale nel backend.

L'obiettivo non e' solo sostituire alcuni `tempfile` con `BytesIO`, ma introdurre un boundary architetturale unico per tutta la gestione di:

- lettura file
- scrittura file
- upload
- export
- ZIP
- formati geospaziali
- artifact/result
- persistenza su DB
- persistenza su S3/MinIO
- fallback temporaneo su path fisico quando inevitabile

Il principio guida e':

```text
Il codice business non deve leggere/scrivere direttamente dal filesystem.
Tutto passa da un layer I/O + Storage centralizzato.
```

Questo rende controllabile la policy:

```text
RAM-only dove possibile
Object storage o DB per persistenza
Filesystem locale solo come fallback esplicito, temporaneo e tracciato
Filesystem persistente locale vietato in produzione, salvo opt-in dev
```

## Perche' serve una struttura orizzontale

Nel backend attuale le scritture su disco sono distribuite in molti punti:

- upload e preview file
- estrazione ZIP
- materializzazione file source
- lettura/scrittura Shapefile/GPKG/GDB
- result reader
- pipeline result
- scheduler artifact
- IMDF bundle
- export CSV/XLSX
- backup/restore
- datasource local

Se ogni modulo mantiene la propria gestione filesystem, il requisito "no persistent disk writes" resta fragile.

Esempio del problema attuale:

```text
upload.py usa tempfile
imdf/loader.py usa extractall
result_reader.py materializza path temporanei
query_executor.py esporta su final_path
scheduler/artifact_store.py copia file
storage cloud lavora con local_path
```

Ogni modulo implementa una mini-policy diversa.

Con un layer orizzontale, invece, il backend ha un unico confine da controllare:

```text
app/core/io/
app/core/storage/
```

Questo confine diventa un "firewall I/O":

- blocca scritture persistenti non autorizzate
- espone API RAM-first
- consente temp fisici solo con motivo esplicito
- logga ogni materializzazione su disco
- applica limiti dimensione
- centralizza cleanup
- permette test read-only del container
- impedisce regressioni future

## Risultato dei test OSGeo/GDAL

E' stato creato un container di test separato:

```text
image locale: plenora-test-osgeo-gdal:latest
container: plenora-test-osgeo-gdal
base image: ghcr.io/osgeo/gdal:ubuntu-full-3.11.4
GDAL/osgeo: 3.11.4
Python: 3.12.3
```

Probe:

```text
.agent-temp/osgeo_gdal_full_memory_probe.py
```

Risultato:

```text
summary: 19/19 passed
```

Il probe e' passato anche con:

```text
docker run --read-only --network none ...
```

Questo dimostra che i flussi geospaziali testati non richiedono scritture persistenti su disco.

### Casi geospaziali coperti in RAM

- GeoJSON write/read su `/vsimem`
- GeoJSON bytes roundtrip
- GPKG write/read su `/vsimem`
- GPKG bytes roundtrip
- FlatGeobuf write/read su `/vsimem`
- FlatGeobuf bytes roundtrip
- Shapefile multi-file in `/vsimem`
- lettura sidecar `.shp/.shx/.dbf/.prj` da `/vsimem`
- creazione ZIP Shapefile in RAM con `BytesIO`
- lettura ZIP Shapefile via `/vsizip//vsimem`
- OpenFileGDB directory `.gdb` in `/vsimem`
- creazione ZIP OpenFileGDB in RAM
- lettura ZIP OpenFileGDB via `/vsizip//vsimem`
- ZIP input caricato come bytes in `/vsimem`
- lettura ZIP input via `/vsizip//vsimem`

### Limite GDAL confermato

Non bisogna scrivere direttamente dentro:

```text
/vsizip//vsimem/file.zip/...
```

Per Shapefile fallisce con:

```text
Read-write random access not supported for /vsizip
```

Workaround corretto, tutto in RAM:

```text
1. Scrivere Shapefile in /vsimem come sidecar
2. Leggere i sidecar con VSIFReadL
3. Creare lo ZIP in RAM con Python BytesIO
4. Se serve rileggerlo, caricare lo ZIP in /vsimem e usare /vsizip
```

Quindi la pipeline valida e':

```text
format write -> /vsimem
archive build -> BytesIO
archive read -> /vsizip//vsimem
```

## Nota su pyogrio e osgeo

Nel backend attuale `pyogrio` usa GDAL vendorizzato:

```text
pyogrio GDAL 3.11.4
```

Il pacchetto Debian `python3-gdal` usa invece:

```text
osgeo GDAL 3.6.2
```

Test importante:

```text
pyogrio scrive in /vsimem -> osgeo non vede i file
osgeo scrive in /vsimem -> pyogrio non vede i file
```

Conclusione:

```text
Non bisogna mischiare pyogrio e osgeo nello stesso flusso /vsimem
se usano due librerie GDAL diverse.
```

Per i flussi geospaziali RAM-only conviene scegliere uno stack coerente:

- soluzione preferita per refactor: OSGeo/GDAL come stack unico
- alternativa: usare pyogrio solo per formati single-file supportati da BytesIO
- per Shapefile/OpenFileGDB multi-file: OSGeo/GDAL e' il candidato piu' completo

## Architettura proposta

## Integrazioni esistenti da riusare

Il backend non parte da zero. Esistono gia' alcune astrazioni che vanno riusate e consolidate, non duplicate.

### Resource Engine esistente

Moduli:

```text
backend/app/core/resources/registry.py
backend/app/core/resources/types.py
backend/app/core/resources/loaders/result_blob.py
backend/app/core/resources/loaders/file_source_blob.py
backend/app/core/resources/file_source_blob_access.py
```

Contratto attuale:

```python
default_registry().get(ref, mode="bytes" | "path" | "stream")
```

Oggi sono registrati:

```text
result_blob
file_source_blob
```

Il contratto e' volutamente byte-level:

```text
Resource.data
Resource.path
Resource.stream
```

e non deve diventare un god-engine che ritorna DataFrame, GeoDataFrame o oggetti di dominio.

Quindi il nuovo layer I/O deve agganciarsi cosi':

```text
Resource Engine
  -> recupera bytes/stream/path temporaneo

I/O readers
  -> interpretano bytes/stream come CSV/XLSX/ZIP/GPKG/SHP/GDB

Domain service
  -> lavora su DataFrame/result/metadata
```

Non va fatto:

```text
Resource Engine -> parse DataFrame
```

Va fatto:

```python
resource = default_registry().get(ref, mode="bytes")
df = io_readers.read_dataframe_bytes(
    resource.data,
    format_hint=resource.format_hint,
)
```

Estensioni consigliate al Resource Engine:

```text
ResourceKind:
  - intermediate_artifact
  - imdf_bundle
  - eseguibili_output
  - scheduler_run_artifact

OutputMode:
  - bytes
  - stream
  - path fallback, ma solo via materialize policy
```

Nota importante:

`mode="path"` esiste gia' come fallback nei loader. Va mantenuto per compatibilita', ma deve essere trattato come deprecated per nuovi call-site RAM-first.

### Object storage esistente

Modulo:

```text
backend/app/infrastructure/storage/cloud_backends.py
```

Il protocollo `ObjectStorage` ha gia':

```python
upload(local_path, key)
upload_bytes(data, key)
download(key, local_path)
get_object_stream(key)
delete(key)
```

Quindi la parte storage non va riscritta da zero. Va completata e normalizzata.

Da promuovere come API primaria:

```python
upload_bytes(data, key)
get_object_stream(key)
```

Da deprecare nei nuovi flussi:

```python
upload(local_path, key)
download(key, local_path)
```

Gap da aggiungere:

```python
upload_stream(stream, key, size=None, content_type=None)
download_bytes(key, max_bytes=None)
```

Oggi `get_object_stream` esiste, ma molti loader lo consumano subito in bytes con bound. Serve distinguere:

```text
lettura piccola -> bytes bounded
lettura grande/download HTTP -> streaming diretto
lettura parser RAM-only -> bytes con cap dichiarato
```

### Result storage esistente

Modulo:

```text
backend/app/core/results/blob_writer.py
```

Esistono gia':

```python
save_result_blob(path, ...)
save_result_blob_from_bytes(data, filename, ...)
```

Il refactor deve invertire la preferenza:

```text
Nuovi caller -> save_result_blob_from_bytes
Caller legacy -> save_result_blob(path)
```

`save_result_blob(path)` deve diventare adapter legacy o passare da `io.materialize`.

### File source storage esistente

Modulo:

```text
backend/app/core/files/storage_dispatch.py
```

Esiste gia':

```python
dispatch_blob_storage(file_id, raw_data, filename)
```

che salva su DB BYTEA o object storage in base a policy.

Questo e' gia' RAM-first lato persistenza. Il problema non e' tanto il save finale, ma il parsing preliminare:

```text
upload.py materializza tmp file
multi_layer_upload.py materializza tmp file
source_blob.py estrae ZIP su tmp_dir
source_loading.py materializza cache stabile su filesystem
preview_temp.py materializza preview
```

Quindi la migrazione file-source deve concentrarsi su:

```text
read/preview/inferenza schema senza path
```

non sulla persistenza blob, che e' gia' parzialmente corretta.

### DataFrame I/O esistente

Modulo:

```text
backend/app/core/dataframes/frame_io.py
backend/app/core/dataframes/geospatial_io.py
```

Oggi ci sono gia' bytes path per tabellare:

```python
save_dataframe_to_bytes(...)
tabular_dataframe_to_bytes(...)
_load_dataframe_from_bytes(...)
```

Ma ci sono ancora formati marcati path-bound:

```python
_PATH_BOUND_ARTIFACT_FORMATS = {"gpkg", "geopackage", "shp", "zip", "gdb"}
```

e in `geospatial_io.py`:

```python
geospatial_dataframe_to_bytes(...)
```

oggi rifiuta:

```text
gpkg
shp
```

dicendo che richiedono filesystem.

Dopo il probe OSGeo/GDAL, questa assunzione va aggiornata:

```text
gpkg -> /vsimem bytes
shp -> /vsimem sidecar + ZIP BytesIO
zip -> /vsizip//vsimem
gdb -> /vsimem .gdb + ZIP BytesIO
```

Quindi `frame_io.py` e `geospatial_io.py` sono fra i primi moduli da migrare.

### ZIP safety esistente

Modulo:

```text
backend/app/utils/zip_safe.py
```

Oggi contiene la validazione zip slip / zip bomb, ma l'API principale e' orientata a:

```python
safe_extractall(zf, dest_dir)
safe_extract_zip_path(zip_path, dest_dir)
```

Nel refactor non va buttata. Va separata in due livelli:

```text
zip_safe validation
  -> valida members, size, path traversal

io.archives
  -> legge entries in RAM senza extractall
```

Regola:

```text
extractall resta solo dentro materialize fallback.
```

### Dependency injection filesystem esistente

Modulo:

```text
backend/app/core/files/service_deps.py
```

Esiste `FileSystemProtocol` con metodi:

```text
mkdir, write_text, write_binary, extract_zip, rmtree, unlink, ...
```

Questo protocollo e' utile per test, ma oggi normalizza l'idea che il file service possa fare filesystem.

Nel refactor:

```text
FileSystemProtocol deve diventare adapter legacy
```

Nuovi service dovrebbero dipendere da:

```text
PayloadReader
ArchiveReader
GeospatialReader
StorageWriter
Materializer
```

non da un filesystem generico.

### Startup validation

Modulo:

```text
backend/app/startup_validation.py
```

Oggi verifica che `DATA_DIR` sia scrivibile.

Con policy RAM/object-storage-first, questa regola va resa condizionale:

```text
se PLENORA_IO_DISK_POLICY=memory_only:
  DATA_DIR non deve essere requisito per i flussi runtime

se serve temp fallback:
  verificare solo TEMP_DIR o tmpfs configurato

se STORAGE_POLICY=local_dev:
  verificare DATA_DIR
```

Altrimenti un deployment RAM-only fallirebbe comunque perche' il bootstrap pretende filesystem scrivibile.

### UploadFile FastAPI / Starlette

Punto da non sottovalutare.

Gli endpoint usano `UploadFile`. A seconda della configurazione framework/runtime, gli upload multipart possono essere spooled su file temporaneo gia' prima che il nostro codice legga `file.file`.

Quindi per dire "zero disk anche in upload" non basta eliminare i nostri `NamedTemporaryFile`.

Va verificato e deciso:

```text
1. accettiamo UploadFile come boundary framework, ma garantiamo no persistent disk dopo l'ingresso
2. oppure implementiamo upload streaming custom da Request.stream()
3. oppure configuriamo limiti/spooling in modo che non tocchi disco
```

Per il requisito forte "solo RAM", questa parte va testata con container read-only e upload HTTP reale.

### Nuovo layer I/O

Directory proposta:

```text
backend/app/core/io/
  __init__.py
  policy.py
  payloads.py
  memory.py
  archives.py
  geospatial.py
  tabular.py
  responses.py
  materialize.py
  guards.py
  metrics.py
```

Responsabilita':

#### `policy.py`

Definisce cosa e' consentito:

```text
forbid_persistent_disk
allow_temp_disk
forbid_all_disk
allow_local_dev_persistent
max_memory_payload_bytes
max_zip_members
max_uncompressed_zip_bytes
```

Esempio variabili:

```text
PLENORA_IO_DISK_POLICY=forbid_persistent
PLENORA_IO_TEMP_POLICY=allow_explicit
PLENORA_IO_MAX_MEMORY_BYTES=...
PLENORA_IO_REQUIRE_OBJECT_STORAGE_FOR_LARGE_OBJECTS=true
```

Policy possibili:

```text
memory_only
temp_only_explicit
object_storage_required
local_dev_allowed
legacy_compat
```

#### `payloads.py`

Tipi comuni:

```python
from dataclasses import dataclass
from typing import BinaryIO, Mapping, Any

@dataclass(frozen=True)
class BinaryPayload:
    data: bytes | BinaryIO
    filename: str
    content_type: str
    size: int | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] | None = None

@dataclass(frozen=True)
class StoredObjectRef:
    storage_key: str | None
    db_id: str | None
    filename: str
    content_type: str
    size: int
    sha256: str
    backend: str
    metadata: Mapping[str, Any] | None = None
```

#### `memory.py`

Utility RAM:

- `bytes_from_upload(upload)`
- `bytes_from_stream(stream, max_bytes=...)`
- `ensure_bytes(payload)`
- `bounded_bytesio(max_bytes=...)`
- `sha256_bytes(...)`
- `spooled_memory_policy(...)` se serve, ma attenzione: `SpooledTemporaryFile` puo' finire su disco

#### `archives.py`

ZIP in RAM:

- apertura ZIP da bytes
- validazione zip slip
- validazione zip bomb
- iterazione entry senza `extractall`
- creazione ZIP in `BytesIO`
- lettura singola entry

API proposta:

```python
archive = open_zip_bytes(data)
entries = archive.list()
payload = archive.read("path/file.geojson")
zip_bytes = build_zip_bytes({"a.txt": b"...", "b.txt": b"..."})
```

Regola:

```text
vietato usare zip.extractall fuori da questo modulo
```

#### `geospatial.py`

Boundary OSGeo/GDAL:

- scrittura dataset su `/vsimem`
- lettura dataset da bytes
- lettura ZIP via `/vsizip//vsimem`
- gestione Shapefile sidecar
- gestione OpenFileGDB directory
- packaging ZIP in RAM
- cleanup `/vsimem`

API proposta:

```python
read_vector_bytes(data, filename, format_hint=None) -> GeoDataset | DataFrame
write_vector_bytes(dataset, format="GPKG") -> BinaryPayload
write_shapefile_zip_bytes(dataset) -> BinaryPayload
write_openfilegdb_zip_bytes(dataset) -> BinaryPayload
```

Pattern interno:

```text
single file:
  bytes -> /vsimem/input.gpkg
  ogr.Open(...)

zip:
  bytes -> /vsimem/input.zip
  /vsizip//vsimem/input.zip/...

shapefile output:
  write sidecar in /vsimem/out/
  read sidecar bytes
  zip in BytesIO

openfilegdb output:
  write .gdb dir in /vsimem/out.gdb
  read tree bytes
  zip in BytesIO
```

#### `tabular.py`

DataFrame I/O RAM-first:

- CSV to/from `BytesIO` / `StringIO`
- XLSX to/from `BytesIO`
- Parquet to/from `BytesIO`
- JSON to/from bytes

API:

```python
dataframe_to_csv_bytes(df) -> BinaryPayload
dataframe_to_xlsx_bytes(df) -> BinaryPayload
dataframe_to_parquet_bytes(df) -> BinaryPayload
read_dataframe_bytes(data, filename, format_hint=None) -> DataFrame
```

#### `responses.py`

HTTP responses senza path fisico:

- `Response`
- `StreamingResponse`
- download con filename
- content type corretto

API:

```python
download_bytes_response(payload: BinaryPayload)
stream_download_response(stream, filename, content_type, size=None)
```

Regola:

```text
FileResponse(path) deve diventare eccezione legacy o vietata fuori dal boundary.
```

#### `materialize.py`

Unico punto dove e' ammesso creare un path fisico temporaneo.

API:

```python
with temporary_path_from_bytes(
    data,
    suffix=".xlsx",
    reason="legacy_library_requires_path",
    persistent=False,
) as path:
    ...
```

Questo modulo deve:

- richiedere un `reason`
- verificare policy ambiente
- scrivere solo sotto `DATA_DIR/temp` o tmpfs configurato
- fare cleanup garantito
- loggare evento
- emettere metrica
- fallire se policy vieta temp disk

Questo rende visibili i casi ancora non RAM-only.

#### `guards.py`

Guardie di sicurezza:

- max upload size
- max decompressed ZIP size
- max ZIP entries
- path traversal
- content type sniffing
- filename normalization
- estensioni consentite
- blocco symlink/hardlink se mai si materializza

#### `metrics.py`

Metriche:

```text
io_memory_bytes_total
io_object_storage_put_total
io_object_storage_get_total
io_db_blob_put_total
io_db_blob_get_total
io_temp_materialize_total
io_temp_materialize_bytes_total
io_persistent_disk_denied_total
io_zip_entries_total
io_vsimem_bytes_total
```

## Layer Storage

Separare I/O locale da persistenza.

Directory proposta:

```text
backend/app/core/storage/
  __init__.py
  service.py
  policy.py
  db_blob_store.py
  object_store.py
  local_dev_store.py
  refs.py
```

### Obiettivo

Il codice business non deve sapere se il payload va su:

- DB BYTEA
- S3
- MinIO
- local dev

Deve chiamare:

```python
stored = storage.save(payload, scope="runtime_result")
```

e leggere:

```python
payload = storage.open(stored)
```

### Policy storage

Esempio:

```text
STORAGE_POLICY=db
STORAGE_POLICY=object_storage
STORAGE_POLICY=local_dev
```

Regole:

- payload piccoli possono andare in DB
- payload grandi devono andare su S3/MinIO
- local filesystem persistente solo in dev o con opt-in esplicito
- produzione/staging non devono usare local persistente
- DB deve contenere metadata e riferimenti

### DB

Salvataggio:

```sql
INSERT INTO blobs (
  id,
  scope,
  filename,
  content_type,
  size_bytes,
  sha256,
  data,
  created_at
)
VALUES (...)
```

Oppure usare tabelle esistenti se gia' disponibili.

### S3/MinIO

Non passare da file locale:

```python
client.put_object(
    Bucket=bucket,
    Key=key,
    Body=bytes_or_stream,
    ContentType=content_type,
)
```

Download:

```python
obj = client.get_object(Bucket=bucket, Key=key)
stream = obj["Body"]
```

Per payload piccoli:

```python
data = stream.read()
```

Per payload grandi:

```python
StreamingResponse(stream)
```

### Local dev

Il filesystem persistente locale deve essere esplicito:

```text
STORAGE_POLICY=local_dev
PLENORA_ALLOW_LOCAL_PERSISTENCE=true
ENVIRONMENT=development
```

In produzione:

```text
local persistent storage -> errore
```

## Lettura centralizzata

La lettura va centralizzata tanto quanto la scrittura.

Problema attuale:

```text
download da S3 -> file temp -> libreria legge path
```

Target:

```text
download da S3/DB/upload -> bytes/stream -> reader RAM
```

Flusso:

```python
source = storage.open(ref)
dataset = io_readers.read(
    source,
    filename=ref.filename,
    content_type=ref.content_type,
    expected_format=ref.format,
)
```

Casi:

- CSV: `BytesIO`/`StringIO`
- XLSX: `BytesIO`
- Parquet: `BytesIO`
- JSON: bytes/string
- ZIP: `zipfile.ZipFile(BytesIO(...))`
- GPKG: `/vsimem/input.gpkg`
- GeoJSON: `/vsimem/input.geojson`
- FlatGeobuf: `/vsimem/input.fgb`
- Shapefile ZIP: `/vsizip//vsimem/input.zip/file.shp`
- OpenFileGDB ZIP: `/vsizip//vsimem/input.zip/folder.gdb`

Solo se una libreria non supporta bytes o `/vsimem`:

```python
with materialize.temporary_path_from_bytes(
    data,
    reason="legacy_reader_requires_path",
) as path:
    ...
```

## Scrittura centralizzata

Il codice business produce dati, ma non decide il path.

Target:

```python
payload = io_exports.dataframe_to_xlsx_bytes(df)
stored = storage.save(payload, scope="result_export")
```

Geospaziale:

```python
payload = io_geospatial.write_vector_bytes(gdf, format="GPKG")
stored = storage.save(payload, scope="file_source")
```

Shapefile:

```python
payload = io_geospatial.write_shapefile_zip_bytes(gdf)
stored = storage.save(payload, scope="file_source")
```

Download:

```python
payload = storage.open(ref)
return io_responses.download_bytes_response(payload)
```

## Regole architetturali

### Vietato fuori dal boundary I/O

Fuori da `app/core/io` e dai moduli storage autorizzati:

```text
tempfile.NamedTemporaryFile
tempfile.TemporaryDirectory
tempfile.mkdtemp
open(path, "wb")
Path.write_bytes
Path.write_text
zipfile.ZipFile.extractall
shutil.copy
shutil.copyfile
shutil.move
FileResponse(path)
df.to_csv(path)
df.to_excel(path)
df.to_parquet(path)
storage.upload(local_path)
storage.download(local_path)
```

Non tutto puo' essere eliminato immediatamente, ma ogni uso residuo deve passare da:

```text
io/materialize.py
```

### Obbligatorio

Ogni materializzazione deve dichiarare:

```text
reason
scope
persistent=False
max_bytes
cleanup strategy
```

Esempio:

```python
with temporary_path_from_bytes(
    data,
    suffix=".xlsx",
    reason="library_requires_path",
    scope="legacy_excel_import",
    persistent=False,
) as path:
    ...
```

## Moduli backend da migrare

### 1. Upload e file source

File principali:

```text
backend/app/core/files/upload.py
backend/app/core/files/source_blob.py
backend/app/core/files/source_loading.py
backend/app/core/files/preview_temp.py
backend/app/core/files/multi_layer_upload.py
backend/app/core/files/source_read.py
```

Problemi attuali:

- `NamedTemporaryFile`
- `mkdtemp`
- `safe_extractall`
- materializzazione ZIP su directory
- file source risolto come path fisico

Target:

- leggere upload in bytes con size guard
- ZIP in RAM
- geospatial ZIP via `/vsizip//vsimem`
- preview da bytes
- file source salvato su DB/S3/MinIO
- path fisico solo tramite `materialize.py`

### 2. IMDF

File:

```text
backend/app/core/imdf/loader.py
backend/app/core/imdf/persist.py
backend/app/core/imdf/storage.py
backend/app/core/imdf/bundle_store.py
backend/workers/tasks/imdf_publish_task.py
```

Problemi attuali:

- estrae ZIP IMDF su filesystem
- persiste bundle in `IMDF_BUNDLES_DIR`
- cache locale
- hydrate da object storage verso disco locale

Target:

- leggere IMDF ZIP da bytes
- validare entries senza extractall
- metadata in DB
- bundle su object storage o DB
- cache locale opzionale solo dev/performance e non source of truth
- download/preview via bytes/stream

Nota:

IMDF e' probabilmente il refactor piu' grande perche' oggi il modello logico e' "bundle directory".
Va convertito a "bundle object" o "bundle archive".

### 3. Results e artifacts

File:

```text
backend/app/core/results/result_reader.py
backend/app/core/results/pipeline_result.py
backend/app/core/results/blob_writer.py
backend/app/core/results/temp_paths.py
backend/app/core/artifacts/resolver.py
backend/app/core/scheduler/artifact_store.py
backend/app/core/scheduler/run_archive_store.py
backend/app/core/scheduler/router.py
```

Problemi attuali:

- temp path per reader
- `FileResponse(path)`
- artifact path-based
- scheduler archive locale

Target:

- `storage.open(ref)` restituisce bytes/stream
- API download usa `StreamingResponse`
- artifact store salva bytes su DB/S3/MinIO
- niente `RESULTS_DIR` come source of truth
- `RESULTS_DIR` solo legacy opt-in

### 4. Export CSV/XLSX/Parquet

File:

```text
backend/app/db/query_executor.py
backend/app/db/query_streaming.py
backend/app/core/features/features_service.py
backend/app/core/intermediate/artifact_writer.py
backend/workers/tasks/carica_task.py
```

Problemi attuali:

- alcuni export scrivono su `final_path`
- CSV/XLSX possono essere generati in RAM

Target:

- `df.to_csv(BytesIO/StringIO)`
- `df.to_excel(BytesIO)`
- `df.to_parquet(BytesIO)`
- salvataggio via `storage.save(payload)`

### 5. REST file enrichment

File:

```text
backend/app/core/arricchisci/api_connection_rest_file.py
backend/app/core/arricchisci/api_connection_support.py
backend/workers/tasks/enrichment_task.py
```

Problemi attuali:

- materializza input/output su filesystem
- cleanup manuale

Target:

- multipart da bytes
- payload request da stream/bytes
- response file in bytes
- result storage DB/S3/MinIO
- temp path solo per API/client legacy che richiede path

### 6. Backup e restore

File:

```text
backend/scripts/backup_db.py
backend/scripts/test_restore.py
```

Problemi attuali:

- `pg_dump` scrive dump locale
- `pg_restore` legge dump locale

Target possibile:

- `pg_dump` stdout -> object storage stream
- object storage stream -> `pg_restore` stdin

Nota:

Questo e' operativo, non runtime applicativo. Si puo' trattare come fase separata.

### 7. Datasource local

File:

```text
backend/app/core/datasources/local.py
```

Questo caso e' diverso:

```text
Datasource local per definizione scrive su filesystem.
```

Opzioni:

1. disabilitarlo in produzione
2. consentirlo solo in dev
3. sostituirlo con object storage provider
4. mantenerlo come opt-in esplicito e non conforme a RAM-only

Raccomandazione:

```text
local datasource deve essere dev-only o esplicitamente non-compliant.
```

## Piano di migrazione

## Priorita' concreta sui call-site attuali

Guardando il codice, la migrazione piu' efficace non e' uniforme. Alcuni punti danno molto risultato con poco rischio, altri sono refactor profondi.

### Priorita' 1 - Geospatial bytes nel dataframe boundary

File:

```text
backend/app/core/dataframes/frame_io.py
backend/app/core/dataframes/geospatial_io.py
```

Motivo:

Questi moduli sono gia' il boundary centrale per DataFrame I/O. Inoltre oggi dichiarano ancora `gpkg/shp/zip/gdb` come path-bound.

Modifiche:

```text
1. sostituire geospatial_dataframe_to_bytes per GPKG/SHP/GDB con OSGeo /vsimem
2. sostituire _load_dataframe_from_bytes per GPKG/SHP/ZIP/GDB con /vsimem + /vsizip
3. lasciare load_dataframe_from_file come adapter legacy
4. aggiungere test read-only per questi metodi
```

Questa e' la prima migrazione da fare perche' elimina l'assunzione tecnica sbagliata:

```text
"GPKG/SHP/GDB richiedono filesystem"
```

Il probe ha dimostrato che non e' piu' vero se usiamo OSGeo/GDAL correttamente.

### Priorita' 2 - Upload/preview senza temp path

File:

```text
backend/app/core/files/upload.py
backend/app/core/files/multi_layer_upload.py
backend/app/core/files/preview_temp.py
```

Motivo:

Questi moduli leggono gia' `raw_data = file_content.read()`, quindi hanno gia' i bytes in RAM, ma poi li scrivono subito su disco per parse/list layer.

Modifiche:

```text
1. leggere upload in bytes con size guard
2. validare MIME sui bytes
3. passare bytes al nuovo dataframe/geospatial reader
4. per ZIP geospaziali: /vsizip//vsimem
5. eliminare safe_extractall dal path primario
6. persistere raw_data con dispatch_blob_storage come gia' oggi
```

Questa fase non deve cambiare il modello DB. Deve solo evitare staging locale.

### Priorita' 3 - Eliminare cache stabile file_source su filesystem

File:

```text
backend/app/core/files/source_loading.py
backend/app/core/files/source_blob.py
backend/app/core/files/source_read.py
```

Motivo:

Questi moduli fanno materializzazione esplicita:

```text
file_source_id -> path fisico
```

Esempi:

```python
resolve_file_source_id_path(...)
temp_file_for(...)
temp_file_for_cm(...)
```

Questi sono compatibility shims. Nel refactor vanno sostituiti da:

```text
file_source_id -> Resource bytes/stream -> io reader
```

Modifiche:

```text
1. mantenere path mode solo per legacy
2. aggiungere API file_source_id -> bytes
3. aggiungere API file_source_id -> DataFrame/GeoDataFrame via io readers
4. loggare ogni uso residuo di path mode
5. fallire path mode se policy memory_only
```

### Priorita' 4 - Results/download

File:

```text
backend/app/core/results/blob_writer.py
backend/app/core/results/result_reader.py
backend/app/core/scheduler/router.py
```

Motivo:

Qui esiste gia' `save_result_blob_from_bytes`, quindi la direzione e' gia' tracciata.

Modifiche:

```text
1. nuovi result sempre da bytes
2. result_reader deve preferire bytes/stream
3. FileResponse(path) solo legacy
4. download via StreamingResponse
5. save_result_blob(path) deprecated
```

### Priorita' 5 - Scheduler archive

File:

```text
backend/app/core/scheduler/artifact_store.py
backend/app/core/scheduler/run_archive_store.py
```

Motivo:

Questo e' ancora filesystem-first:

```text
RESULTS_DIR/{project_id}/runs/...
shutil.copy2(...)
```

Target:

```text
scheduled run archive -> object storage prefix + DB metadata
```

Questa parte va fatta dopo results/download, perche' dipende dallo stesso modello di StoredObjectRef.

### Priorita' 6 - IMDF

File:

```text
backend/app/core/imdf/loader.py
backend/app/core/imdf/persist.py
backend/app/core/imdf/storage.py
backend/app/core/imdf/bundle_store.py
```

Motivo:

E' il refactor piu' invasivo perche' il modello corrente e':

```text
bundle = directory locale
```

Target:

```text
bundle = archive object + metadata DB/object
```

Va fatto quando il nuovo storage/read/response boundary e' gia' stabile.

### Priorita' 7 - Backup/restore

File:

```text
backend/scripts/backup_db.py
backend/scripts/test_restore.py
```

Motivo:

Non e' runtime HTTP/Celery ordinario. Si puo' trattare separatamente con streaming `pg_dump`/`pg_restore`.

### Priorita' 8 - Datasource local

File:

```text
backend/app/core/datasources/local.py
```

Decisione di prodotto/ops, non solo tecnica:

```text
local datasource = scrittura filesystem per definizione
```

Va marcato come:

```text
dev-only
oppure opt-in esplicito
oppure non compliant con RAM-only
```

## Pulizia finale e rimozione residui legacy

Questa parte e' fondamentale: il refactor non deve finire con due sistemi paralleli.

Alla fine della migrazione non devono restare funzioni sparse di lettura/scrittura che aggirano il nuovo boundary I/O. Gli adapter legacy possono esistere solo temporaneamente, con una allowlist e una data/fase di rimozione.

### Principio

Durante la migrazione:

```text
vecchio path-based -> adapter legacy -> nuovo boundary
```

A fine migrazione:

```text
codice business -> nuovo boundary I/O + Storage
```

Non deve restare:

```text
codice business -> helper legacy path-based
```

### Cosa cancellare o ridurre a legacy interno

#### File source materialization

Da rimuovere come API pubbliche dopo migrazione:

```text
backend/app/core/files/source_loading.py
  resolve_file_source_id_path(...)

backend/app/core/files/source_blob.py
  temp_file_for(...)
  temp_file_for_cm(...)
```

Target finale:

```text
file_source_id -> Resource Engine bytes/stream -> io reader
```

Se serve ancora un path per una libreria legacy:

```text
solo app/core/io/materialize.py
```

#### Result temp reader

Da rimuovere o rendere internal-only:

```text
backend/app/core/results/result_reader.py
  get_result_as_tempfile(...)
  get_result_as_tempfile_cm(...)
```

Target finale:

```text
result_id/project_id -> Resource bytes/stream -> response/parser
```

`get_result_as_bytes(...)` puo' restare solo se delega al Resource Engine o allo storage boundary.

#### Result path save

Da deprecare e poi rimuovere:

```text
backend/app/core/results/blob_writer.py
  save_result_blob(path, ...)
```

Target finale:

```text
save_result_blob_from_bytes(...)
storage.save(BinaryPayload)
```

Se un caller produce ancora un path, deve essere il caller a passare da `io.materialize` e la cosa deve essere loggata come fallback.

#### Upload temp helpers

Da eliminare dal path principale:

```text
backend/app/core/files/upload.py
  mkdtemp_writable(...)
  create_from_upload(...) path-temp branch

backend/app/core/files/multi_layer_upload.py
  NamedTemporaryFile
  mkdtemp_writable
  safe_extractall branch

backend/app/core/files/preview_temp.py
  preview_file_temp(...) path-temp implementation
```

Target finale:

```text
upload bytes -> io readers -> schema inference -> dispatch_blob_storage
```

Potra' restare un adapter legacy tipo:

```text
preview_file_temp_legacy_path(...)
```

ma solo se:

```text
1. non esportato come API normale
2. protetto da policy
3. presente in allowlist con ticket/fase rimozione
```

#### ZIP extraction

Da eliminare fuori dal boundary:

```text
safe_extractall(...)
safe_extract_zip_path(...)
zipfile.ZipFile.extractall(...)
```

Non va cancellato `zip_safe.py`: va ridotto a validatore condiviso.

Target finale:

```text
zip_safe.py
  valida entries, zip slip, zip bomb

io/archives.py
  legge entries in RAM

io/materialize.py
  unico posto dove extract su filesystem e' ancora ammesso
```

#### Geospatial path-based export

Da ridurre a legacy:

```text
backend/app/core/dataframes/geospatial_io.py
  _salva_shapefile_zippato(...) con TemporaryDirectory
  _salva_formato_geo(...) path-first
```

Target finale:

```text
geospatial_dataframe_to_bytes(...)
  GeoJSON -> bytes
  GPKG -> /vsimem bytes
  SHP -> /vsimem sidecar + ZIP BytesIO
  OpenFileGDB -> /vsimem .gdb + ZIP BytesIO
```

Le funzioni path-based possono restare solo come:

```text
save_geospatial_dataframe_to_path_legacy(...)
```

e devono passare da `io.materialize` o essere usate solo in dev/test espliciti.

#### DataFrame path-bound artifact reader

Da rimuovere come path primario:

```text
backend/app/core/dataframes/frame_io.py
  _PATH_BOUND_ARTIFACT_FORMATS
  tempfile.NamedTemporaryFile branch in _load_dataframe_from_bytes
  tempfile/shutil branch in load_dataframe_from_artifact
  _carica_da_zip(...) extractall/tempdir
```

Target finale:

```text
load_dataframe_from_bytes(...)
load_dataframe_from_resource(...)
load_dataframe_from_file(...) solo legacy adapter
```

#### Scheduler filesystem archive

Da sostituire:

```text
backend/app/core/scheduler/artifact_store.py
  ScheduleArtifactStore.archive(...)
  shutil.copy2(...)

backend/app/core/scheduler/run_archive_store.py
  ScheduleRunArchiveStore filesystem reader
```

Target finale:

```text
ScheduleRunArchiveStore -> object storage/DB metadata
```

Non deve restare una doppia lista run:

```text
alcune da filesystem
alcune da storage
```

se non come modalita' legacy esplicita.

#### IMDF local bundle

Da rimuovere come source of truth:

```text
backend/app/core/imdf/persist.py
  persist locale in IMDF_BUNDLES_DIR

backend/app/core/imdf/storage.py
  save_bundle(local_dir, ...)
  ensure_local_bundle(...) hydration su filesystem

backend/app/core/imdf/bundle_store.py
  ensure_local_bundle(...)
```

Target finale:

```text
IMDF bundle = object/archive bytes + metadata
local cache = opzionale, non source of truth, policy-controlled
```

#### Eseguibili/output storage path variant

Da deprecare:

```text
backend/app/core/eseguibili/storage.py
  save(task_id, src_path)
```

Da tenere come primario:

```text
save_bytes(task_id, filename, data)
open_stream(key)
```

#### FileSystemProtocol generico

Da non usare nei nuovi flussi:

```text
backend/app/core/files/service_deps.py
  FileSystemProtocol
  DefaultFileSystem
```

Puo' restare solo per test legacy o adapter di compatibilita'.

Target:

```text
dipendenze specifiche:
  PayloadReader
  ArchiveReader
  GeospatialReader
  StorageWriter
  Materializer
```

Un protocollo filesystem generico e' troppo largo: rende facile reintrodurre scritture non governate.

### Processo di decommissioning

Ogni vecchia funzione deve attraversare questi stati:

```text
1. Active legacy
   ancora usata dai call-site esistenti

2. Deprecated
   emette warning/log strutturato quando chiamata
   ha replacement documentato

3. Internal compatibility only
   non esportata in __all__
   non importabile dai nuovi moduli
   coperta da allowlist

4. Removed
   funzione cancellata
   test statico impedisce reintroduzione
```

Esempio log richiesto in fase deprecated:

```text
event=legacy_disk_io_used
function=get_result_as_tempfile_cm
replacement=ResourceEngine(mode="bytes")+io.responses
policy=...
caller=...
```

### Allowlist temporanea

La allowlist del test statico deve essere decrementale.

Formato consigliato:

```yaml
- file: backend/app/core/files/source_blob.py
  symbol: temp_file_for_cm
  reason: legacy path materialization for current file-source callers
  replacement: ResourceEngine bytes + io readers
  migration_phase: P3
  remove_by: io-refactor-phase-3-complete
```

Regola:

```text
nessuna voce allowlist senza replacement e fase di rimozione
```

### Criterio di fine refactor

Il refactor si considera finito solo quando:

```text
1. i flussi principali passano con PLENORA_IO_DISK_POLICY=memory_only
2. i test statici non hanno allowlist per nuovi path runtime
3. Resource Engine path mode non e' usato dai flussi ordinari
4. FileResponse(path) non e' usato per result/artifact/download runtime
5. upload/preview non usano NamedTemporaryFile o extractall
6. geospatial GPKG/SHP/GDB/ZIP passano da /vsimem e /vsizip
7. RESULTS_DIR e IMDF_BUNDLES_DIR non sono source of truth
8. datasource local e' esplicitamente dev-only/opt-in/non-compliant
```

### Regola finale

Il nuovo layer non deve diventare "un altro modo" per fare I/O.

Deve diventare:

```text
l'unico modo supportato
```

Tutto il resto va cancellato, reso legacy-internal, oppure bloccato da policy/test.

### Fase 0 - Baseline e guardie

Obiettivo:

- fotografare gli usi filesystem attuali
- aggiungere test statici che impediscono nuove scritture dirette
- nessun refactor funzionale pesante

Azioni:

1. creare lista allowlist temporanea dei call site esistenti
2. aggiungere test `rg`/AST che blocca nuovi usi fuori allowlist
3. introdurre `app/core/io/policy.py`
4. introdurre `app/core/io/materialize.py`
5. loggare ogni nuova materializzazione

### Fase 1 - Core I/O RAM

Obiettivo:

- creare API condivise senza migrare tutto subito

Azioni:

1. `payloads.py`
2. `memory.py`
3. `archives.py`
4. `tabular.py`
5. `responses.py`
6. test unitari per CSV/XLSX/Parquet/ZIP

### Fase 2 - Geospatial OSGeo

Obiettivo:

- portare nel backend il probe validato

Azioni:

1. scegliere base immagine:
   - opzione A: OSGeo image come base backend
   - opzione B: python slim + `python3-gdal` Debian
2. implementare `geospatial.py`
3. test per:
   - GPKG RAM
   - GeoJSON RAM
   - FlatGeobuf RAM
   - Shapefile ZIP RAM
   - OpenFileGDB ZIP RAM
4. test container `--read-only`

Raccomandazione tecnica:

```text
Usare uno stack GDAL unico per evitare conflitti /vsimem.
```

Decisione immagine Docker:

```text
Opzione A - base OSGeo/GDAL:
  FROM ghcr.io/osgeo/gdal:ubuntu-full-3.11.4
  Pro: GDAL/osgeo coerente, gia' compilato, /vsimem affidabile
  Contro: cambia Python/base OS; il probe usa Python 3.12.3, mentre backend oggi usa Python 3.11

Opzione B - python:3.11-slim-bookworm + python3-gdal:
  Pro: meno impatto sul backend attuale
  Contro: Debian porta GDAL 3.6.2; pyogrio resta su GDAL 3.11.4 vendorizzato, quindi non mischiare /vsimem

Opzione C - immagine custom multi-stage:
  base runtime compatibile backend + GDAL/osgeo coerenti
  Pro: piu' controllata
  Contro: maggiore manutenzione build
```

Se si sceglie OSGeo come base backend, vanno ritestati:

```text
psycopg2
pyodbc
msodbcsql18
postgresql-client-17
pandas/geopandas
Celery worker/beat
healthcheck
build frontend/backend compose
```

Il container `plenora-test-osgeo-gdal` dimostra la capacita' GDAL, non ancora la compatibilita' completa con tutto lo stack Plenora.

### Fase 3 - Storage bytes/stream

Obiettivo:

- object storage e DB senza path locali

Azioni:

1. estendere cloud backend con:
   - `upload_bytes`
   - `upload_stream`
   - `download_bytes`
   - `open_stream`
2. evitare `upload(local_path)` nei nuovi flussi
3. aggiungere `storage.save(payload)`
4. aggiungere `storage.open(ref)`
5. mappare scope:
   - runtime_result
   - file_source
   - imdf_bundle
   - scheduler_artifact
   - export

### Fase 4 - Upload/file source

Obiettivo:

- eliminare temp disk dai flussi upload piu' comuni

Azioni:

1. upload bytes con size guard
2. ZIP senza extractall
3. geospatial ZIP via `geospatial.py`
4. preview RAM
5. salvataggio file source su DB/S3/MinIO

### Fase 5 - Result/export/download

Obiettivo:

- eliminare `FileResponse(path)` e export su path

Azioni:

1. CSV/XLSX/Parquet bytes
2. `StreamingResponse`
3. result reader da storage ref
4. scheduler artifact come object storage/DB

### Fase 6 - IMDF

Obiettivo:

- eliminare `IMDF_BUNDLES_DIR` come persistenza primaria

Azioni:

1. bundle come ZIP object
2. metadata DB
3. no extractall
4. cache locale solo opzionale
5. publish task da bytes/stream

### Fase 7 - Backup/restore

Obiettivo:

- evitare dump file locale

Azioni:

1. `pg_dump` stdout
2. stream verso S3/MinIO
3. restore da stream/stdin
4. test restore end-to-end

## Test richiesti

### Unit test

- ZIP in RAM
- ZIP bomb guard
- ZIP slip guard
- CSV/XLSX/Parquet bytes
- storage save/open DB
- storage save/open S3/MinIO mock
- materialize policy deny/allow
- geospatial `/vsimem` single-file
- Shapefile ZIP RAM
- OpenFileGDB ZIP RAM

### Contract test

Verificare che fuori dal boundary I/O non compaiano nuovi usi di:

```text
tempfile.NamedTemporaryFile
tempfile.TemporaryDirectory
tempfile.mkdtemp
zipfile.ZipFile.extractall
Path.write_bytes
Path.write_text
open(..., "wb")
FileResponse
shutil.move
shutil.copy
```

Implementazione consigliata:

```text
tests2/io/test_no_direct_filesystem_contract.py
```

Il test deve:

```text
1. fare scan AST o rg dei moduli backend/app e backend/workers
2. consentire solo allowlist temporanea dei call-site legacy
3. fallire se appare un nuovo uso fuori allowlist
4. avere allowlist commentata con owner/motivo/fase di migrazione
```

La allowlist iniziale non deve essere "permissiva". Deve indicare per ogni voce:

```text
file
funzione
motivo
replacement target
fase prevista
```

Esempio:

```text
backend/app/core/files/upload.py:create_from_upload
  motivo: legacy upload staging
  target: io.readers bytes + geospatial /vsimem
  fase: Priorita' 2
```

### Runtime deny test

Oltre al test statico, serve un test runtime che intercetti scritture accidentali:

```text
PLENORA_IO_DISK_POLICY=memory_only
```

e monkeypatch di:

```text
tempfile.NamedTemporaryFile
tempfile.TemporaryDirectory
tempfile.mkdtemp
Path.write_bytes
Path.write_text
builtins.open in write/append/create mode
shutil.copy*
shutil.move
zipfile.ZipFile.extractall
```

Obiettivo:

```text
i nuovi flussi RAM-only devono passare anche se queste API sono vietate
```

Non sostituisce il container `--read-only`, ma becca prima i regressions nei test unitari.

### Integration test

- upload CSV -> preview -> save -> download
- upload XLSX -> preview -> save -> download
- upload GPKG -> preview -> save -> download
- upload Shapefile ZIP -> preview -> save -> download
- upload OpenFileGDB ZIP -> preview -> save -> download
- result export CSV/XLSX senza filesystem
- scheduler artifact download senza filesystem
- IMDF bundle upload/publish/download senza `IMDF_BUNDLES_DIR` persistente

### Read-only container test

Per i flussi RAM-only:

```text
docker run --read-only --network none ...
```

Il test deve passare per:

- geospatial probe
- tabular export probe
- ZIP probe
- storage mock probe

## Rischi e tradeoff

### Memoria

Spostare tutto in RAM richiede limiti.

Servono:

- max upload bytes
- max decompressed ZIP bytes
- max export bytes
- fallback streaming per object storage
- policy che sposta payload grandi su S3/MinIO

### Compatibilita' librerie

Alcune librerie vogliono path fisico.

Mitigazione:

- `materialize.py` unico fallback
- reason obbligatorio
- cleanup automatico
- metrica
- test che conta quante volte succede

### GDAL stack

`pyogrio` e `osgeo` non devono usare due GDAL diversi nello stesso flusso `/vsimem`.

Mitigazione:

- scegliere OSGeo/GDAL come stack unico per geospatial RAM-only
- evitare mix `/vsimem` tra pyogrio e osgeo

### Refactor grande

Il refactor tocca molti moduli.

Mitigazione:

- creare boundary prima
- migrare per scope
- mantenere adapter legacy temporanei
- usare allowlist testuale dei call site esistenti
- rimuovere allowlist progressivamente

## Decisioni consigliate

1. Creare `app/core/io` e `app/core/storage` come boundary orizzontali.
2. Vietare nuove scritture dirette fuori dal boundary.
3. Usare OSGeo/GDAL unico per i flussi geospaziali RAM-only.
4. Persistenza primaria: DB o S3/MinIO, non filesystem locale.
5. `local datasource` solo dev/opt-in.
6. `IMDF_BUNDLES_DIR` e `RESULTS_DIR` non devono essere source of truth.
7. Ogni path fisico temporaneo deve passare da `materialize.py`.
8. Download via `StreamingResponse`/bytes, non `FileResponse(path)`.
9. Upload/download object storage via bytes/stream, non `local_path`.
10. Aggiungere test read-only container per i flussi RAM-only.

## Conclusione

La modifica e' un refactor architetturale importante, ma e' la direzione piu' solida.

Non conviene inseguire ogni singolo `tempfile` localmente. Conviene creare un boundary orizzontale I/O + Storage e poi migrare i call site.

I test OSGeo/GDAL dimostrano che i casi geospaziali difficili possono essere gestiti interamente in RAM:

```text
GeoJSON, GPKG, FlatGeobuf, Shapefile ZIP, OpenFileGDB ZIP
```

Il resto del lavoro e' integrare questa capacita' nel backend reale:

```text
upload -> reader RAM -> storage DB/S3/MinIO -> download stream
```

Con questa struttura, il backend diventa piu' semplice da controllare:

```text
nessun modulo business scrive su disco
tutte le eccezioni sono dichiarate
ogni persistenza passa da DB/S3/MinIO/local-dev policy
```

## Boundary congelato — by-design residues (2026-05-25)

Stato dopo Phase5 + REST connector bytes-first + IMDF-1..6 +
QUERY-1 + QUERY-1 step 2 (legacy streaming helpers tombstoned) +
QUERY-1 step 3 (IMDF legacy tombstoned: `extract_imdf_zip(path)`,
`persist_imdf_bundle_to_disk`, `ensure_local_bundle`,
`_hydrate_local_cache`).

Il report `test_report_inside_boundary_frozen` segnala **14 entries
su 8 file** all'interno del boundary I/O. Questi residui sono
intenzionali e classificati per categoria. Non vanno inseguiti: se
qualcuno li tocca, deve aggiornare contestualmente questa sezione
con il nuovo razionale.

### Boundary primitives (intentional disk capability)

File che esistono PER esporre disk capability controllata al resto
del codice. Esigere che non chiamino `tempfile`/`write_bytes`/
`extractall` sarebbe meta-circolare.

| File | Entries | Razionale |
|---|---|---|
| `app/core/io/materialize.py` | 3 | `temporary_path_from_bytes` escape hatch + helper. Politica `MEMORY_ONLY` configurabile. |
| `app/core/io/geospatial.py` | 1 | Tempdir per librerie che richiedono `/vsimem` fallback (es. driver legacy). |
| `app/core/results/temp_paths.py` | 2 | Adapter path-only per result streaming + eseguibili hydration (centralizzato). |
| `app/utils/zip_safe.py` | 3 | `safe_extractall` zip-bomb-safe + `pick_zip_member_by_extension`. Implementation primitive. |
| `app/core/files/service_deps.py` | 2 | `DefaultFileSystem.extract_zip` path adapter per upload pipeline. |

**Policy**: questi file possono usare filesystem primitives liberamente.
Modifiche che ne riducono il numero di entries sono benvenute ma non
obbligatorie. Modifiche che ne aumentano il numero richiedono nuovo
razionale qui.

### IMDF subsystem (format-as-directory + cache hydration)

OGC IMDF e' format-as-directory (`.gdb` File Geodatabase letto da
pyogrio via filesystem). Il subsystem accetta intentionally questa
shape per i path che richiedono GDAL/pyogrio.

| File | Entries | Razionale |
|---|---|---|
| `app/core/imdf/loader.py` | 2 | `extract_imdf_zip` (path-mode legacy) usa `tempfile.mkdtemp` + `safe_extractall`. Post-IMDF-6: `extract_imdf_zip_bytes` fully-RAM (legge solo membri zip via `ZipFile.read`, popola `bundle.files` + `gdb_zip_bytes` + `ifc_bytes`; tmp_dir=None). |
| `app/core/imdf/persist.py` | 3 | `persist_imdf_bundle_to_disk` legacy (write_bytes + write_text + shutil.move). La funzione canonica IMDF-3 `persist_imdf_bundle` va su storage; il legacy resta per tooling/test finche' non migrano. |
| `app/core/imdf/storage.py` | 2 | `ensure_local_bundle` hydration `path.write_bytes` + `safe_extractall` (.gdb extract da per-file layout). Post IMDF-5: `hydrate_gdb_to_temp` rimosso, sostituito da `open_gdb_in_vsimem` (ctypes adapter su `gdal_vsi.py`, zero filesystem callsite). |

**Policy**: questi file restano filesystem-aware finche' il subsystem
publish/validate non viene migrato (IMDF-5+). Tombstone del legacy
zip fallback (`safe_extractall` in `ensure_local_bundle`) e' un break
prodotto separato, da discutere quando si accetta la perdita di
compatibilita' con bundle pre-IMDF-3.

### Scripts (one-off tooling, not application code)

| File | Entries | Razionale |
|---|---|---|
| `scripts/backup_db.py` | 1 | Backup utility, usa tempdir locale per dump intermedio. Non production runtime. |
| `scripts/test_restore.py` | 1 | Test harness restore DB. Esegue solo manualmente. |
| `scripts/export_openapi_manifest.py` | 1 | Dump OpenAPI su file per integrazione esterna. One-shot. |
| `scripts/imdf_real_e2e_check.py` | 2 | E2E IMDF locale: scarica bundle reale + estrae + valida. Tool di smoke test. |

**Policy**: `scripts/` resta esplicitamente fuori dal boundary
(`_EXCLUDED_ROOTS = ("scripts",)` in `_gen_io_allowlist.py`). Modifiche
non richiedono aggiornamento di questa sezione.

### Criterio di freeze

- Il report `test_report_inside_boundary_frozen` enforces lo snapshot
  per-file. Test fails se: (a) nuovo file appare nel boundary; (b)
  esistente cambia numero di entries.
- Aggiornare contestualmente questo doc + snapshot quando si fa una
  modifica deliberata (es. IMDF-5 chiude publish path).
- Il report e' un *ratchet bidirezionale*, non un target da
  minimizzare. Migrazioni che fanno scendere il numero sono
  benvenute ma richiedono aggiornamento esplicito del doc.

### Non in freeze

Le seguenti aree restano target attivi di migrazione (non by-design):

- `extract_imdf_zip(path)` path-mode legacy in loader.py — il path
  bytes-first IMDF-6 e' la via canonica; il path-mode resta per i
  test che leggono da disk. Tombstone se i test migrano.
- `ensure_local_bundle` hydration su `IMDF_BUNDLES_DIR`: usata solo
  da DELETE endpoint e (residualmente) per cache cleanup. Tombstone
  possibile se DELETE migra a `storage.delete_bundle` puro.
- `persist_imdf_bundle_to_disk` legacy — rimovibile quando test/script
  (`imdf_real_e2e_check.py`, `test_imdf_persist_bundle_contract.py`,
  `test_plenora_metadata_schema.py`) migrano a `persist_imdf_bundle`.

Aree tombstoned (post IMDF-4 step 4 + IMDF-5, no piu' fallback legacy):
- `storage.save_bundle(task_id, local_dir, meta)` legacy zip writer.
- `ensure_local_bundle` zip-based fallback (downloadava `{task_id}.zip`).
- Router `/publish` / `/validate-publish` / `GET /bundles/{id}` /
  `DELETE` fallback `_hydrate_local_cache + read_metadata`.
- Validator + publish task fallback `ensure_local_bundle + find_gdb_dir`.
- `storage.hydrate_gdb_to_temp` (IMDF-5: sostituito da
  `open_gdb_in_vsimem` con ctypes adapter `/vsimem`).
- Tmp dir per `.gdb` extraction in worker + validator/publish (.gdb
  ora vive in `/vsimem` durante l'operazione, zero disco).
- Bundle pre-IMDF-3 senza `_gdb.zip` su storage richiedono re-upload
  via `persist_imdf_bundle` per essere processabili.
