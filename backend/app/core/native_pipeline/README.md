# `app.core.native_pipeline` — percorso nativo sidecar (Arrow IPC)

Percorso **esplicito e parallelo** che esegue una catena
`PostgreSQL read -> Data plan -> IO convert` delegando a tre binari Rust
qualificati, invocati come sottoprocessi con handoff su file Arrow IPC.

Il runner nativo è **Linux/POSIX-only**. Su Windows fallisce prima del primo
spawn: senza process group POSIX non può garantire la terminazione dell'intero
albero e non offre una modalità degradata.

Questo package **non sostituisce** il percorso legacy pandas e non lo modifica.
I due percorsi non si chiamano a vicenda e **non esiste fallback** in nessuna
delle due direzioni: una capability non supportata è un errore, non un
instradamento alternativo.

> **Nessun claim di parità completa.** Il package copre le tre capability
> elencate sotto e nient'altro. Il percorso nativo preserva schema/metadata
> Arrow e `plenora-row-diagnostics-v1`; il percorso legacy pandas resta
> separato e non acquisisce queste proprietà.

## Capability

Sorgente di verità: `capabilities.py`. Ispezionabile a runtime con
`python -m app.core.native_pipeline capabilities`.

| Capability | Stato | Motivo |
|---|---|---|
| `postgres.read` | supportata | `plenora-database v1.1.0` espone `postgres-read-ipc` |
| `data.plan.apply` | supportata | `plenora-data-tools v1.0.2` espone `run --plan` |
| `io.convert` | supportata | `plenora-IO-tools v1.0.1` espone `convert` |
| `mysql.read` | **non supportata** | nessuna CLI pubblica MySQL export/read nella release qualificata |
| `sqlserver.read` | **non supportata** | nessuna CLI pubblica SQL Server read |
| `oracle.read` | **non supportata** | nessun provider Oracle nelle release qualificate |
| `postgres.write`, `mysql.write`, `sqlserver.write`, `oracle.write` | **non supportate** | nessuna CLI pubblica di scrittura: le semantiche `affected_rows` row-scoped non sono dimostrabili da questo lato del boundary |

Una capability non supportata solleva `UnsupportedCapability` **prima di
qualsiasi spawn**: nessun processo viene creato, nemmeno il probe di identità.

## API pubblica

```python
from app.core.native_pipeline import (
    NativePipelineRequest, NativeToolchainConfig, TableReadSpec,
    run_native_pipeline,
)

result = run_native_pipeline(
    NativePipelineRequest(
        source=TableReadSpec(
            dsn_env_var="PLENORA_PG_DSN",   # nome della variabile, non il DSN
            schema="public",
            table="misure",
            max_rows=1_000_000,
            max_output_bytes=512 * 1024 * 1024,
            order_by="id",
        ),
        plan_path=Path("plan.json"),
        output_path=Path("/artifacts/out.arrow"),
    ),
    NativeToolchainConfig.from_settings(),
)
```

`result` è un `NativePipelineResult`: percorso dell'artifact, dimensione,
report per stage, `loss` dichiarata dallo stage IO, diagnostica strutturata e
versioni dimostrate della toolchain. **Nessun dataframe** attraversa il
boundary.

Errori tipizzati (tutti sottoclassi di `NativePipelineError`, tutti con `code`
machine-readable): `NativePipelineDisabled`, `UnsupportedCapability`,
`InvalidNativeRequest`, `OutputAlreadyExists`, `ToolchainIdentityError`,
`ProcessSpawnFailed`, `StageTimeout`, `StageStreamOverflow`, `StageFailed`,
`DataPlanRejected`, `MalformedStageOutput`, `NativePlatformUnsupported`,
`WorkspaceCleanupFailed`.

## CLI

```bash
python -m app.core.native_pipeline capabilities
python -m app.core.native_pipeline run \
    --dsn-env PLENORA_PG_DSN \
    --schema public --table misure \
    --plan plan.json --output out.arrow \
    --max-rows 1000000 --order-by id
```

Un solo documento JSON su stdout: `{"status":"ok","result":{...}}` con exit 0,
oppure `{"status":"error","error":{"code":...}}` con exit non-zero. Il DSN non
compare mai in argv né in output.

## Configurazione dei binari pinnati

Ogni binario si configura come array JSON contenente **esattamente un path
assoluto** a un file regular non-symlink. Wrapper, argomenti prefix, path
relativi e lookup dell'eseguibile su `PATH` sono rifiutati. Vuoto = non
configurato = run bloccato prima di ogni spawn.

| Variabile | Default | Note |
|---|---|---|
| `NATIVE_PIPELINE_ENABLED` | `false` | opt-in esplicito; default fail-closed |
| `NATIVE_PIPELINE_DATABASE_ARGV` | *(vuoto)* | es. `["/opt/plenora/bin/plenora-database"]` |
| `NATIVE_PIPELINE_DATA_ARGV` | *(vuoto)* | es. `["/opt/plenora/bin/plenora-data-tools"]` |
| `NATIVE_PIPELINE_IO_ARGV` | *(vuoto)* | es. `["/opt/plenora/bin/plenora-io"]` |
| `NATIVE_PIPELINE_DATABASE_IDENTITY_MODE` / `_IDENTITY_PATH` / `_SHA256` | `sha256` / *(vuoto)* / *(vuoto)* | Database v1.1.0 non espone `--version`; path e digest sono obbligatori quando enabled |
| `NATIVE_PIPELINE_DATABASE_IDENTITY` / `_VERSION` | `plenora-database` / `1.1.0` | label/versione release riportate dopo prova digest |
| `NATIVE_PIPELINE_DATA_IDENTITY_MODE` / `_IDENTITY_PATH` / `_SHA256` | `sha256` / *(vuoto)* / *(vuoto)* | path e digest sono obbligatori; version mode è override opzionale |
| `NATIVE_PIPELINE_DATA_IDENTITY` / `_VERSION` | `plenora-data-tools` / `1.0.2` | label/versione release riportate dopo prova digest |
| `NATIVE_PIPELINE_IO_IDENTITY_MODE` / `_IDENTITY_PATH` / `_SHA256` | `sha256` / *(vuoto)* / *(vuoto)* | IO v1.0.1 emette solo status/version; path e digest sono obbligatori |
| `NATIVE_PIPELINE_IO_IDENTITY` / `_VERSION` | `plenora-io` / `1.0.1` | label/versione release riportate dopo prova digest |
| `NATIVE_PIPELINE_STAGE_TIMEOUT_MS` | `300000` | budget wall-clock per stage |
| `NATIVE_PIPELINE_IDENTITY_TIMEOUT_MS` | `10000` | budget del probe `--version` |
| `NATIVE_PIPELINE_MAX_STDOUT_BYTES` | `1048576` | bound su stdout catturato |
| `NATIVE_PIPELINE_MAX_STDERR_BYTES` | `262144` | bound su stderr catturato |
| `NATIVE_PIPELINE_MAX_REPORT_BYTES` | `262144` | bound sul documento JSON di report |
| `NATIVE_PIPELINE_MAX_BINARY_BYTES` | `536870912` | bound per eseguibile materializzato |
| `NATIVE_PIPELINE_MAX_STAGE_ARTIFACT_BYTES` | `536870912` | bound 512 MiB per artifact Arrow |
| `NATIVE_PIPELINE_MAX_PLAN_BYTES` | `16777216` | bound sullo snapshot del piano |
| `NATIVE_PIPELINE_WORKSPACE_DIR` | *(vuoto)* | radice dei workspace isolati; vuoto = temp di processo |

Un valore malformato (`NATIVE_PIPELINE_*_ARGV` che non è un array JSON di
stringhe non vuote) solleva `ValueError` alla costruzione della config: una
toolchain mezza dichiarata non degrada mai in "esegui quello che trovi".

### Verifica di identità

Prima del primo subprocess il piano e i tre eseguibili vengono copiati con
`O_NOFOLLOW`, bound, mode privato e `fsync` nel workspace. Probe e stage usano
solo questi snapshot. In mode SHA il path di identità originario coincide con
l'unico path argv; dopo la copia digest e argv puntano allo stesso snapshot.

Mode `sha256` è il default per tutti e tre i binari: non spawna probe,
legge a chunk lo snapshot e confronta in constant time il digest configurato.
Database v1.1.0 non espone versione e IO v1.0.1 non emette identity; Data può
usare `version` soltanto come override esplicito, che richiede sempre
identity+versione. Tutti i pin sono prevalidati prima del primo probe;
path/digest mancanti, file illeggibile, mismatch, exit/shape/versione inattesi,
timeout o overflow fermano la catena prima che muova dati.

## Invarianti di esecuzione

- argv sempre come lista, `shell=False`, nessuna interpolazione;
- ambiente figlio costruito da allowlist (`DEFAULT_ENV_ALLOWLIST`), inclusi
  `LD_LIBRARY_PATH` e trust store SSL necessari ai binari; `TMPDIR`/`TEMP`/`TMP`
  sono sempre riscritti sullo scratch isolato del run;
- **il DSN è passato per nome**: il valore raggiunge solo l'ambiente del figlio
  dello stage database, mai argv, mai gli altri stage, mai i probe;
- `stdin` è `DEVNULL`; stdout/stderr drenati su thread dedicati con bound rigido
  (sforamento = `StageStreamOverflow`, processo ucciso);
- timeout per stage con kill dell'intero process group noto, eseguito sempre
  anche se il leader è già terminato con exit 0;
- ogni stage scrive dentro un workspace temporaneo privato, rimosso su ogni
  percorso di uscita;
- **no-clobber**: l'artifact è copiato mode `0600` nella directory di
  destinazione; solo dopo cleanup riuscito del workspace viene pubblicato con
  `os.link(staged, destination)`. `FileExistsError` non sostituisce né cancella
  il concorrente; la directory è sincronizzata con `fsync` su POSIX;
- su qualsiasi fallimento non viene pubblicato nulla;
- redaction dei segreti applicata al testo grezzo **prima** del parsing, quindi
  report, diagnostica, messaggi d'errore e output CLI la ereditano.

## Contratto di report atteso dai binari

Ogni stage può emettere su stdout **un singolo oggetto JSON** (oppure niente).
`NaN` e le forme Infinity non sono JSON e vengono rifiutate.
Il parser accetta due tentativi: l'intero stream, poi la sua ultima riga non
vuota (il caso "log di progresso seguiti dal report"). Qualsiasi altra forma è
`MalformedStageOutput`.

Chiavi lette, tutte opzionali:

- `diagnostics`: array di oggetti; raccolte da tutti gli stage e riportate in
  `result.diagnostics`;
- `row_diagnostics`: documento aggregato, top-level o sotto `error`, validato
  integralmente come `plenora-row-diagnostics-v1`, `complete`, indici source
  zero-based, totali/conteggi coerenti ed esempi row-scoped validi;
- `loss` (solo stage IO): oggetto `{"declared": bool, "entries": [...]}` oppure
  array di entry.

Exit code `2` dello stage Data è una **rejection contrattuale**: solleva
`DataPlanRejected` con la diagnostica strutturata, non pubblica nulla e non
esegue lo stage IO. Una rejection `invalid_plan` senza row diagnostics può
restare una rejection; un envelope parsabile `data_mapping` senza diagnostica
completa è `MalformedStageOutput` fail-closed.

Il contenuto degli artifact non viene interpretato: la catena è un handoff di
file, il che è anche ciò che tiene lo schema Arrow fuori dalla portata di
pandas.

La qualification non si esaurisce nei fake: prima di abilitare il path è
obbligatorio eseguire la lane live fuori dalla suite default con le release
pinnate e PostgreSQL reale, verificando happy path byte-identico e rejection
Data con diagnostica completa/no output. Le fixture DB/live restano opt-in e
sono avviate dal verifier, non dalla suite locale.

## Test

`backend/tests2/native_pipeline/` — i test usano tre eseguibili fake reali
(processi veri, non mock).

```bash
cd backend
python -m pytest tests2/native_pipeline -q
```

Su host Windows eseguire la suite nel container backend o in WSL/Linux: il
runner rifiuta Windows prima dello spawn perché richiede process group POSIX.
