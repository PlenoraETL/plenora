"""Upload multi-layer/multi-sheet (STRUCT-001 Seam D-G).

Estratto da `FileSourceService.create_multi_layer_from_upload` senza
alcun cambio comportamentale. La funzione standalone accetta tutti
i collaborator come kwargs espliciti per:

  - testabilita' diretta (no FileSourceService.__new__);
  - go-portability: signature 1:1 con
    `func CreateMultiLayerFromUpload(content io.Reader, filename, name string, layersCfg []LayerCfg, opts Opts) ([]FileSource, error)`;
  - leaf module: zero import da `app.services.file_service` (cycle impossibile).

Use case: upload di un file multi-layer (GPKG/GDB) o multi-sheet (XLSX)
che genera N FileSource (uno per layer/sheet). Tutti condividono lo
stesso blob; differiscono in `layer`/`sheet`/`columns`.

Invarianti preservate (test-blindabili):
  1. layers_config vuoto/None -> ValueError immediato, no side effect.
  2. MIME validation defense-in-depth SEMPRE attiva (default lazy
     import del validator se mime_validate=None).
  3. ZIP unwrap target SOLO `.gpkg` / `.gdb` (NO SHP/CSV come
     create_from_upload). Per .gdb, `tmp_path.read_bytes()` su
     directory solleverebbe IsADirectoryError - bug latente
     pre-esistente NON fixato in questo seam (preservato verbatim).
  4. Loop per-layer: se parse fallisce per UN layer, logger.warning +
     columns=[]/row_count=0 PER QUEL layer, il loop CONTINUA sui
     successivi.
  5. dispatch_blob_storage: chiamato 1 volta PRIMA del loop con
     `storage_key_id=batch_id` (BE-HARDEN-001C E1 - shared blob).
     db_save_file_source: chiamato N volte (uno per layer) con i
     `shared_blob_kwargs` propagati. Atomicita' batch NON garantita -
     se db_save fallisce al layer K, i K-1 precedenti restano
     persistiti (E3 affrontera' la transazione batch).
  6. Cleanup asimmetrico (preservato verbatim):
       - tmp file: cleanup_file (in produzione = os.unlink)
       - zip_tmp_dir: cleanup_dir (in produzione = rmtree_safe)
  7. group_id or default_group propagato coerente su DTO + DB save.
  8. blob_kwargs propagato 1:1 a db_save_file_source via **kwargs.
  9. is_sheet_based dispatch: XLSX -> layer arg = None, sheet = valore;
     GPKG/GDB -> layer arg = valore, sheet = None. DTO field analogo.

Side effect confinati (tutti via callback iniettata):
  - generate_file_id, time_now, detect_format
  - load_by_format, column_builder_from_df
  - dispatch_blob_storage, db_save_file_source
  - build_file_source (DTO factory)
  - cleanup_dir (rmtree_safe), cleanup_file (os.unlink)
  - safe_extractall, zip_slip_error type
  - mime_validate (default lazy import del validator)
"""

from __future__ import annotations

import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Tuple


def _emit_partial_batch_failure_log(
    logger,
    created: List[Any],
    expected_count: int,
    failed_layer: Optional[str],
    failed_phase: Optional[str],
) -> None:
    """BE-HARDEN-001A: emette `partial_batch_failure` log strutturato
    SOLO se il loop si e' interrotto DOPO almeno un save riuscito (e
    PRIMA del completamento del batch). Pre-loop failure (MIME block /
    BadZipFile / ZipSlipError) e first-layer failure NON triggerano il
    log perche' `created_count == 0`. Full success NON triggera (mai
    chiamata: il loop completa senza except).
    """
    created_count = len(created)
    if 0 < created_count < expected_count:
        logger.warning(
            f"event=partial_batch_failure "
            f"expected_count={expected_count} "
            f"created_count={created_count} "
            f"failed_layer={failed_layer!r} "
            f"failed_phase={failed_phase}"
        )


def create_multi_layer_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    layers_config: Optional[List[Dict[str, Any]]] = None,
    srid: Optional[int] = None,
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    detect_format: Callable[[str], str],
    load_by_format: Callable[..., Tuple[Any, Optional[Any]]],
    column_builder_from_df: Callable[..., List[Any]],
    dispatch_blob_storage: Callable[..., dict],
    db_save_file_source: Callable[..., None],
    build_file_source: Callable[..., Any],
    cleanup_dir: Callable[[Any], None],
    cleanup_file: Callable[[Any], None],
    safe_extractall: Callable[..., None],
    zip_slip_error: type,
    temp_dir: str,
    default_group: str,
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> List[Any]:
    """Crea N FileSource da un file multi-layer (GPKG/GDB) o multi-sheet (XLSX).

    Vedi docstring del modulo per invarianti e collaborator.

    Args:
        layers_config: lista di dict con chiavi opzionali:
            - "layer": nome layer (per GPKG/GDB);
            - "sheet": nome sheet (per XLSX);
            - "selected_columns": lista colonne da importare (opzionale).

    Returns:
        Lista di FileSource costruiti via build_file_source (uno per layer
        configurato; il numero corrisponde a len(layers_config) ECCETTO
        in caso di propagazione exception, dove i precedenti sono comunque
        persistiti su DB ma la lista ritornata e' parziale o non ritornata).

    Raises:
        ValueError: layers_config vuoto/None, MIME block, ZIP malformato
                    o path traversal.
        Exception:  dispatch_blob_storage / db_save_file_source propagano
                    la loro eccezione (atomicita' batch non garantita).
    """
    # MIME validator default: lazy import per preservare "sempre attivo"
    # del comportamento originale (NON opzionale come create_from_upload).
    if mime_validate is None:
        from app.utils.mime_validator import validate_no_executable
        mime_validate = validate_no_executable

    if not layers_config:
        raise ValueError("layers_config non puo essere vuota")

    file_content.seek(0)
    raw_data = file_content.read()

    # MIME defense-in-depth: PRIMA di qualsiasi side effect.
    try:
        mime_validate(content=raw_data, filename=filename or name)
    except Exception as e:
        from app.utils.mime_validator import MimeMismatchError
        if isinstance(e, MimeMismatchError):
            logger.warning(
                f"create_multi_layer_from_upload bloccato (MIME): "
                f"name={name!r} filename={filename!r}: {e}"
            )
            raise ValueError(str(e)) from e
        raise

    suffix = Path(filename).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir)
    tmp.write(raw_data)
    tmp.close()
    tmp_path = Path(tmp.name)

    zip_tmp_dir: Optional[Path] = None
    zip_raw_data = raw_data  # MULTI-GDB-001: conserva ZIP originale per GDB
    format_type = detect_format(filename)

    try:
        if format_type == 'zip':
            zip_tmp_dir = Path(tempfile.mkdtemp(dir=temp_dir))
            try:
                with zipfile.ZipFile(tmp_path, 'r') as z:
                    safe_extractall(z, zip_tmp_dir)
            except zipfile.BadZipFile as e:
                raise ValueError(f"File ZIP non valido o corrotto: {e}") from e
            except zip_slip_error as e:
                raise ValueError(f"File ZIP rifiutato (path traversal): {e}") from e
            extracted_files = [
                f for f in zip_tmp_dir.rglob("*")
                if f.is_file() or (f.is_dir() and f.suffix.lower() == ".gdb")
            ]
            # NB: SOLO .gpkg / .gdb (NO SHP/CSV come create_from_upload).
            for ext in ['.gpkg', '.gdb']:
                match = next(
                    (f for f in extracted_files if f.suffix.lower() == ext), None,
                )
                if match:
                    tmp_path = match
                    format_type = detect_format(match.name)
                    filename = match.name
                    # MULTI-GDB-001 fix: per .gdb (directory) `read_bytes()`
                    # solleverebbe IsADirectoryError. Conserviamo il ZIP
                    # originale come blob (analogo a create_from_upload).
                    # Per .gpkg (file), leggiamo i bytes del path estratto.
                    if format_type == 'gdb':
                        raw_data = zip_raw_data
                    else:
                        raw_data = tmp_path.read_bytes()
                    break

        # Determina se stiamo lavorando con layer (gpkg/gdb) o sheet (xlsx)
        is_sheet_based = format_type in ['xlsx', 'xls']

        created: List[Any] = []
        now = time_now()

        # BE-HARDEN-001A: tracking osservabilita' per partial batch failure.
        # NON cambia behavior (exception propaga come prima); solo emette
        # log strutturato `partial_batch_failure` se il loop si interrompe
        # DOPO almeno un save riuscito.
        #
        # Post BE-HARDEN-001C E1: dispatch e' UNA volta pre-loop, quindi
        # un dispatch fail produce sempre `created=0` -> log NON emesso
        # (condizione 0 < created < expected non soddisfatta). Il
        # `failed_phase` resta solo "db_save".
        expected_count = len(layers_config)
        last_failed_layer: Optional[str] = None
        last_failed_phase: Optional[str] = None  # "db_save"

        # BE-HARDEN-001C E1 - shared blob dispatch:
        # 1 chiamata a dispatch_blob_storage PRIMA del loop con
        # `storage_key_id=batch_id`. Le N row in object_storage path
        # condividono lo stesso storage_key (delete safe grazie a E2
        # reference-aware delete in `db.delete_file_source`).
        # In BYTEA path, le N row hanno lo stesso `data` bytes
        # (5a, comportamento BYTEA invariato).
        # Failure pre-loop -> 0 db_save chiamati (orphan guard).
        batch_id = f"batch_{uuid.uuid4().hex}"
        shared_blob_kwargs = dispatch_blob_storage(
            batch_id, raw_data, filename,
            storage_key_id=batch_id,
        )

        for layer_cfg in layers_config:
            # Supporta sia {"layer": "nome"} che {"sheet": "nome"}
            layer = layer_cfg.get("layer") or layer_cfg.get("sheet")
            selected_columns = layer_cfg.get("selected_columns")

            safe_layer = layer.replace(" ", "_").replace("-", "_") if layer else "default"
            file_source_name = f"{name}_{safe_layer}"
            file_id = generate_file_id(file_source_name)

            # Inferenza colonne / row_count / CRS - BEST EFFORT PER-LAYER.
            # Se fallisce, logger.warning + columns=[] / row_count=0 / crs=None,
            # il loop CONTINUA sui successivi layer (preservato verbatim).
            columns: List[Any] = []
            row_count = 0
            crs: Optional[str] = None
            try:
                if is_sheet_based:
                    df, gdf = load_by_format(tmp_path, format_type, None, layer)
                else:
                    df, gdf = load_by_format(tmp_path, format_type, layer, None)
                if selected_columns:
                    # Filter inline: 3 LOC pure, evita callback dedicato.
                    valid_cols = [c for c in selected_columns if c in df.columns]
                    if valid_cols:
                        df = df[valid_cols]
                columns = column_builder_from_df(df, gdf, srid)
                row_count = len(df)
                if gdf is not None and hasattr(gdf, 'crs') and gdf.crs:
                    epsg = gdf.crs.to_epsg()
                    if epsg:
                        crs = f"EPSG:{epsg}"
                    else:
                        auth = gdf.crs.to_authority()
                        crs = f"{auth[0]}:{auth[1]}" if auth else str(gdf.crs)
                elif srid:
                    crs = f"EPSG:{srid}"
            except Exception as e:
                logger.warning(f"Errore inferenza colonne layer '{layer}': {e}")

            columns_dicts = [
                {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
                for c in columns
            ]

            file_source = build_file_source(
                id=file_id,
                name=file_source_name,
                description=description,
                original_filename=filename,
                format=format_type,
                layer=None if is_sheet_based else layer,
                sheet=layer if is_sheet_based else None,
                columns=columns_dicts,
                row_count=row_count,
                file_size=len(raw_data),
                crs=crs,
                group_id=group_id or default_group,
                created_at=now,
                updated_at=now,
            )

            metadata = (
                file_source.model_dump(mode='json')
                if hasattr(file_source, 'model_dump')
                else {}
            )

            # BE-HARDEN-001C E1: dispatch fatto UNA volta pre-loop. Qui
            # ogni layer chiama solo db_save_file_source con i kwargs
            # condivisi (`shared_blob_kwargs`). In object_storage path
            # tutte le N row puntano allo stesso storage_key (delete
            # safe via E2 reference-aware delete). In BYTEA path tutte
            # le N row hanno gli stessi `data` bytes (5a, invariato).
            #
            # Atomicita' batch resta NON garantita: se db_save fallisce
            # al layer K, i K-1 precedenti restano persistiti -> E3 lo
            # affrontera' in transaction. BE-HARDEN-001A: tracciamo
            # `last_failed_layer`/`last_failed_phase` PRIMA di rilanciare,
            # cosi' il log `partial_batch_failure` viene emesso se
            # created > 0.
            try:
                db_save_file_source(
                    file_id=file_id,
                    group_id=group_id or default_group,
                    name=file_source_name,
                    original_filename=filename,
                    fmt=format_type,
                    metadata=metadata,
                    **shared_blob_kwargs,
                )
            except Exception:
                last_failed_layer = layer
                last_failed_phase = "db_save"
                _emit_partial_batch_failure_log(
                    logger, created, expected_count,
                    last_failed_layer, last_failed_phase,
                )
                raise

            logger.info(
                f"FileSource multi-layer creato: {file_id} layer='{layer}'"
            )
            created.append(file_source)

        return created

    finally:
        # Cleanup ASIMMETRICO (preservato verbatim):
        # - tmp file: cleanup_file (in produzione = os.unlink)
        # - zip_tmp_dir: cleanup_dir (in produzione = rmtree_safe)
        try:
            cleanup_file(tmp.name)
        except Exception as e:
            logger.debug(
                f"cleanup tmp multi-layer fallito ({tmp.name}): {e}"
            )
        if zip_tmp_dir is not None:
            try:
                cleanup_dir(zip_tmp_dir)
            except Exception as e:
                logger.debug(
                    f"cleanup zip_tmp_dir multi-layer fallito ({zip_tmp_dir}): {e}"
                )
