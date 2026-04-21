# ADR-0011: `FileSourcesDispatch` soglia S3 50MB, pattern FS-4 simmetrico

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL gestisce "file sources" — file caricati dagli utenti come input a nodi eseguibili o data source del canvas (Excel con anagrafiche, shapefile, GeoJSON, bundle ZIP, ecc.). Il volume per-file varia molto: si va da 10 KB (lookup table Excel) a centinaia di MB (bundle DXF compressi, GeoTIFF, dumps).

Domanda di storage: **dove metto i bytes del file**?

Due opzioni:

1. **DB (Postgres)**: colonna `BYTEA` in `plenora.file_sources.data`. Pro: transazionale, backup con `pg_dump` include tutto, audit e ACL unificati con i metadati. Contro: DB cresce con i file, I/O DB saturato da blob, `pg_dump` lento, toast pressure.
2. **Object storage (S3 o compat)**: chiave tipo `file-sources/{file_id}/{filename}`. Pro: scala, I/O dedicato, backup S3-side. Contro: due source of truth (metadati DB, blob S3), orphan possibili se una delle due fail.

Gia' in produzione esiste la stessa dicotomia in due altri domini:

- **eseguibili output** (`eseguibili-outputs/`): output di nodi eseguibili, path gia' gestito con S3 threshold.
- **IMDF bundles** (`imdf-bundles/`): bundle processati, stessa idea.

Il vincolo per file_sources nasce dal pattern "FS-4" del security audit (vedi memory `project_security_audit`): evitare di duplicare logica di dispatch per-dominio, mantenere simmetria cross-domini, e configurare la soglia via env.

## Decision

**Policy `hybrid` per-dominio, con soglia per-file di default 50 MB configurabile via env**:

- `STORAGE_POLICY=hybrid` (default): file `< threshold` → `BYTEA` in DB; file `>= threshold` → upload a object storage con key `file-sources/{file_id}/{filename}`.
- `STORAGE_POLICY=db`: tutto in DB (forza, utile per dev/test senza S3 configurato).
- `STORAGE_POLICY=object_storage`: tutto in S3 (forza, utile per deploy con DB di piccole dimensioni).

Soglia di default per file_sources: `FILE_SOURCE_S3_THRESHOLD_BYTES=52428800` (50 MB). Separata dalle soglie di altri domini (`RESULT_S3_THRESHOLD_BYTES`, `ESEGUIBILE_OUTPUT_S3_THRESHOLD_BYTES`) per permettere tuning per-dominio.

Pattern applicato: metodo `_dispatch_store(raw_data, file_id, filename)` in `app/services/file_service.py:815+` che ritorna `{data, storage_key, size_bytes}` pronto da passare a `db.save_file_source`. Caller non sa dove i bytes finiranno; decide il dispatcher leggendo policy + size.

**Orphan guard**: se l'upload object storage fallisce (exception da `storage.upload_bytes`), il dispatcher **rilancia** — nessuna row DB viene creata. Simmetricamente, per il path DB una insert fallita non lascia oggetti S3 orphan (perche' la scrittura DB avviene after-the-fact su `data` / `storage_key` gia' risolti).

## Rationale

- **50 MB e' il break-even empirico** per Postgres BYTEA. Sotto, leggere/scrivere da DB e' piu' veloce del roundtrip HTTPS a S3 + startup boto3 client. Sopra, il cost del DB (toast, replication lag, pg_dump) supera il cost S3. Valore derivato empiricamente su dataset tipici dei clienti (ICSC, RFI) + conferma con i domini gia' in prod (eseguibili, IMDF).
- **Threshold per-dominio** e non globale. File_sources hanno distribuzione di size diversa da eseguibili_outputs (output tendenzialmente piu' piccoli, input utente tendenzialmente piu' grandi). Configurare separatamente permette tuning senza regressione cross-dominio.
- **Policy override via env**: in dev e CI non vogliamo dipendere da S3. `STORAGE_POLICY=db` forza tutto in DB e i test passano su SQLite/Postgres locale senza `AWS_*`. Prod tipica: `STORAGE_POLICY=hybrid`.
- **Naming consistente S3**: `file-sources/{file_id}/{filename}` specchio di `eseguibili-outputs/{...}` e `imdf-bundles/{...}`. Un operatore che esplora il bucket capisce il layout in 5 secondi.
- **Symmetry con dispatch pattern FS-4**. Il security audit ha richiesto simmetria cross-domini per evitare bug divergenti (un dominio con threshold hard-coded, un altro con env var, un terzo senza policy). Il metodo `_dispatch_store` riusa il pattern di `result_store` e `eseguibili_output_store` (stesso shape di return, stessi branch policy).
- **Orphan guard rilancia l'exception**, non la swallowa. Il caller sa che qualcosa e' andato storto; nessuna row DB con `data=NULL, storage_key=NULL` che finge di esistere.

## Alternatives considered

- **Tutto in DB sempre** — **Rejected**. Dataset clienti hanno file singoli fino a 500 MB. BYTEA di quelle dimensioni rompe toast, rallenta `pg_dump`, e la connessione psycopg2 deve allocare buffer in memoria per un singolo fetch. Non scala.
- **Tutto in S3 sempre** — **Rejected**. File piccoli (lookup Excel 50 KB) pagherebbero un overhead S3 (client init, HTTPS roundtrip, paginate LIST) sproporzionato. Per operazioni interattive (preview file, list file sources di un progetto) la latenza impatterebbe la UX. Inoltre in dev sarebbe necessario MinIO/LocalStack configurato sempre.
- **Soglia globale unica** — **Rejected** per ragioni sopra (distribuzione size per-dominio diversa).
- **Stream-based (mai allocare bytes interi in memoria)** — **Rejected per Fase 0/1**. Tecnicamente corretto per file grandi, ma richiede refactoring di tutti i consumer a stream API. I provider `cloud_backends` espongono `get_object_stream` per la read, ma la write path (dispatcher) e' ancora `bytes`-based. Follow-up se/quando un caller reale incontrera' OOM. Non urgent in Fase 1.
- **Tabella separata `file_source_blobs`** invece di colonna `BYTEA` nel record — **Rejected**. Join in piu' senza benefit tangibile; il toast Postgres gia' separa il blob dal record principale. Overkill.

## Consequences

### Positive
- Cost ottimale per-size: piccoli in DB (veloci), grandi in S3 (scalabili).
- Config via env: stessa codebase per dev (DB only), staging (hybrid), prod (hybrid con soglia piu' alta se serve).
- Simmetria con eseguibili e IMDF → operatore familiare con un dominio conosce anche gli altri.
- Orphan guard esplicito previene inconsistency DB/S3.
- Log esplicito sul path chosen (`_logger.info(f"[FS-B1-large] File source {file_id} su object storage ({MB} MB): {storage_key}")`) — debugging banale.

### Negative
- Due possibili source per leggere il blob → `get_blob_bytes` deve branchare su `storage_key is not None`. Complessita' modesta nel read path (~10 righe), localizzata in `_get_blob_from_db` / `_get_blob_from_object_storage`.
- Rollback / recovery su S3 non e' transazionale. Se il DB rollbacka dopo un upload S3 andato, l'object S3 resta orphan. Gestito con un task cron `scripts/cleanup_orphan_s3.py` che confronta `plenora.file_sources.storage_key` con `list_objects` — fuori scope di questo ADR, tracciato come operational.
- Soglia per-dominio configurabile = piu' env var da documentare. Trade-off accettato: flessibilita' > semplicita'.

### Neutral
- Il dominio `file_sources` arriva dopo `result_store` e `eseguibili_output_store` — pattern maturo, riusato; nessuna innovazione qui. Il valore e' nella simmetria (follow-up: consolidare i tre dispatcher in un generico `StorageDispatcher` se il pattern si ripete ancora — non urgente).
- Test di boundary (file di dimensione ~soglia) in `tests2/services/test_file_service_dispatch.py`.

## References

- Source: `backend/app/services/file_service.py:815-861` — `_dispatch_store` (policy branch + upload + orphan guard)
- Source: `backend/app/services/file_service.py:863-872` — `get_blob_bytes` API pubblica
- Source: `backend/app/shared/cloud_backends.py:139+` — `AwsS3ObjectStorage.upload_bytes`
- Related: ADR-0007 (Protocol storage backends — contract che questo dispatcher consuma)
- Memory: `project_security_audit` — pattern FS-4 (symmetry cross-domini)
- Invariants: `backend/docs/architecture/invariants.md` §storage — threshold per-dominio configurabile, orphan guard
