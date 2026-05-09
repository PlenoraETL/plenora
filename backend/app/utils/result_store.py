"""
PLENORA-ETL — Result Store

Helper per salvare i risultati pipeline nel DB.
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from app.core.results.retention import DEFAULT_RESULTS_RETENTION
from app.shared.resources.storage_layout import RESULTS_PREFIX
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Numero default di risultati da conservare per progetto.
# Compatibility export; implementation lives in app.core.results.retention.

__all__ = [
    "DEFAULT_RESULTS_RETENTION",
    "PipelineResultSave",
    "save_pipeline_result",
    "save_pipeline_result_outcome",
]


@dataclass(frozen=True)
class PipelineResultSave:
    """Typed result for pipeline output persistence."""

    result_id: str
    output_filename: str


def _pipeline_result_save_legacy_payload(
    outcome: PipelineResultSave,
) -> Dict[str, str]:
    """Serialize the public save_pipeline_result compatibility payload."""
    return {
        "result_id": outcome.result_id,
        "output_filename": outcome.output_filename,
    }


def get_temp_output_path(filename: str) -> Path:
    """Ritorna un path in data/temp per scrivere il file risultato."""
    from app.config import settings
    tmp_dir = Path(settings.DATA_DIR) / "temp" / f"plenora_{os.getpid()}_{os.urandom(4).hex()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / filename


def _apply_results_retention(project_id: str) -> None:
    """
    Applica la retention policy sui risultati del progetto.
    Conserva gli N risultati piu' recenti, elimina i piu' vecchi.
    N viene letto da projects.retention_n nelle config tables, default DEFAULT_RESULTS_RETENTION.
    """
    from app.core.results.retention import apply_results_retention

    apply_results_retention(project_id)


class ResultSaveError(Exception):
    """Errore fatale durante il salvataggio di un risultato pipeline.

    Sollevata da save_result_blob* quando la persistenza fallisce in modo
    non recuperabile. Casi tipici:
      - DB rifiuta l'insert (vincolo FK, connection, ecc.)
      - S3 upload fallisce (permessi, rete, bucket)
      - Retention non recuperabile post-save
      - Payload oltre `RESULT_DB_HARD_CAP_BYTES` in policy=db
    Il caller (tipicamente un task Celery) DEVE far fallire il task,
    altrimenti l'utente scaricherebbe un risultato stantio credendo
    che sia il nuovo.
    """


# Storage policy dispatch helpers (A1


def _storage_policy() -> str:
    """Policy normalizzata: db | object_storage | hybrid.
    Validata al boot da cloud_backends.validate_storage_config."""
    from app.config import settings
    return (settings.get("STORAGE_POLICY", "hybrid") or "hybrid").strip().lower()


def _result_s3_threshold() -> int:
    """Soglia hybrid policy per i result. Default tipato in
    `Settings.DEFAULTS["RESULT_S3_THRESHOLD_BYTES"]` (50 MB).

    BE-CONFIG-001B: il default vive in Settings, non piu' inline qui.
    Override env `RESULT_S3_THRESHOLD_BYTES` continua a funzionare via
    `Settings._converti_tipo_env` (type coercion `int`).
    """
    from app.config import settings
    return int(settings.get("RESULT_S3_THRESHOLD_BYTES"))


def _result_db_hard_cap() -> int:
    from app.config import settings
    return int(settings.get("RESULT_DB_HARD_CAP_BYTES", 500 * 1024 * 1024))


def _should_use_object_storage(size: int, policy: str) -> bool:
    """Dispatch policy-aware: `db` -> mai, `object_storage` -> sempre,
    `hybrid` -> sopra RESULT_S3_THRESHOLD_BYTES.

    BE-DATA-PLANE-001B: la decisione e' delegata alla pure function
    `app.shared.resources.storage_dispatch.resolve_payload_target`. Questo wrapper
    legge la threshold env-bound (`_result_s3_threshold`) e tiene
    invariata la signature legacy `(size, policy) -> bool` per i test
    esistenti che fanno `monkeypatch.setattr(result_store,
    "_should_use_object_storage", ...)`.
    """
    from app.shared.resources.storage_dispatch import resolve_payload_target
    return resolve_payload_target(
        policy=policy,
        size_bytes=size,
        threshold_bytes=_result_s3_threshold(),
    ) == "object_storage"


def _cleanup_uploaded_result_blob(
    storage,
    storage_key: str,
    *,
    reason: str,
    project_id: str,
    result_id: str,
) -> None:
    """Best-effort delete del blob orfano post-upload.

    BE-DATA-PLANE-004 (rationale): chiamato quando l'upload object
    storage e' gia' riuscito ma la `db.save_result(...)` successiva
    fallisce (l'ordine upload-prima-poi-DB e' deliberato per evitare
    row DB orfana lato opposto). Senza questo cleanup, il blob resta
    su object storage senza piu' alcuna row che lo punti -> orphan.

    `app.shared.storage_compensation.compensating_delete` per
    unificare log shape + capability handling con gli altri 3
    callsite. Mantiene la signature legacy (caller gia' ha
    `storage`); l'helper riusa l'istanza fornita ed evita un
    secondo `get_runtime_object_storage`.

    Mai solleva: il caller sta gia' propagando un altro errore (DB
    save) e non deve essere mascherato.
    """
    from app.shared.storage_compensation import compensating_delete
    compensating_delete(
        storage_key,
        domain="result",
        reason=reason,
        storage=storage,
        extras={"project_id": project_id, "result_id": result_id},
        logger=logger,
    )


def _enforce_db_hard_cap(size: int, ctx: str) -> None:
    """Fail-fast se `size` supera il hard cap DB. Applicato in policy=db
    (sempre) e come guard del ramo DB di `hybrid` (protegge da configurazioni
    in cui la soglia di dispatch e' piu' alta del cap pratico del BYTEA PG).
    """
    cap = _result_db_hard_cap()
    if size > cap:
        raise ResultSaveError(
            f"[{ctx}] Payload {size} byte supera RESULT_DB_HARD_CAP_BYTES={cap}. "
            "Alza il cap, cambia STORAGE_POLICY a object_storage/hybrid, "
            "o riduci la dimensione del result."
        )


def save_result_blob(project_id: str, path: Path, fmt: str,
                     rows_count: int = None, group_id: str = None) -> str:
    """
    Salva il risultato (file su disco) come DB blob o object storage.

    Dispatch policy-aware (A1, STORAGE_POLICY):
      - `db` -> sempre DB BYTEA, fail se size > RESULT_DB_HARD_CAP_BYTES
      - `object_storage` -> sempre object storage (streaming dal disk).
                            Nessun fallback BYTEA: capability off o bucket
                            mancante => ResultSaveError esplicito (fail-closed).
                            Validazione coerenza al boot in
                            cloud_backends.validate_storage_config.
      - `hybrid` -> threshold-based su RESULT_S3_THRESHOLD_BYTES;
                            il ramo DB e' protetto dallo stesso hard cap.
                            Manutenuto per retro-compat dev/test.

    Reader (`app.core.results.blob.get_result_data`) gestisce trasparente
    storage_key (object_storage) vs `data` BYTEA (db_legacy) — i nuovi
    result in policy=object_storage hanno sempre storage_key valorizzato
    e data=NULL. Le row legacy con data BYTEA continuano a essere lette.

    Applica la retention policy dopo il salvataggio. Elimina il file temp
    solo dopo che il blob/object e' stato scritto.

    Args:
        group_id: gruppo dell'utente proprietario del progetto.
                  Se None, viene letto dal progetto nel DB come fallback.

    Returns: result_id generato (sempre non vuoto se il metodo ritorna)

    Raises:
        ResultSaveError: se il salvataggio fallisce (DB, object storage,
            payload oltre hard cap). Il file temp viene lasciato sul
            disco per consentire retry manuale.
    """
    _saved = False
    try:
        from app.core.results import store as results_store

        # Risolvi group_id
        resolved_group_id = results_store.resolve_group_id_for_result(
            project_id,
            group_id,
        )

        result_id = str(uuid.uuid4())

        try:
            file_size = path.stat().st_size
        except OSError as _ose:
            raise ResultSaveError(f"Impossibile leggere dimensione file {path}: {_ose}")

        policy = _storage_policy()
        use_object_storage = _should_use_object_storage(file_size, policy)

        if not use_object_storage:
            # DB blob path — hard cap guard (db sempre; hybrid ramo DB
            _enforce_db_hard_cap(file_size, ctx=f"save_result_blob/{policy}")
            blob = path.read_bytes()
            results_store.save_result_row(
                result_id=result_id,
                project_id=project_id,
                group_id=resolved_group_id,
                filename=path.name,
                fmt=fmt,
                data=blob,
                rows_count=rows_count,
            )
        else:
            # Object storage path: upload streaming dal disk (no RAM load
            # Ordine: upload prima (no row DB orfana se upload fallisce
            storage_key = f"{RESULTS_PREFIX}/{project_id}/{result_id}.bin"
            try:
                from app.infrastructure.storage.cloud_backends import (
                    aws_credentials_error_message,
                    get_runtime_object_storage,
                )
                storage = get_runtime_object_storage()
                storage.upload(path, storage_key)
            except Exception as _se:
                # Nessun fallback silenzioso: se la policy/threshold dice S3
                # un fallimento upload deve far fallire il job. Differenziamo
                # solo il MESSAGGIO per dare all'operatore un'azione chiara
                # quando la causa root sono credenziali AWS scadute
                hint = aws_credentials_error_message(_se)
                if hint is not None:
                    raise ResultSaveError(
                        f"{hint} (result {result_id}, file {path.name})"
                    ) from _se
                raise ResultSaveError(
                    f"Upload object storage fallito per result {result_id} "
                    f"({path.name}): {_se}"
                ) from _se

            # BE-DATA-PLANE-004: il blob e' gia' su object storage. Se
            # la save DB fallisce, il blob resta orfano. Triggera
            # compensating delete best-effort prima di propagare l'errore
            # cosi' il caller riceve sempre la root cause (DB save) e
            # l'object storage non accumula orfani
            try:
                results_store.save_result_row(
                    result_id=result_id,
                    project_id=project_id,
                    group_id=resolved_group_id,
                    filename=path.name,
                    fmt=fmt,
                    data=None,
                    rows_count=rows_count,
                    storage_key=storage_key,
                    size_bytes=file_size,
                )
            except Exception as _db_err:
                _cleanup_uploaded_result_blob(
                    storage,
                    storage_key,
                    reason="db_save_failed_after_object_upload",
                    project_id=project_id,
                    result_id=result_id,
                )
                raise
            logger.info(
                f"[FS-4] Result {result_id} su object storage (file-based, "
                f"policy={policy}, {file_size // (1024*1024)} MB): {storage_key}"
            )

        _saved = True

        # Applica retention dopo il salvataggio
        _apply_results_retention(project_id)

        return result_id

    except ResultSaveError:
        # I rami interni (hard cap, upload S3 con credenziali AWS scadute
        # ecc.) sollevano gia' un ResultSaveError con messaggio operativo
        # specifico. Non re-wrappare con prefisso generico, altrimenti il
        # messaggio diventa "Salvataggio risultato nel DB fallito ..." anche
        # quando la causa e' upload S3 + credenziali. Simmetrico a
        # save_result_blob_from_bytes (vedi sotto, except ResultSaveError
        raise
    except Exception as e:
        # Prima era un warning silenzioso -> il task Celery non se ne accorgeva
        # e ritornava success=True pur non avendo salvato nulla, lasciando
        # l'utente a scaricare blob stantii. Ora failiamo esplicitamente
        logger.error(
            f"[CRASH] save_result_blob fallito per project_id={project_id} "
            f"file={path.name}: {e}",
            exc_info=True,
        )
        raise ResultSaveError(
            f"Salvataggio risultato nel DB fallito per {project_id}: {e}"
        ) from e
    finally:
        # Elimina il file temp SOLO se salvato con successo (DB BYTEA o S3
        # Se _saved=False il file resta in temp per ispezione/recovery manuale
        if _saved:
            try:
                if path.exists():
                    path.unlink()
                if path.parent.exists() and not any(path.parent.iterdir()):
                    path.parent.rmdir()
            except Exception as e:
                logger.warning("Cleanup file temp fallito per %s: %s", project_id, e)


def save_result_blob_from_bytes(
    project_id: str,
    data: bytes,
    filename: str,
    fmt: str,
    rows_count: int = None,
    group_id: str = None,
) -> str:
    """Salva result bytes. Dispatch policy-aware (A1, STORAGE_POLICY).

    Stessa semantica di `save_result_blob` ma input in-memory (bytes
    gia' serializzati). Vedi docstring di `save_result_blob` per i
    dettagli del dispatch e del fail-closed in policy=object_storage.

    Threshold config (solo policy=hybrid): `RESULT_S3_THRESHOLD_BYTES`
    (default 50 MB).
      - size < threshold -> DB blob (BYTEA).
      - size >= threshold -> object storage via cloud_backends. La row
        DB tiene solo metadata + storage_key; get_result_data risolve
        trasparente.

    Hard cap S3: `MAX_RESULT_S3_SIZE` (default 5 GB, limite S3 single PUT).
    Sopra il cap: ResultSaveError (file troppo grosso — da questo caso
    in su serve multipart upload, out of scope).

    Args:
        data: bytes gia' serializzati
        filename: nome visibile nel metadata DB (es. "report.csv")

    Returns: result_id generato.

    Raises: ResultSaveError se fallisce DB/S3 o size supera hard cap.
    """
    try:
        from app.core.results import store as results_store

        size = len(data)

        # Risolvi group_id
        resolved_group_id = results_store.resolve_group_id_for_result(
            project_id,
            group_id,
        )

        result_id = str(uuid.uuid4())

        policy = _storage_policy()
        use_object_storage = _should_use_object_storage(size, policy)

        if not use_object_storage:
            # DB blob path — hard cap guard (db sempre; hybrid ramo DB
            _enforce_db_hard_cap(size, ctx=f"save_result_blob_from_bytes/{policy}")
            results_store.save_result_row(
                result_id=result_id,
                project_id=project_id,
                group_id=resolved_group_id,
                filename=filename,
                fmt=fmt,
                data=data,
                rows_count=rows_count,
            )
        else:
            # Object storage path — upload prima, poi row con storage_key
            # ordine importante: no row DB orfana su upload failure
            storage_key = f"{RESULTS_PREFIX}/{project_id}/{result_id}.bin"
            try:
                from app.infrastructure.storage.cloud_backends import (
                    aws_credentials_error_message,
                    get_runtime_object_storage,
                )
                storage = get_runtime_object_storage()
                storage.upload_bytes(data, storage_key)
            except Exception as _se:
                # Nessun fallback silenzioso: se la policy/threshold dice S3
                # un fallimento upload deve far fallire il job. Differenziamo
                # solo il MESSAGGIO per dare all'operatore un'azione chiara
                # quando la causa root sono credenziali AWS scadute
                hint = aws_credentials_error_message(_se)
                if hint is not None:
                    raise ResultSaveError(
                        f"{hint} (result {result_id})"
                    ) from _se
                raise ResultSaveError(
                    f"Upload object storage fallito per result {result_id}: {_se}"
                ) from _se

            # BE-DATA-PLANE-004: vedi save_result_blob (path-based) per
            # rationale: compensating delete best-effort se la DB save
            # fallisce dopo upload riuscito
            try:
                results_store.save_result_row(
                    result_id=result_id,
                    project_id=project_id,
                    group_id=resolved_group_id,
                    filename=filename,
                    fmt=fmt,
                    data=None,
                    rows_count=rows_count,
                    storage_key=storage_key,
                    size_bytes=size,
                )
            except Exception as _db_err:
                _cleanup_uploaded_result_blob(
                    storage,
                    storage_key,
                    reason="db_save_failed_after_object_upload",
                    project_id=project_id,
                    result_id=result_id,
                )
                raise
            logger.info(
                f"[FS-4] Result {result_id} su object storage "
                f"(policy={policy}, {size // (1024*1024)} MB): {storage_key}"
            )

        # Retention post-save (stesso pattern del path-based
        _apply_results_retention(project_id)

        return result_id

    except ResultSaveError:
        raise
    except Exception as e:
        logger.error(
            f"[CRASH] save_result_blob_from_bytes fallito per project_id={project_id} "
            f"filename={filename}: {e}",
            exc_info=True,
        )
        raise ResultSaveError(
            f"Salvataggio risultato in-memory fallito per {project_id}: {e}"
        ) from e


def save_pipeline_result(
    project_id: str,
    df,
    output_format: str,
    filename_stem: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> Dict[str, str]:
    """Compatibility wrapper returning legacy dict result metadata."""
    outcome = save_pipeline_result_outcome(
        project_id=project_id,
        df=df,
        output_format=output_format,
        filename_stem=filename_stem,
        rows_count=rows_count,
        group_id=group_id,
        geom_column=geom_column,
        crs=crs,
    )
    return _pipeline_result_save_legacy_payload(outcome)


def save_pipeline_result_outcome(
    project_id: str,
    df,
    output_format: str,
    filename_stem: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> PipelineResultSave:
    """Salva un DataFrame come result pipeline. Un solo entry point.

    Dispatch interno:
      - formati in-memory-safe (csv/xlsx/json/parquet/geojson):
        save_dataframe_to_bytes + save_result_blob_from_bytes. Nessun
        file creato o toccato su DATA_DIR/temp.
      - formati filesystem-only (gpkg/shp): save_dataframe crea un
        tempfile in TemporaryDirectory (OS /tmp), save_result_blob
        lo persiste (DB o S3 in base alla size), poi unlink garantito
        dal context manager. Il file esiste transitoriamente
        (millisecondi tipicamente).

    Storage finale (dispatch delegato a save_result_blob*):
      - size < RESULT_S3_THRESHOLD_BYTES -> DB BYTEA (transazionale)
      - size >= threshold -> S3 via cloud_backends (auto-chunking)
    I caller vedono la stessa API (ritorna result_id opaco); il reader
    get_result_data risolve trasparente.

    Disk-write residue (documentate, NON eliminate):
      - gpkg: driver GDAL/OGR richiede un path, non accetta BytesIO.
        Non eliminabile senza cambiare libreria.
      - shp: multi-file (shp+shx+dbf+prj+cpg) zippati in .zip. OGR
        genera file-by-file, il packaging in zip e' fatto da
        `save_dataframe._salva_formato_geo`. Non eliminabile.
      - Streaming ESTRAI csv/parquet: NON passa da qui (path dedicato
        in `query_executor._execute_streaming_to_file`). Residuo su
        tempfile legittimo: chunk-by-chunk da cursor DB, evita OOM per
        result set grandi.

    Callers (ESTRAI non-streaming/MANIPOLA/ARRICCHISCI/features/
    enrichment non-eseguibili) chiamano SOLO questa funzione. Niente
    if/else duplicato caller-side.

    Args:
        filename_stem: nome SENZA estensione (es. "report"). L'estensione
            viene derivata da output_format.

    Returns:
        typed result save outcome.
    """
    from app.core.files.dataframe_io import (
        save_dataframe, save_dataframe_to_bytes,
    )

    fmt_lower = (output_format or "").lower()
    filename = f"{filename_stem}.{fmt_lower}"

    if fmt_lower in ("gpkg", "shp"):
        # Filesystem-only: serializzatori che richiedono path
        # driver OGR per gpkg, multi-file zip per shp
        # Cleanup hardening: TemporaryDirectory context manager garantisce
        # rmtree al `__exit__` indipendentemente da exception path.
        # La root resta sotto DATA_DIR/temp: su Windows sandbox l'OS temp
        # puo' essere non scrivibile/cancellabile dal processo test.
        # Upload via save_result_blob_from_bytes (bytes-based, no
        # cleanup sovrapposto al nostro).
        import shutil
        _settings = globals().get("settings")
        if _settings is None:
            from app.config import settings as _settings
        temp_parent = Path(_settings.DATA_DIR) / "temp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = temp_parent / f"urby_gpkg_shp_{os.getpid()}_{os.urandom(4).hex()}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        try:
            tmp_path = Path(tmp_dir) / filename
            saved_path = save_dataframe(df, tmp_path, output_format, geom_column, crs)
            # Per shp `save_dataframe` ritorna .zip invece di .shp — allinea
            actual_fmt = saved_path.suffix.lstrip(".") if fmt_lower == "shp" else fmt_lower
            data = saved_path.read_bytes()
            result_id = save_result_blob_from_bytes(
                project_id=project_id,
                data=data,
                filename=saved_path.name,
                fmt=actual_fmt,
                rows_count=rows_count,
                group_id=group_id,
            )
            output_filename = saved_path.name
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        # tmp_dir + contenuto rimossi best-effort (idempotent, no-throw)
        return PipelineResultSave(
            result_id=result_id,
            output_filename=output_filename,
        )

    # In-memory path: zero touch su DATA_DIR/temp
    data = save_dataframe_to_bytes(df, fmt_lower, geom_column=geom_column, crs=crs)
    result_id = save_result_blob_from_bytes(
        project_id=project_id,
        data=data,
        filename=filename,
        fmt=fmt_lower,
        rows_count=rows_count,
        group_id=group_id,
    )
    return PipelineResultSave(result_id=result_id, output_filename=filename)


def get_result_as_bytes(project_id: str) -> Optional[bytes]:
    """Equivalente in-memory di `get_result_as_tempfile`: ritorna bytes direttamente.

    Usato per leggere l'ultimo result di un project_source senza
    materializzarlo su disco. Il caller decide come parsarlo
    (pd.read_csv(BytesIO(data)), pd.read_parquet(BytesIO(data)), ecc.).

    Il backend storage (DB BYTEA vs S3 object) e' trasparente: la
    risoluzione avviene in db.get_result_data che scarica da S3 se
    storage_key e' presente sulla row.

    Returns: bytes del result, o None se il project non ha result
    (o se il download S3 fallisce — log warning in quel caso).
    """
    try:
        from app.core.results import store as results_store

        row = results_store.get_latest_result_row(project_id)
        if not row:
            return None
        if not row.id:
            return None
        return results_store.get_result_data(row.id)
    except Exception as e:
        logger.warning(f"Errore lettura blob result ({project_id}): {e}")
        return None


def get_result_as_tempfile(project_id: str) -> Optional[Path]:
    """
    Recupera l'ultimo risultato dal DB ed estrae in un file temp.
    Il chiamante deve eliminare il file dopo l'uso.

    Returns: Path del file temp, o None se non trovato
    """
    try:
        from app.core.results import store as results_store

        row = results_store.get_latest_result_row(project_id)
        if not row:
            return None

        if not row.id:
            return None

        data = results_store.get_result_data(row.id)
        if not data:
            return None

        suffix = f".{row.format or 'csv'}"
        from app.config import settings
        tmp_dir = Path(settings.DATA_DIR) / "temp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"plenora_cached_{os.urandom(4).hex()}{suffix}"
        tmp_path.write_bytes(data)
        return tmp_path
    except Exception as e:
        logger.warning(f"Errore lettura risultato dal DB ({project_id}): {e}")
        return None


def get_result_info(project_id: str) -> Optional[dict]:
    """Ritorna metadata dell'ultimo risultato (senza blob)."""
    try:
        from app.core.results import store as results_store

        return results_store.get_latest_result(project_id)
    except Exception as e:
        logger.warning("get_result_info fallito per %s: %s", project_id, e)
        return None
