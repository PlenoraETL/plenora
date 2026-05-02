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
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional


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
            # Il Protocol object storage espone `download(key, path)`,
            # quindi passiamo per temp file e leggiamo i bytes.
            tmp = tempfile.NamedTemporaryFile(delete=False, dir=temp_dir)
            tmp.close()
            tmp_path = Path(tmp.name)
            try:
                storage.download(storage_key, tmp_path)
                return tmp_path.read_bytes()
            finally:
                try:
                    tmp_path.unlink()
                except Exception as e:
                    logger.debug(
                        f"cleanup tmp S3 blob fallito per {tmp_path}: {e}"
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
