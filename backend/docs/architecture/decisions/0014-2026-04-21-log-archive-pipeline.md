# ADR-0014: Log archive pipeline — writer isolato, storage-agnostic

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

Phase 1 ha introdotto il trigger `plenora.block_log_modifications` (migration `2026-04-21_log_immutable_triggers.sql`): UPDATE vietato sempre, DELETE ammesso solo con `SET LOCAL plenora.retention_cleanup = 'true'` scoped alla transazione del beat job di retention. La retention su `plenora.logs` (30gg) e' attiva (`beat_scheduler.py:_cleanup_old_logs`), quella su `plenora.audit_actions` non esiste. Nessuno dei due dataset ha oggi un path di archive: la retention a 30gg sta cancellando righe senza backup — data loss compliance-hostile rispetto a ISO 27001 A.12.4.1 (preservazione eventi di sicurezza).

Phase 2 Hardening ha elaborato il design in `orchestrate/phase_2_hardening/a_log_lifecycle.md` (A-LOG, classi log + policy append-only + modello archive+purge), `orchestrate/phase_2_hardening/o_arc_archive_pipeline.md` (O-ARC, pipeline archive concreta) e `orchestrate/phase_2_hardening/o_arc_batch1_triage.md` (chiusura assunzioni aperte + perimetro Batch 1).

Questo ADR ratifica formalmente O-ARC **limitato al Batch 1**: writer isolato, testabile, non ancora chiamato dal runtime. L'attivazione (hook beat, env var, `validate_storage_config`, runbook) e' scope di batch successivi con commit atomici distinti. L'ADR non apre in parallelo un ADR-0013 per A-LOG: il design note resta valido come input architetturale ratificato a livello di orchestrate.

## Decision

1. **Perimetro O-ARC** (da `o_arc_archive_pipeline.md §1.1`): archive+purge esclusivamente per `plenora.logs` (retention 30gg) e `plenora.audit_actions` (retention 365gg prevista in Batch 2). `schedule_logs`, `log_runs`, `execution_logs` restano fuori per classificazione (state tables o retention gia' attiva).

2. **Path modulo**: `backend/app/services/log_archive.py`. `backend/app/workers/` non esiste; `backend/app/services/` ospita gia' logica di dominio worker-related (`orphan_cleanup_service.py`, `connection_service.py`, `task_service.py`). `backend/workers/` resta entry point Celery.

3. **Interfaccia pubblica** (bloccata per tutta la sequenza Batch 1-4):

   ```
   class ArchiveResult(TypedDict):
       archived_rows: int
       object_keys: list[str]
       cutoff_ts: datetime

   class LogArchiveWriter:
       def __init__(self, storage: ObjectStorage, key_prefix: str, batch_size: int) -> None: ...
       def archive_and_purge(
           self,
           table: Literal["logs", "audit_actions"],
           cutoff_ts: datetime,
           select_rows: Callable[[datetime, int], list[dict]],
           retention_bypass_execute: Callable[[str, tuple], None],
           run_date: datetime | None = None,
       ) -> ArchiveResult: ...
   ```

4. **SELECT + DELETE come callables iniettate**. Il writer non apre transazioni PG, non importa `psycopg2`, non importa `app.db.*`. Il bypass `SET LOCAL plenora.retention_cleanup` resta scoped al chiamante (il beat job di Batch 3). O-ARC §4.2 prescriveva solo DELETE come callable; l'estensione a SELECT deriva dalla stessa giustificazione (un solo titolare delle transazioni PG) ed e' chiusa in triage §3.2.

5. **Datetime naive rifiutato con `ValueError`**. Sia `cutoff_ts` sia qualsiasi `ts`/`timestamp` nelle righe serializzate deve avere `tzinfo != None`. La colonna DB e' TIMESTAMPTZ; un naive in arrivo e' sintomo di bug upstream e va intercettato fail-fast, non normalizzato silent.

6. **Verifica post-upload via `ObjectStorage.list_objects`**. Nessuna estensione del Protocol (`head_object`/`stat` restano fuori). Dopo ogni `upload_bytes(data, key)`, il writer chiama `list_objects(prefix=key)`, verifica che esista un entry con `entry["key"] == key` e `entry["size"] > 0`. Se non trovato o `size == 0`: il writer solleva un'eccezione esplicita con messaggio diagnosticabile (key attesa + prefix usato), **la DELETE callable non viene invocata**. Zero ambiguita' "upload silent-failed → DELETE comunque".

7. **Ordine operazioni per iterazione batch** (invariante O-ARC §4.3):
   SELECT callable → serialize NDJSON gzip → `upload_bytes` → verifica via `list_objects` → DELETE callable scoped `AND id <= max(batch_ids)` → ripeti finche' SELECT ritorna `[]`.

8. **Formato archive** (O-ARC §5): una riga NDJSON per evento DB, UTF-8, gzip stdlib. Key pattern `<key_prefix><table>/<YYYY-MM>/<table>-<YYYY-MM-DD>-<NNN>.ndjson.gz` con `NNN` zero-padded 3 cifre. Cap implicito 1000 batch/giorno (oltre serve batch_size piu' grande). Partitioning basato su `run_date` (giorno del run del job), non su `ts` riga.

9. **Colonna temporale astratta via parametro `table`**: `logs → "ts"`, `audit_actions → "timestamp"`. Nessuna migration di rinomina (O-ARC assunzione #4): il costo di uniformare lo schema supera il costo di due righe di mappa interna.

10. **Batch 1 scope minimo**: solo `backend/app/services/log_archive.py` + `backend/tests2/services/test_log_archive_writer_contract.py`. Nessun hook beat, nessuna env var in `config.py`, nessuna estensione Protocol, nessuna migration. Il writer non viene importato da alcun file di produzione fino al Batch 3.

## Rationale

- **Writer storage-agnostic + PG-agnostic**. Il dominio del modulo e' "serializza righe, uploada, coordina purge". PG e boto3 sono dettagli di infra che vivono rispettivamente nel beat (via `plenora.retention_cleanup` transaction) e nel Protocol `ObjectStorage` (ADR-0007). Iniettare i punti di contatto come callables significa (a) un solo point-of-truth per il bypass PG, (b) testabilita' totale senza fixture DB/S3, (c) portabilita' su futuri provider.

- **Verifica via `list_objects`, non `head_object`**. Estendere il Protocol e' un costo ricorrente: ogni nuovo provider deve implementare l'estensione. `list_objects(prefix=key)` e' sufficiente — S3/Azure/GCS ritornano zero o un match deterministico. Il trade-off (una round-trip HTTP in piu') e' trascurabile a frequenza 24h.

- **Upload-prima-DELETE non negoziabile**. Qualsiasi failure mode che cancelli righe DB senza object archive valido e' data loss permanente. Il Batch 1 inchioda l'invariante con contract test: `upload_bytes` solleva → DELETE mai chiamata; verifica `list_objects` fallisce → DELETE mai chiamata.

- **Naive datetime come `ValueError`**. Il DB usa ovunque TIMESTAMPTZ; una riga con ts naive nel flusso di archive e' un bug silenzioso (ISO serializzerebbe ma senza offset, il rehydrate perde informazione). Fail-fast e' coerente con charter base 8 (fail-fast su dato critico) e con la policy `execute = fail-fast` di ADR-0012.

- **Ratifica in ADR prima del codice**. Le decisioni (path modulo, SELECT callable, naive ValueError, `list_objects` verification, batch seq padding) vincolano la forma del modulo; fissarle in ADR-0014 evita drift nei commit 1a-1d e nei batch successivi.

## Alternatives considered

- **Writer apre la transazione PG da se'** con `SET LOCAL plenora.retention_cleanup = 'true'` — **Rejected**. Amplierebbe i call site del bypass da 1 (beat) a 2, contro closeout Phase 1 §9 ("non puo' diventare set-and-forget ne' escape hatch") e kickoff §5. Un solo titolare del bypass mantiene l'audit chiaro.

- **Writer riceve `db_session`/connection** anziche' callables — **Rejected**. Trascina dipendenza `app.db.*` nel modulo, costringe a mockare psycopg2 nei contract test, riproduce l'accoppiamento che l'iniezione callable elimina.

- **Estendere `ObjectStorage` Protocol con `head_object(key)`** — **Rejected**. Protocol congelato da ADR-0007; estensione richiede porting su tutti i provider presenti e futuri. `list_objects(prefix=key)` soddisfa la verifica con costo marginale.

- **Datetime naive → normalizza come UTC silent** — **Rejected**. Maschera bug upstream (fixture test con `datetime.now()` non-aware, dati migrati senza tz). L'errore esplicito rende il problema immediatamente diagnosticabile.

- **Path modulo `backend/app/workers/log_archive.py`** — **Rejected**. Creare `app/workers/` per un singolo modulo e' sprawl di convenzioni. `app/services/` e' gia' la sede di logica di dominio worker-related (es. `orphan_cleanup_service.py`).

- **Archive criptato applicativo (Fernet per riga)** — **Rejected**. SSE-S3/SSE-KMS lato bucket (gia' enforced in `AwsS3ObjectStorage.upload_bytes`, `cloud_backends.py:205`) basta al threat model ISO 27001 baseline. Crypto applicativa duplicherebbe il meccanismo e complicherebbe il rehydrate senza incremento di garanzia.

- **Compressione alternativa (zstd, lz4)** — **Rejected**. gzip stdlib, zero dipendenze nuove, ratio sufficiente per log testuali.

- **Batch seq a lunghezza variabile** (`0`, `1`, ..., `999`) — **Rejected**. Ordering lessicografico e' piu' pulito con padding fisso (`000`, `001`, ...); cap implicito 1000/giorno e' esplicito in ADR invece che emergente.

## Consequences

### Positive

- Writer testabile al 100% senza DB reale e senza S3: i contract test di Batch 1 girano in <1s su mock puri.
- Single point-of-truth per `plenora.retention_cleanup`: resta il beat job, il writer non lo vede mai.
- Il Protocol `ObjectStorage` resta invariato; retrocompat con `AwsS3ObjectStorage` esistente e con futura `MinioObjectStorage` (track `A-A1b`).
- Upload-prima-DELETE + verifica `list_objects` rendono impossibile la classe "DELETE senza backup valido".
- Naive-datetime policy esplicita chiude un'ambiguita' che altrimenti tornerebbe in ogni review.

### Negative

- Il beat job di Batch 3 dovra' passare 2 callables (SELECT + DELETE authorized) al writer: boilerplate ~10-15 righe di closure in `beat_scheduler.py`. Tradeoff accettato rispetto al riversare la conoscenza PG nel writer.
- `list_objects(prefix=key)` aggiunge un round-trip HTTP per ogni batch uploadato; a frequenza 24h e batch size 10k righe il costo e' O(1 HTTP/batch) = trascurabile.
- Il writer non emette metriche di observability ne' log applicativi a fine run in Batch 1. L'hook di logging chiusura circolare (O-ARC §6.3) entra in Batch 3 con il beat come chiamante reale.

### Neutral

- Nessuna env var nuova in Batch 1. Le 5 env var previste (`LOG_ARCHIVE_ENABLED`, `LOG_ARCHIVE_BUCKET`, `LOG_ARCHIVE_KEY_PREFIX`, `LOG_ARCHIVE_BATCH_SIZE`, `AUDIT_RETENTION_DAYS`) arrivano in Batch 2.
- Nessuna modifica al trigger `block_log_modifications` ne' allo schema `plenora.logs`/`plenora.audit_actions`. Schema congelato.
- Nessun ADR A-LOG (lifecycle) aperto qui. Il design note `orchestrate/phase_2_hardening/a_log_lifecycle.md` e' citato come input architetturale ratificato a livello di orchestrate, non come ADR formale. L'eventuale ADR A-LOG e' batch separato.

## References

- Design note: `orchestrate/phase_2_hardening/a_log_lifecycle.md` §4 (modello archive+purge)
- Design note: `orchestrate/phase_2_hardening/o_arc_archive_pipeline.md` §1 (perimetro), §3 (batch), §4 (interfaccia), §5 (formato), §6 (failure modes), §8 (no-go Batch 1)
- Design note: `orchestrate/phase_2_hardening/o_arc_batch1_triage.md` §0 (path modulo), §3 (API), §6 (rischi), §7 (checklist perimetro), §8 (split commit)
- Source (Protocol target): `backend/app/shared/cloud_backends.py:62-104` (`ObjectStorage`)
- Source (reference impl): `backend/app/shared/cloud_backends.py:139-229` (`AwsS3ObjectStorage`)
- Source (retention baseline): `backend/workers/beat_scheduler.py:858-900` (`_cleanup_old_logs` pattern)
- Source (trigger contract): `backend/migrations/2026-04-21_log_immutable_triggers.sql:52-83`
- Related: ADR-0007 (Protocol runtime_checkable — contratto `ObjectStorage`)
- Related: ADR-0008 (backup observability DB-side — pattern log applicativo esito job, applicabile in Batch 3)
- Related: ADR-0012 (preview/execute policy — fail-fast principle applicato qui al naive datetime)
- Invariants: `backend/docs/architecture/invariants.md §1.4` (logging append-only), §6 (carry-over O-ARC — update in Batch 4)

## Addendum Batch 2 (2026-04-21)

Questo addendum ratifica le decisioni di contratto env emerse dal triage implementativo `orchestrate/phase_2_hardening/o_arc_batch2_triage.md` prima dell'implementazione di Batch 2 (env var + fail-fast boot validation). **Supersede** la citazione env in §Consequences/Neutral dove diverge. Il corpo principale dell'ADR (Context, Decision, Rationale, Alternatives, Consequences) resta invariato: l'addendum integra solo i dettagli di naming/default/fail-fast aperti al momento della ratifica iniziale.

### A1. Nome canonico batch size

**`LOG_ARCHIVE_BATCH_ROWS`** e' il nome canonico della env che configura la dimensione del batch del writer. Supersede `LOG_ARCHIVE_BATCH_SIZE` citato in §Consequences/Neutral.

Motivazione:
- `backend/app/services/log_archive.py:178` (merged in Batch 1) documenta gia' `LOG_ARCHIVE_BATCH_ROWS` come nome atteso.
- "ROWS" e' non-ambiguo rispetto a "bytes": il parametro e' un conteggio di righe DB per batch, non una size in byte del payload.
- Rinominare il commento in `log_archive.py` per allinearlo a `BATCH_SIZE` sarebbe scope-creep sul modulo Batch 1 frozen.

### A2. Default `LOG_ARCHIVE_KEY_PREFIX`

Default: **`"logs-archive/"`** (literal, senza auto-derivazione di `<env>`).

Motivazione:
- O-ARC §2.4 suggeriva `logs-archive/<env>/`, ma `backend/app/config.py` non ha oggi un canale canonico per derivare `<env>` logico (prod/staging/dev) a livello di property; introdurlo accoppierebbe config e deployment topology.
- Ops override esplicito in `.env` per-environment (`LOG_ARCHIVE_KEY_PREFIX=logs-archive/prod/`) e' piu' onesto e auditable.
- Il writer (`log_archive._build_object_key` righe 206-209) valida gia' il formato (vuoto oppure endswith `"/"`); il default rispetta l'invariante.

### A3. Fail-fast bucket-distinct

Regola aggiunta a `validate_storage_config()` nel commit Batch 2b:

**`LOG_ARCHIVE_BUCKET` deve essere distinto da `BACKUP_BUCKET` e da `BACKUP_S3_BUCKET` (legacy)**. Overlap → `StorageConfigError` a boot.

Motivazione (O-ARC §5.4):
- Archive log e backup DB hanno IAM boundary diversi: il service account beat deve avere `PutObject` su archive e **niente** delete su backup (protezione contro auto-sabotaggio in caso di bug del job).
- La separazione di bucket e' l'unico enforcement tecnico al boot contro un cut-paste di `.env` che allinei i due bucket.
- Nessun probe di reachability (coerente con lo stile `validate_storage_config` esistente: sola coerenza config, mai I/O di rete).

### A4. Comportamento con `LOG_ARCHIVE_ENABLED=false`

Quando `LOG_ARCHIVE_ENABLED=false` (default), `validate_storage_config()` **salta silenziosamente** tutti i check `LOG_ARCHIVE_*`, anche se altre env `LOG_ARCHIVE_*` sono gia' settate nel `.env`.

Scelta esplicita:
- Silent pass (nessun warning) abilita il pattern "preparazione operativa": un admin scrive le env finali in `.env` con `ENABLED=false` prima del flip, senza rumore a ogni boot.
- **Asimmetria consapevole** rispetto al warning A1 cut-paste `STORAGE_POLICY=db` + bucket (`cloud_backends.py:381-386`): li' la combinazione suggerisce errore di cut-paste, qui e' un'attesa legittima pre-flip.
- Un eventuale avviso "stai per attivare archive, verifica config" sara' scope di un commit separato, non di questo addendum.

### A5. Env matrix finale ratificata

| Env var | Tipo | Default | Enforcement (boot, solo se `ENABLED=true`) |
|---|---|---|---|
| `LOG_ARCHIVE_ENABLED` | bool | `False` | — (trigger del ramo; `false` → silent pass) |
| `LOG_ARCHIVE_BUCKET` | str | unset | required, distinto da `BACKUP_BUCKET`/`BACKUP_S3_BUCKET` |
| `LOG_ARCHIVE_KEY_PREFIX` | str | `"logs-archive/"` | se settata: vuota oppure endswith `"/"` |
| `LOG_ARCHIVE_BATCH_ROWS` | int | `10000` | intero > 0 (via helper `_check_positive_int`) |
| `AUDIT_RETENTION_DAYS` | int | `365` | intero >= 0 (`0` = retention disabilitata, simmetrico a `SCHEDULE_LOGS_RETENTION_DAYS`) |

### Additional references

- Triage Batch 2: `orchestrate/phase_2_hardening/o_arc_batch2_triage.md`
- Baseline codice: `backend/app/services/log_archive.py:178` (nome canonico `LOG_ARCHIVE_BATCH_ROWS`)
- Pattern warning A1 cut-paste: `backend/app/shared/cloud_backends.py:381-386` (non-applicabile ad archive per §A4)
