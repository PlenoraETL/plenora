"""Upload "raw" (blob senza parsing) per file source (STRUCT-001 Seam D-E).

Estratto da `FileSourceService.create_raw_from_upload` senza alcun
cambio comportamentale. La funzione standalone accetta tutti i
collaborator come kwargs espliciti per:

  - testabilita' diretta (no FileSourceService.__new__);
  - go-portability: signature 1:1 con
    `func CreateRawFromUpload(content io.Reader, filename, name string, opts Opts) (FileSource, error)`;
  - leaf module: zero import da `app.services.file_service` (cycle impossibile).

Use case: fallback per formati che il parser non sa gestire (DXF,
ZIP non standard, allegati ticket, ecc.). Il blob viene salvato nel
DB come BYTEA o object_storage (via `dispatch_blob_storage`); le
colonne/layer del DTO restano vuoti.

Invarianti preservate (test-blindabili):
  1. MIME validation defense-in-depth eseguita PRIMA di qualsiasi
     side effect (no DTO build, no dispatch, no DB save se MIME block).
  2. Orphan guard: `dispatch_blob_storage` exception propaga e
     `db_save_file_source` NON viene chiamato.
  3. `db_save_file_source` exception propaga al caller.
  4. `group_id or default_group` propagato coerente sia al DTO sia
     al DB save (NO drift).
  5. `blob_kwargs` (chiavi `data`/`storage_key`/`size_bytes`) passato
     1:1 al `db_save_file_source` via `**kwargs`.

Side effect confinati (tutti via callback iniettata):
  - generate_file_id(name) -> file_id opaco
  - time_now() -> datetime UTC-aware per timestamps
  - dispatch_blob_storage(file_id, raw_data, filename) -> blob_kwargs
  - db_save_file_source(file_id, group_id, name, original_filename, fmt,
    metadata, **blob_kwargs)
  - build_file_source(**fields) -> DTO (Pydantic/dataclass)
  - mime_validate(content, filename) -> raise MimeMismatchError on block
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Callable, Optional


def create_raw_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    dispatch_blob_storage: Callable[[str, bytes, str], dict],
    db_save_file_source: Callable[..., None],
    default_group: str,
    build_file_source: Callable[..., Any],
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> Any:
    """Crea un file source "raw" (blob senza parsing).

    Defense-in-depth: anche se i router upload validano gia' il MIME
    prima di chiamare il service, qui ri-validiamo "block-only" sui
    magic bytes per proteggere caller futuri (task Celery, batch
    import, ecc.) che bypassino il router.

    Args:
        file_content:           BinaryIO del file caricato.
        filename:               nome file originale (per estensione + MIME).
        name:                   nome logico del file source.
        description:            descrizione opzionale.
        group_id:               group_id del caller; se None usa `default_group`.
        generate_file_id:       callable per id opaco (es. self._generate_file_id).
        time_now:               callable per datetime UTC (es. self._time.now).
        dispatch_blob_storage:  callable policy-aware DB/object_storage.
        db_save_file_source:    callable di scrittura DB (`db.save_file_source`).
        default_group:          fallback per group_id (es. settings.DEFAULT_GROUP).
        build_file_source:      callable kwargs-only che costruisce il DTO
                                FileSource (Pydantic in produzione, dataclass
                                fallback nei test standalone).
        logger:                 LoggerProtocol-like (warning/info usati).
        mime_validate:          callable (content, filename) -> None che
                                solleva MimeMismatchError su block. Se None,
                                fallback a `app.utils.mime_validator.validate_no_executable`.

    Returns:
        L'istanza DTO costruita da `build_file_source`.

    Raises:
        ValueError:    su MIME block (riemesso da MimeMismatchError).
        Exception:     dispatch_blob_storage / db_save_file_source propagano
                       le loro eccezioni (orphan guard rispettato).
    """
    # MIME validator default: lazy import per evitare side effect
    # all'import del modulo. Pattern coerente coi seams D-A/D-D.
    if mime_validate is None:
        from app.utils.mime_validator import validate_no_executable
        mime_validate = validate_no_executable

    file_id = generate_file_id(name)
    file_content.seek(0)
    raw_data = file_content.read()

    # Step 1: MIME defense-in-depth PRIMA di qualsiasi side effect
    # (DTO build / dispatch / DB save). Block-only sui magic bytes.
    try:
        mime_validate(content=raw_data, filename=filename or name)
    except Exception as e:
        # MimeMismatchError -> ValueError con logger.warning. Altri tipi
        # di exception del validator vengono comunque trattati come
        # MIME block (fail-closed): ritrasmessi come ValueError.
        # Per il caso atteso (MimeMismatchError), il messaggio originale
        # viene preservato in str(e).
        from app.utils.mime_validator import MimeMismatchError
        if isinstance(e, MimeMismatchError):
            logger.warning(
                f"create_raw_from_upload bloccato (MIME): "
                f"name={name!r} filename={filename!r}: {e}"
            )
            raise ValueError(str(e)) from e
        raise

    file_ext = Path(filename).suffix.lstrip(".").lower() or "bin"
    now = time_now()

    # Step 2: Costruzione DTO via callback (go-ready: il modulo non
    # conosce il tipo concreto del DTO, kwargs-only).
    file_source = build_file_source(
        id=file_id,
        name=name,
        description=description,
        original_filename=filename,
        format=file_ext,
        layer=None,
        sheet=None,
        columns=[],
        row_count=0,
        file_size=len(raw_data),
        crs=None,
        group_id=group_id or default_group,
        created_at=now,
        updated_at=now,
    )

    # Step 3: serializzazione metadata duck-typed (Pydantic vs dataclass).
    metadata = (
        file_source.model_dump(mode="json")
        if hasattr(file_source, "model_dump")
        else {}
    )

    # Step 4: dispatch policy-aware (DB BYTEA vs object_storage).
    # Se solleva (es. S3 down), propaga e l'orphan guard impedisce
    # il successivo db_save_file_source (no row DB orfana).
    blob_kwargs = dispatch_blob_storage(file_id, raw_data, filename)

    # Step 5: DB write. Se solleva, propaga al caller.
    db_save_file_source(
        file_id=file_id,
        group_id=group_id or default_group,
        name=name,
        original_filename=filename,
        fmt=file_ext,
        metadata=metadata,
        **blob_kwargs,
    )

    logger.info(
        f"FileSource raw creato: {file_id} format={file_ext} "
        f"({len(raw_data)} bytes)"
    )
    return file_source
