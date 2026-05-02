"""Preview di un file caricato senza persistenza DB (STRUCT-001 Seam D-A).

Estratto da `FileSourceService.preview_file_temp` senza alcun cambio
comportamentale. La funzione e' standalone (no DB, no auth, no
cross-tenant check) e accetta tutti i collaborator come kwargs
espliciti per:

  - testabilita' diretta con MagicMock (no FileSourceService.__new__);
  - go-portability: signature 1:1 con
    `func PreviewFileTemp(content io.Reader, filename string, opts PreviewOpts) (PreviewResult, error)`;
  - leaf module: zero import da `app.services.file_service` (no cycle).

Side effect confinati:
  - temp file write su `app.config.settings.TEMP_DIR`;
  - MIME validation defense-in-depth via `app.utils.mime_validator`
    (block-only su magic bytes; stesso check del router);
  - ZIP unwrap (su zip_safe.safe_extract via fs.extract_zip);
  - parser dispatch via `loader` (DataLoaderProtocol);
  - cleanup garantito in `finally` per tmp file/dir su success e exception.

Nessuna chiamata DB, nessun auth check: l'autorizzazione e' del router
(layer auth FastAPI) - qui valida solo che il blob sia parsabile.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO, Optional


_logger_module = logging.getLogger(__name__)


def _rmtree_safe(path, _retries: int = 3) -> None:
    """Rimuove file o directory con retry per handle locked (es. fiona/GDAL).

    Inlined da `app.services.file_service._rmtree_safe` per mantenere
    questo modulo leaf (zero import da `app.services.file_service`).
    Behavior byte-per-byte identico al sorgente originale.
    """
    import stat
    import time
    import gc
    import shutil
    import os

    p = Path(path)
    last_exc: Optional[BaseException] = None
    for _attempt in range(_retries):
        try:
            if p.is_file():
                p.unlink()
                return
            elif p.is_dir():
                def _on_error(func, fpath, exc_info):
                    try:
                        os.chmod(fpath, stat.S_IWRITE)
                        func(fpath)
                    except Exception as e:
                        # Callback onerror di shutil.rmtree: non deve raisare
                        # (contratto API). Debug log per visibilita' su permessi.
                        _logger_module.debug(
                            f"_rmtree_safe onerror chmod fallito per {fpath}: {e}"
                        )
                shutil.rmtree(p, onerror=_on_error)
                if not p.exists():
                    return
        except Exception as e:
            last_exc = e
        gc.collect()
        time.sleep(0.3)
    if last_exc is not None and p.exists():
        _logger_module.debug(
            f"_rmtree_safe: cleanup fallito per {p} dopo {_retries} tentativi: {last_exc}"
        )


def preview_file_temp(
    file_content: BinaryIO,
    filename: str,
    *,
    fs,
    loader,
    format_detector,
    column_builder,
    logger,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
) -> dict:
    """
    Preview temporanea di un file prima dell'upload definitivo.

    - layer: carica un layer specifico (GPKG/GDB)
    - sheet: carica un foglio specifico (XLSX)
    - Se nessuno specificato su file multi-layer/sheet, restituisce la lista disponibile.

    Defense-in-depth: ri-valida block-only sui magic bytes (il router
    gia' lo fa, ma chiamate dirette al service da altre code paths
    non possono assumere quella protezione).

    Args:
        file_content:    BinaryIO del file caricato (rewind-able).
        filename:        nome file originale (per detect_format + MIME hint).
        fs:              FileSystemProtocol (extract_zip/iterdir/is_dir).
        loader:          DataLoaderProtocol (load_csv/excel/json/geospatial,
                         list_layers/list_sheets).
        format_detector: classe FormatDetector (detect classmethod).
        column_builder:  classe ColumnInfoBuilder (from_dataframe staticmethod).
        logger:          LoggerProtocol-like (warning/debug usato qui).
        layer:           layer GPKG/GDB da caricare (opzionale).
        sheet:           foglio XLSX da caricare (opzionale).

    Returns:
        dict con `columns`, `row_count`, `format`, `layers`, `sheets`.

    Raises:
        ValueError: formato non supportato o MIME mismatch (block-only).
    """
    import tempfile
    from app.utils.mime_validator import (
        MimeMismatchError, validate_no_executable,
    )

    # Letti i bytes per il MIME check, poi rewind per il caller.
    # NB: per file molto grandi questo accumula in RAM una volta in
    # piu'; e' OK per preview che e' bounded a pochi MB tipici.
    file_content.seek(0)
    _peek = file_content.read()
    file_content.seek(0)
    try:
        validate_no_executable(content=_peek, filename=filename)
    except MimeMismatchError as e:
        logger.warning(
            f"preview_file_temp bloccato (MIME): filename={filename!r}: {e}"
        )
        raise ValueError(str(e)) from e

    format_type = format_detector.detect(filename)

    if format_type == 'unknown':
        raise ValueError(f"Formato file non supportato: {filename}")

    suffix = Path(filename).suffix

    from app.config import settings
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=settings.TEMP_DIR) as tmp:
        import shutil
        shutil.copyfileobj(file_content, tmp)
        tmp_path = Path(tmp.name)

    tmp_dir = None
    original_tmp = tmp_path

    try:
        columns = []
        row_count = 0

        if format_type == 'zip':
            tmp_dir = Path(tempfile.mkdtemp(dir=settings.TEMP_DIR))
            fs.extract_zip(tmp_path, tmp_dir)

            for item in fs.iterdir(tmp_dir):
                if fs.is_dir(item) and item.suffix.lower() == '.gdb':
                    format_type = 'gdb'
                    tmp_path = item
                    break
                elif item.suffix.lower() == '.shp':
                    format_type = 'shp'
                    tmp_path = item
                    break
                elif item.suffix.lower() == '.gpkg':
                    format_type = 'gpkg'
                    tmp_path = item
                    break

        # File multi-layer/sheet senza selezione: restituisce lista layers/sheets
        if format_type in ['gpkg', 'gdb'] and not layer:
            available_layers = loader.list_layers(tmp_path)
            if len(available_layers) > 1:
                return {
                    "columns": [],
                    "row_count": 0,
                    "format": format_type,
                    "layers": available_layers,
                    "sheets": []
                }
            if available_layers:
                layer = available_layers[0]

        if format_type in ['xlsx', 'xls'] and not sheet:
            available_sheets = loader.list_sheets(tmp_path)
            if len(available_sheets) > 1:
                return {
                    "columns": [],
                    "row_count": 0,
                    "format": format_type,
                    "layers": [],
                    "sheets": available_sheets
                }
            if available_sheets:
                sheet = available_sheets[0]

        # Carica con layer o sheet
        is_sheet = format_type in ['xlsx', 'xls']
        from app.services.file_load_dispatch import load_by_format
        df, gdf = load_by_format(
            loader, tmp_path, format_type,
            None if is_sheet else layer,
            sheet if is_sheet else None,
            logger=logger,
        )
        columns = column_builder.from_dataframe(df, gdf)
        row_count = len(df)

        return {
            "columns": [{"name": c.name, "type": c.type} for c in columns],
            "row_count": row_count,
            "format": format_type,
            "layers": [],
            "sheets": []
        }

    finally:
        import gc
        try:
            del df, gdf
        except NameError:
            pass
        gc.collect()
        if tmp_dir and tmp_dir.exists():
            _rmtree_safe(tmp_dir)
        try:
            if original_tmp.exists():
                _rmtree_safe(original_tmp)
        except Exception as e:
            logger.debug(
                f"cleanup preview original_tmp fallito ({original_tmp}): {e}"
            )
        # Se non era ZIP, tmp_path == original_tmp, gia' gestito sopra
        if tmp_path != original_tmp:
            try:
                if tmp_path.exists():
                    _rmtree_safe(tmp_path)
            except Exception as e:
                logger.debug(
                    f"cleanup preview tmp_path fallito ({tmp_path}): {e}"
                )


