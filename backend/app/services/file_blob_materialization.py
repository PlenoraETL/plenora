"""Blob/temp materialization helpers per file source (STRUCT-001 Seam D-D).

Estratto da `FileSourceService._get_blob_from_db` + `_temp_file_for`
senza alcun cambio comportamentale. Le 2 funzioni vivono qui con
collaborator ESPLICITI (callback) per:

  - testabilita' diretta con MagicMock (nessuna ricostruzione di
    FileSourceService);
  - go-portability: signature 1:1 con
    `func GetBlobFromDB(fileID string, expectedGroup string, deps Deps) ([]byte, error)`;
  - leaf module: zero import da `app.services.file_service` (cycle impossibile).

Fail-closed N1 PRESERVATO:
  Il check `assert_visible(file_id, expected_group_id)` resta nel
  service e viene passato come callback. Se il caller dimentica di
  passarlo, si re-attiva il fail-closed (PermissionError sul service).
  Qui le funzioni si limitano a invocarlo come prima riga sostantiva.

Side effect confinati:
  - `db_get_file_source(file_id, include_data=True|False)`: callback
    iniettata; tipicamente wrapper su `app.db.db_service.get_file_source`.
  - `get_object_storage()`: callback opzionale; tipicamente wrapper
    su `app.shared.cloud_backends.get_runtime_object_storage`. Usata
    solo per row con `storage_key` non-null.
  - `temp_dir`: directory dei temp file (tipicamente `settings.TEMP_DIR`).
  - `cleanup`: callback per rimozione (tipicamente
    `app.shared.file_cleanup.rmtree_safe`).
  - `safe_extractall`: callback per ZIP estrazione safe (tipicamente
    `app.utils.zip_safe.safe_extractall`).

Nessun raise: best-effort. Eccezioni di letture DB/storage vengono
loggate via `logger` e ritorni `None`. Lo SHP/GDB malformed cleanup
e' garantito anche su eccezione interna.
"""

from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from app.shared.exceptions import MemoryLimitExceededError
from app.core.resources.bounded_reader import read_stream_bounded

# Default cap for the storage_key bounded read. Matches
# `Settings.DEFAULTS["RESOURCE_MAX_BYTES"]`; kept local so settings
# failures do not break fail-soft blob fetch.
_DEFAULT_BLOB_READ_CAP_BYTES = 500 * 1024 * 1024


def _read_blob_cap_bytes() -> int:
    """Read the storage_key bounded-read cap from settings.

    Best-effort: any failure (settings not loaded, type error)
    falls back to the module default. The function NEVER raises;
    a degraded settings layer must not break blob fetch.
    """
    try:
        from app.config import settings
        raw = settings.get(
            "RESOURCE_MAX_BYTES", _DEFAULT_BLOB_READ_CAP_BYTES,
        )
        return int(raw) if raw is not None else _DEFAULT_BLOB_READ_CAP_BYTES
    except Exception:
        return _DEFAULT_BLOB_READ_CAP_BYTES


def get_blob_from_db(
    file_id: str,
    expected_group_id: Optional[str],
    *,
    assert_visible: Callable[[str, str], None],
    db_get_file_source: Callable[..., Optional[dict]],
    get_object_storage: Callable[[], Any],
    temp_dir: str,
    logger,
) -> Optional[bytes]:
    """Recupera blob file dal DB (BYTEA) o da S3 se `storage_key` set.

    Dispatch automatico FS-B1-large: la row DB contiene o `data` (BYTEA)
    o `storage_key` (TEXT puntato a S3). Questo helper risolve
    trasparente al caller ritornando bytes in-memory in entrambi i casi.

    N1: `expected_group_id` obbligatorio per il fail-closed check
    (delegato ad `assert_visible`). Chi chiama senza passarlo riceve
    PermissionError lanciata dal callback.

    BE-GO-READY-RESOURCE-ENGINE-006 (R-005.1 closure): il branch
    storage_key NON usa piu' un tempfile roundtrip. La precedente
    implementazione (`download(key, tmp_path)` + `read_bytes()` +
    `unlink`) e' stata sostituita con `get_object_stream(key)` +
    concatenazione dell'iterable di chunks in bytes in-memory. Questo
    elimina il roundtrip su disco (1 write + 1 read + 1 unlink),
    chiude il foot-gun di leak su crash mid-write, e si allinea alla
    bytes-first policy.

    BE-GO-READY-RESOURCE-ENGINE-011-FILE-BLOB-MATERIALIZATION-BOUNDED:
    la concatenazione e' ora **bounded** dal cap
    `RESOURCE_MAX_BYTES` (default 500 MB). Su overflow l'accumulo
    viene abortito (`MemoryLimitExceededError`) e la funzione applica il
    contratto fail-soft esistente: log error + ritorno None. Stesso
    cap che `load_dataframe_from_file` enforced sulla parse path
    a valle: tunable singolo per il budget "blob-in-memoria". Cap
    <=0 disabilita il guard (escape hatch ops, sconsigliato in
    produzione).

    Compatibilita' `temp_dir` (DEPRECATED in questo branch, KEEP_COMPAT
    in signature):
      Il parametro `temp_dir` resta nella signature per non rompere
      i caller esistenti — `temp_file_for` re-inietta lo stesso
      valore, e i wrapper `FileSourceService._get_blob_from_db`
      passano `settings.TEMP_DIR`. NESSUNA scrittura su disco
      avviene nel branch storage_key (R-005.1) e nessuna nel
      branch BYTEA (sempre stato cosi'). `temp_dir` e' consultato
      SOLO da `temp_file_for` per i write tempfile reali. Future
      cleanup stream puo' rimuovere `temp_dir` dalla signature di
      `get_blob_from_db` dopo audit dei caller (zero-blast-radius:
      i caller passano gia' `temp_dir=` come kwarg e il rimuovere
      richiede aggiornare la signature in tutti i 3 callsites
      simultaneamente).

    Object storage disabled / fetch failure / oversize: tutti
    sotto lo stesso try/except. Quando:
      - `get_object_storage()` solleva `ObjectStorageDisabledError`
        (object storage off);
      - `get_object_stream` solleva un'eccezione network/auth;
      - `read_stream_bounded` solleva `MemoryLimitExceededError`;
    il try/except esterno cattura -> `logger.error` + return None.
    Stesso contract pre-batch (caller riceve `None` e branchera'
    sul fail-soft).
    """
    assert_visible(file_id, expected_group_id or "")
    try:
        row = db_get_file_source(file_id, include_data=True)
        if not row:
            return None
        if row.get("data") is not None:
            return row["data"]
        storage_key = row.get("storage_key")
        if storage_key:
            storage = get_object_storage()
            # ObjectStorage Protocol espone `get_object_stream(key)`
            # che ritorna `{"body": Iterable[bytes], "size": int,
            # "content_type": str}`. Concateniamo i chunks in memoria
            # via `read_stream_bounded` con cap
            # `RESOURCE_MAX_BYTES` (default 500 MB). Su overflow
            # `MemoryLimitExceededError` viene catturato dal
            # try/except esterno -> log error + return None.
            resp = storage.get_object_stream(storage_key)
            cap = _read_blob_cap_bytes()
            return read_stream_bounded(
                resp["body"],
                max_bytes=cap if cap > 0 else sys.maxsize,
                label=f"file_id={file_id}",
                stage="file_blob_oversize",
            )
        return None
    except MemoryLimitExceededError as e:
        # Dedicated message: ops can grep for this signature to
        # distinguish OOM-prevention from generic fetch failures.
        logger.error(
            f"[GESTITO] Blob {file_id} oversize su storage_key: {e}. "
            f"Cap RESOURCE_MAX_BYTES limita la lettura. "
            f"Aumenta il cap o usa una API path-mode (out of scope)."
        )
        return None
    except Exception as e:
        logger.error(f"[GESTITO] Errore lettura blob {file_id}: {e}")
        return None


def temp_file_for(
    file_id: str,
    expected_group_id: Optional[str],
    *,
    assert_visible: Callable[[str, str], None],
    db_get_file_source: Callable[..., Optional[dict]],
    get_object_storage: Callable[[], Any],
    temp_dir: str,
    safe_extractall: Callable[[zipfile.ZipFile, Path], None],
    cleanup: Callable[[Any], None],
    logger,
) -> Optional[Path]:
    """Estrae il blob dal DB in un file temporaneo.

    Per SHP/GDB: estrae lo ZIP in una temp dir e ritorna il path del
    `.shp` o della `.gdb` directory. Il chiamante deve eliminare il
    file (o la dir parent per SHP/GDB) dopo l'uso.

    N1: `expected_group_id` obbligatorio per il fail-closed check.
    """
    assert_visible(file_id, expected_group_id or "")
    try:
        row_meta = db_get_file_source(file_id, include_data=False)
        # Nota: assert_visible sopra ha gia' validato il caller; il
        # blob fetch qui passa `expected_group_id` cosi' la callback
        # interna re-fail-close se mai chiamata standalone.
        blob = get_blob_from_db(
            file_id, expected_group_id,
            assert_visible=assert_visible,
            db_get_file_source=db_get_file_source,
            get_object_storage=get_object_storage,
            temp_dir=temp_dir,
            logger=logger,
        )
    except Exception as e:
        # Errore DB lato metadata o callback fallita.
        # get_blob_from_db gia' logga [GESTITO] e non raisa, qui
        # intercettiamo solo failure reali del lookup metadata.
        logger.warning(
            f"[GESTITO] _temp_file_for {file_id}: fetch metadata/blob fallito: {e}"
        )
        return None
    if not blob or not row_meta:
        return None

    fmt = row_meta.get("format", "")
    original_filename = row_meta.get("original_filename", f"{file_id}.bin")

    # Per SHP e GDB: il blob e' uno ZIP - estrai tutto in temp dir.
    # Chi riceve il path ritornato (load_dataframe / get_layers_info)
    # cleanup la tmp_dir parent dopo l'uso. Qui cleanuppiamo SOLO i
    # path errore (estrazione fallita o nessun .shp/.gdb trovato).
    if fmt in ("shp", "gdb"):
        tmp_dir = Path(tempfile.mkdtemp(dir=temp_dir))
        try:
            with zipfile.ZipFile(io.BytesIO(blob), 'r') as z:
                safe_extractall(z, tmp_dir)
            if fmt == "shp":
                shp_files = list(tmp_dir.rglob("*.shp")) + list(tmp_dir.rglob("*.SHP"))
                if shp_files:
                    return shp_files[0]
            else:
                gdb_dirs = [f for f in tmp_dir.rglob("*") if f.is_dir() and f.suffix.lower() == ".gdb"]
                if gdb_dirs:
                    return gdb_dirs[0]
        except Exception as e:
            logger.error(f"[GESTITO] Errore estrazione {fmt.upper()} zip {file_id}: {e}")
        # Cleanup tmp_dir su entrambi i path errore (exception
        # nell'estrazione, oppure zip valido ma senza .shp/.gdb
        # dentro): senza, ogni file SHP malformato accumula una
        # tmp dir nei worker.
        cleanup(tmp_dir)
        return None

    suffix = Path(original_filename).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir)
    tmp.write(blob)
    tmp.close()
    return Path(tmp.name)
