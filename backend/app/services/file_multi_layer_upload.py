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
     db_save_file_sources_batch: chiamato 1 volta DOPO il loop con la
     lista delle N row, in UNA SOLA transazione DB (BE-HARDEN-001C E3 -
     all-or-nothing). Se la transazione fallisce, 0 row persistite e
     cleanup compensativo del shared storage_key (best-effort).
  6. Cleanup asimmetrico (preservato verbatim):
       - tmp file: cleanup_file (in produzione = os.unlink)
       - zip_tmp_dir: cleanup_dir (in produzione = rmtree_safe)
  7. group_id or default_group propagato coerente su DTO + DB save.
  8. shared_blob_kwargs propagato a tutte le N row del batch (data o
     storage_key, size_bytes condivisi).
  9. is_sheet_based dispatch: XLSX -> layer arg = None, sheet = valore;
     GPKG/GDB -> layer arg = valore, sheet = None. DTO field analogo.

Side effect confinati (tutti via callback iniettata):
  - generate_file_id, time_now, detect_format
  - load_by_format, column_builder_from_df
  - dispatch_blob_storage, db_save_file_sources_batch
  - delete_blob (cleanup compensativo storage su DB rollback)
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
    db_save_file_sources_batch: Callable[[List[Dict[str, Any]]], None],
    delete_blob: Callable[[str], None],
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

        now = time_now()

        # BE-HARDEN-001C E1 - shared blob dispatch:
        # 1 chiamata a dispatch_blob_storage PRIMA del loop con
        # `storage_key_id=batch_id`. Le N row in object_storage path
        # condividono lo stesso storage_key. In BYTEA path le N row
        # hanno gli stessi `data` bytes.
        # Failure pre-loop -> nessuna row, nessun cleanup necessario
        # (orphan guard interno al dispatch).
        batch_id = f"batch_{uuid.uuid4().hex}"
        shared_blob_kwargs = dispatch_blob_storage(
            batch_id, raw_data, filename,
            storage_key_id=batch_id,
        )

        # Loop di staging: parse + DTO build per ogni layer, ZERO side
        # effect DB. La persistenza avviene UNA volta sola dopo il loop
        # via db_save_file_sources_batch (E3 atomic transaction).
        created: List[Any] = []
        rows: List[Dict[str, Any]] = []
        for layer_cfg in layers_config:
            # Supporta sia {"layer": "nome"} che {"sheet": "nome"}
            layer = layer_cfg.get("layer") or layer_cfg.get("sheet")
            selected_columns = layer_cfg.get("selected_columns")

            safe_layer = layer.replace(" ", "_").replace("-", "_") if layer else "default"
            file_source_name = f"{name}_{safe_layer}"
            file_id = generate_file_id(file_source_name)

            # Inferenza colonne / row_count / CRS - BEST EFFORT PER-LAYER.
            # Se fallisce, logger.warning + columns=[] / row_count=0 / crs=None,
            # il loop CONTINUA sui successivi layer (soft-preserve, E3
            # decision documentata nello scope).
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

            rows.append({
                "file_id": file_id,
                "group_id": group_id or default_group,
                "name": file_source_name,
                "original_filename": filename,
                "fmt": format_type,
                "metadata": metadata,
                **shared_blob_kwargs,
            })
            created.append(file_source)

        # BE-HARDEN-001C E3 - all-or-nothing batch save.
        # Una sola transazione DB attorno alle N INSERT (via
        # save_file_sources_batch). Se solleva, cleanup compensativo
        # del shared storage_key (best-effort) prima che la eccezione
        # DB propaghi. Un eventuale fail del cleanup -> log strutturato
        # `event=orphan_blob_cleanup_failed` SENZA mascherare la cause
        # root.
        committed = False
        try:
            db_save_file_sources_batch(rows)
            committed = True
        finally:
            if not committed:
                shared_key = shared_blob_kwargs.get("storage_key")
                if shared_key:
                    try:
                        delete_blob(shared_key)
                    except Exception as cleanup_err:
                        logger.error(
                            f"event=orphan_blob_cleanup_failed "
                            f"batch_id={batch_id} "
                            f"storage_key={shared_key!r} "
                            f"error={cleanup_err!r}"
                        )

        for fs in created:
            logger.info(
                f"FileSource multi-layer creato: {fs.id} "
                f"layer={getattr(fs, 'layer', None)!r} "
                f"sheet={getattr(fs, 'sheet', None)!r}"
            )

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
