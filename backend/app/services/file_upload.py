"""Upload "single-file" con parsing per file source (STRUCT-001 Seam D-F).

Estratto da `FileSourceService.create_from_upload` senza alcun cambio
comportamentale. La funzione standalone accetta tutti i collaborator
come kwargs espliciti per:

  - testabilita' diretta (no FileSourceService.__new__);
  - go-portability: signature 1:1 con
    `func CreateFromUpload(content io.Reader, filename, name string, opts Opts) (FileSource, error)`;
  - leaf module: zero import da `app.services.file_service` (cycle impossibile).

Use case: upload di un file singolo (CSV/XLSX/JSON/GeoJSON/GPKG/SHP-zip/
GDB-zip) con parsing colonne, inferenza CRS per geo, save BYTEA o
object_storage policy-aware, DB write.

Invarianti preservate (test-blindabili):
  1. MIME validation defense-in-depth (se `mime_validate` fornito):
     eseguita PRIMA di qualsiasi side effect persistente. Default
     None = no-op (comportamento attuale di create_from_upload).
  2. Parse failure NON aborta il flusso: l'inferenza colonne e' best-
     effort; se solleva, logger.warning + columns=[], row_count=0,
     DB save procede comunque (preservato byte-per-byte).
  3. Orphan guard: `dispatch_blob_storage` exception propaga e
     `db_save_file_source` NON viene chiamato.
  4. `db_save_file_source` exception propaga al caller.
  5. Cleanup temp file/dir GARANTITO in `finally` (success + exception).
  6. `group_id or default_group` propagato coerente sia al DTO sia al
     DB save.
  7. `blob_kwargs` da dispatch passato 1:1 a db_save_file_source via
     `**kwargs`.
  8. ZIP unwrap: `BadZipFile` -> ValueError("non valido o corrotto"),
     `zip_slip_error` -> ValueError("path traversal"). Per SHP/GDB il
     blob salvato resta lo ZIP originale (necessario per .shx/.dbf/
     directory .gdb).

Side effect confinati (tutti via callback iniettata):
  - generate_file_id, time_now, detect_format
  - list_layers, list_sheets, load_by_format, column_builder_from_df
  - dispatch_blob_storage, db_save_file_source
  - build_file_source (DTO factory)
  - cleanup (rmtree_safe), safe_extractall (zip_safe), zip_slip_error type
  - mime_validate (opzionale; default = nessuna validazione)
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable, List, Optional, Tuple

from app.services.file_storage_dispatch import BlobDispatchResult


def create_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    selected_columns: Optional[List[str]] = None,
    srid: Optional[int] = None,
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    detect_format: Callable[[str], str],
    list_layers: Callable[[Path], List[str]],
    list_sheets: Callable[[Path], List[str]],
    load_by_format: Callable[..., Tuple[Any, Optional[Any]]],
    column_builder_from_df: Callable[..., List[Any]],
    dispatch_blob_storage: Callable[[str, bytes, str], BlobDispatchResult],
    db_save_file_source: Callable[..., None],
    build_file_source: Callable[..., Any],
    cleanup: Callable[[Any], None],
    safe_extractall: Callable[..., None],
    zip_slip_error: type,
    temp_dir: str,
    default_group: str,
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> Any:
    """Crea file source da upload single-file (con parsing colonne).

    Vedi docstring del modulo per invarianti e collaborator.

    Returns:
        L'istanza DTO costruita da `build_file_source`.

    Raises:
        ValueError:    su MIME block (riemesso da MimeMismatchError)
                       o su ZIP malformato / path traversal.
        Exception:     dispatch_blob_storage / db_save_file_source
                       propagano le loro eccezioni (orphan guard).
    """
    file_id = generate_file_id(name)
    format_type = detect_format(filename)

    suffix = Path(filename).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir)
    file_content.seek(0)
    raw_data = file_content.read()
    tmp.write(raw_data)
    tmp.close()
    tmp_path = Path(tmp.name)

    # MIME defense-in-depth (opzionale): se fornito, eseguito DOPO la
    # scrittura del temp ma PRIMA di qualsiasi side effect persistente
    # (parse / dispatch / DB). Cleanup garantito dal finally esterno
    # qualunque sia l'esito (success o raise).
    if mime_validate is not None:
        try:
            mime_validate(content=raw_data, filename=filename or name)
        except Exception as e:
            from app.utils.mime_validator import MimeMismatchError
            if isinstance(e, MimeMismatchError):
                logger.warning(
                    f"create_from_upload bloccato (MIME): "
                    f"name={name!r} filename={filename!r}: {e}"
                )
                # Cleanup temp prima di propagare (il finally esterno
                # passa via lo zip_tmp_dir setting=None piu' tmp.name).
                try:
                    cleanup(tmp.name)
                except Exception as ce:
                    logger.debug(f"cleanup tmp upload (mime block) fallito: {ce}")
                raise ValueError(str(e)) from e
            raise

    zip_raw_data = raw_data  # conserva ZIP originale per SHP
    zip_tmp_dir: Optional[Path] = None

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
            for ext in ['.shp', '.gpkg', '.gdb', '.geojson', '.csv', '.xlsx']:
                match = next(
                    (f for f in extracted_files if f.suffix.lower() == ext), None,
                )
                if match:
                    tmp_path = match
                    format_type = detect_format(match.name)
                    filename = match.name
                    # Per SHP teniamo il ZIP (ha bisogno di .shx, .dbf, ecc.)
                    if format_type in ('shp', 'gdb'):
                        # GDB e' cartella, non leggibile come bytes -> ZIP nel blob
                        raw_data = zip_raw_data
                    else:
                        raw_data = tmp_path.read_bytes()
                    break

        if format_type in ['gdb', 'gpkg']:
            layers = list_layers(tmp_path)
            if not layer and len(layers) == 1:
                layer = layers[0]
        elif format_type == 'xlsx':
            sheets = list_sheets(tmp_path)
            if not sheet and len(sheets) == 1:
                sheet = sheets[0]

        # Inferenza colonne / row_count / CRS - BEST EFFORT.
        # Se fallisce, logger.warning + columns=[] / row_count=0 / crs=None,
        # il flusso PROSEGUE col DB save (comportamento preservato).
        columns: List[Any] = []
        row_count = 0
        crs: Optional[str] = None
        try:
            df, gdf = load_by_format(tmp_path, format_type, layer, sheet)
            if selected_columns:
                # Filter inline: 3 LOC pure, evita callback dedicato.
                valid_cols = [c for c in selected_columns if c in df.columns]
                if valid_cols:
                    df = df[valid_cols]
            columns = column_builder_from_df(df, gdf, srid)
            row_count = len(df)
            if gdf is not None and hasattr(gdf, 'crs') and gdf.crs:
                # Preferisce il codice EPSG numerico al WKT grezzo
                epsg = gdf.crs.to_epsg()
                if epsg:
                    crs = f"EPSG:{epsg}"
                else:
                    # Fallback: prova to_authority (es. ESRI:xxxxx)
                    auth = gdf.crs.to_authority()
                    crs = f"{auth[0]}:{auth[1]}" if auth else str(gdf.crs)
            elif srid:
                crs = f"EPSG:{srid}"
        except Exception as e:
            logger.warning(f"Errore inferenza colonne: {e}")

        now = time_now()
        # Converti ColumnInfo locali in dict per compatibilita' col DTO
        columns_dicts = [
            {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
            for c in columns
        ]
        file_source = build_file_source(
            id=file_id,
            name=name,
            description=description,
            original_filename=filename,
            format=format_type,
            layer=layer,
            sheet=sheet,
            columns=columns_dicts,
            row_count=row_count,
            file_size=len(raw_data),
            crs=crs,
            group_id=group_id or default_group,
            created_at=now,
            updated_at=now,
        )

        # Metadata serialization duck-typed (Pydantic vs dataclass fallback)
        metadata = (
            file_source.model_dump(mode='json')
            if hasattr(file_source, 'model_dump')
            else {}
        )

        # Dispatch policy-aware (BYTEA vs object_storage). Se solleva
        # (es. S3 down), propaga al caller; il finally cleanup esegue,
        # ma db_save_file_source NON viene chiamato (orphan guard).
        blob_kwargs = dispatch_blob_storage(file_id, raw_data, filename)

        db_save_file_source(
            file_id=file_id,
            group_id=group_id or default_group,
            name=name,
            original_filename=filename,
            fmt=format_type,
            metadata=metadata,
            **blob_kwargs,
        )

        logger.info(f"FileSource creato: {file_id} ({len(raw_data)} bytes)")
        return file_source

    finally:
        # Pulisci zip_tmp_dir per prima (contiene il path estratto se ZIP).
        if zip_tmp_dir is not None:
            try:
                cleanup(zip_tmp_dir)
            except Exception as e:
                logger.debug(
                    f"cleanup zip_tmp_dir fallito ({zip_tmp_dir}): {e}"
                )
        try:
            cleanup(tmp.name)
        except Exception as e:
            logger.debug(f"cleanup tmp upload fallito ({tmp.name}): {e}")
