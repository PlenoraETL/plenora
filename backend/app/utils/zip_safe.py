"""
zip_safe.py — Estrazione ZIP con guard contro path traversal (zip slip)
e contro zip bomb (size cap pre-extract + file count + compression ratio).

Uso raccomandato (sostituisce `zipfile.ZipFile.extractall(dest)`):

    from app.utils.zip_safe import safe_extractall
    with zipfile.ZipFile(path, 'r') as zf:
        safe_extractall(zf, dest_dir)

Oppure, se il caller controlla gia' un `ZipFile`:

    safe_extract_zip_path(zip_path, dest_dir)

Protezioni attive:
  - **Zip slip** (path traversal):
    - rifiuta path assoluti (Unix `/foo`, Windows `C:\\foo`, UNC `\\\\srv`)
    - rifiuta member che dopo resolve escono da `dest_dir`
  - **Zip bomb** (pre-extract, solo header — non si tocca il disco
    finche' tutti i controlli passano):
    - cap totale uncompressed: `ZIP_MAX_UNCOMPRESSED_BYTES` (default 500 MB)
    - cap singolo file uncompressed: `ZIP_MAX_SINGLE_FILE_BYTES`
      (default = cap totale; abbassare per scenari "molti file piccoli"
      dove non e' atteso alcun member singolo > N MB)
    - cap numero file: `ZIP_MAX_FILES` (default 5000)
    - compression ratio per singolo file > 1MB uncompressed:
      `file_size / compress_size > ZIP_MAX_COMPRESSION_RATIO` (default 100)
      → tipicamente indica un bomb (uno ZIP legittimo raramente supera 30x)

Tutti i cap sono sovrascrivibili da env var — es. un caller per backup
GDB può alzare `ZIP_MAX_UNCOMPRESSED_BYTES=5368709120` (5 GB).

L'estrazione e' member-by-member: la prima violazione rilevata solleva
(con tmp_dir eventualmente parzialmente popolata — il caller e' gia'
responsabile del cleanup su eccezione, vedi callsite pattern in
`file_service.py` con `_rmtree_safe`).
"""

import os
import zipfile
from pathlib import Path
from typing import Union


class ZipSlipError(ValueError):
    """Raised quando un archivio ZIP tenta un path traversal."""


class ZipBombError(ValueError):
    """Raised quando un archivio ZIP supera i cap pre-extract (size,
    count o compression ratio) — indicatore di zip bomb."""


# Cap defaults (lazy-read from env ad ogni chiamata: no import-time
# freeze, cosi' test + tuning operational senza restart del processo)

_DEFAULT_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
_DEFAULT_MAX_FILES = 5000
_DEFAULT_MAX_COMPRESSION_RATIO = 100  # file_size / compress_size

# Soglia sotto la quale non applichiamo il ratio check: file piccoli
# con ratio alto (es. un .txt di 10 byte che comprime a 1 byte → ratio 10)
# sono rumore statistico, non attacco. Sopra 1MB il ratio significativo.
_RATIO_CHECK_MIN_UNCOMPRESSED = 1 * 1024 * 1024  # 1 MB


def _get_caps() -> tuple[int, int, int, int]:
    """Legge i 4 cap da env. Valori non-int o <=0 cadono sul default
    (silenziosamente: l'env puo' essere letto da operator non-dev).

    Returns: (max_total, max_single_file, max_files, max_ratio)
    `max_single_file` default = `max_total` (no constraint extra finche'
    non viene esplicitamente abbassato).
    """
    def _env_int(name: str, default: int) -> int:
        val = os.getenv(name)
        if not val:
            return default
        try:
            n = int(val)
            return n if n > 0 else default
        except ValueError:
            return default

    max_total = _env_int("ZIP_MAX_UNCOMPRESSED_BYTES", _DEFAULT_MAX_UNCOMPRESSED_BYTES)
    return (
        max_total,
        _env_int("ZIP_MAX_SINGLE_FILE_BYTES", max_total),
        _env_int("ZIP_MAX_FILES", _DEFAULT_MAX_FILES),
        _env_int("ZIP_MAX_COMPRESSION_RATIO", _DEFAULT_MAX_COMPRESSION_RATIO),
    )


def _validate_member(member_name: str, dest_root: Path, dest_dir: Path) -> None:
    """Valida un singolo member. Solleva ZipSlipError se sospetto.

    R#4 cleanup: il containment check (resolve + relative_to) e' delegato
    a `app.shared.path_policy.assert_path_within`. I reject su path
    assoluti / drive letter restano inline perche' specifici del formato
    ZIP (nessun member ZIP legittimo inizia con '/' o 'C:').
    """
    from app.shared.path_policy import assert_path_within

    if not member_name:
        return
    # Path assoluti Unix / Windows / UNC
    if member_name.startswith(('/', '\\')):
        raise ZipSlipError(f"Zip slip: path assoluto rifiutato: {member_name!r}")
    if len(member_name) >= 2 and member_name[1] == ':':
        # es. "C:/foo" o "C:\\foo"
        raise ZipSlipError(f"Zip slip: drive letter rifiutata: {member_name!r}")

    try:
        assert_path_within(dest_dir / member_name, [dest_root])
    except ValueError as e:
        raise ZipSlipError(
            f"Zip slip: member {member_name!r} esce da {dest_root}"
        ) from e


def _validate_bomb_limits(zf: zipfile.ZipFile) -> None:
    """Controlli zip bomb PRE-extract: scansiona l'infolist senza
    toccare il disco. Solleva ZipBombError alla prima violazione.
    """
    max_total, max_single, max_files, max_ratio = _get_caps()

    infolist = zf.infolist()

    if len(infolist) > max_files:
        raise ZipBombError(
            f"Zip bomb: troppi file ({len(infolist)} > {max_files}). "
            f"Override via env ZIP_MAX_FILES se legittimo."
        )

    total = 0
    for member in infolist:
        size = member.file_size
        comp = member.compress_size or 1  # evita div-by-zero su dir entries
        # Cap singolo file: utile in scenari "molti file piccoli" (es.
        # IMDF venue: ~50 GeoJSON da pochi KB ciascuno) per rifiutare
        # un member sospetto da gigabyte anche se il totale starebbe
        # nel cap.
        if size > max_single:
            raise ZipBombError(
                f"Zip bomb: file {member.filename!r} troppo grande "
                f"({size} > {max_single} bytes). "
                f"Override via env ZIP_MAX_SINGLE_FILE_BYTES se legittimo."
            )
        total += size
        if total > max_total:
            raise ZipBombError(
                f"Zip bomb: dimensione totale uncompressed "
                f"{total} > {max_total} bytes. "
                f"Override via env ZIP_MAX_UNCOMPRESSED_BYTES se legittimo."
            )
        # Ratio check solo per file sopra la soglia minima — un file
        # piccolo con ratio alto non rappresenta mai un vero attacco.
        if size > _RATIO_CHECK_MIN_UNCOMPRESSED:
            ratio = size / comp
            if ratio > max_ratio:
                raise ZipBombError(
                    f"Zip bomb: compression ratio sospetto per "
                    f"{member.filename!r}: {ratio:.0f}x "
                    f"(size={size}, compressed={comp}, cap={max_ratio}x). "
                    f"Override via env ZIP_MAX_COMPRESSION_RATIO se legittimo."
                )


def safe_extractall(zf: zipfile.ZipFile, dest_dir: Union[str, Path]) -> None:
    """Estrae tutti i member di un ZipFile gia' aperto. Valida zip slip
    member-per-member durante l'estrazione, e zip bomb UNA VOLTA all'inizio
    scorrendo l'infolist (nessun file scritto se i cap sono violati).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_root = dest_dir.resolve()
    # Controllo bomb PRIMA di toccare il disco — se fallisce, dest_dir
    # resta vuota (il caller la crea solo qui dentro via mkdir sopra,
    # ma se preesisteva non la tocchiamo).
    _validate_bomb_limits(zf)
    for member in zf.infolist():
        _validate_member(member.filename, dest_root, dest_dir)
        zf.extract(member, dest_dir)


def safe_extract_zip_path(zip_path: Union[str, Path],
                          dest_dir: Union[str, Path]) -> None:
    """Apre il file ZIP e lo estrae con safe_extractall."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        safe_extractall(zf, dest_dir)
