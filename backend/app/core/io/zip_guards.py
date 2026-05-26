"""
zip_safe.py — Guard contro zip bomb (cap pre-extract).

Post Punto 3 cleanup (2026-05-25):
  - `safe_extractall` / `safe_extract_zip_path` rimossi (zero caller
    runtime: tutto il flow di estrazione passa per `zipfile.ZipFile(
    BytesIO(...))` + `validate_zip_bomb_limits` + `z.read(name)` su
    bytes, oppure per `/vsizip//vsimem` per i binari OGR).
  - `ZipSlipError` + `_validate_member` rimossi: la zip-slip vulnerability
    e' un disk-write attack. Senza estrazione su filesystem (RAM-only)
    il rischio scompare. I caller che leggono singoli member via
    `z.read(name)` ottengono bytes, niente path traversal possibile.

Public surface attivo:
  - `validate_zip_bomb_limits(zf)`: cap size / count / ratio, da chiamare
    PRIMA di `z.read(name)` per evitare decompress bombs.
  - `pick_zip_member_by_extension(...)`: scelta del member ZIP per
    priorita' di estensione (utility comune ai RAM-only loader).
  - `ZipBombError`: raised da `validate_zip_bomb_limits`.
"""

import zipfile
from pathlib import Path
from typing import Callable, Optional

from app.environment import get_env


class ZipBombError(ValueError):
    """Raised quando un archivio ZIP supera i cap pre-extract (size,
    count o compression ratio) — indicatore di zip bomb."""


# Cap defaults (lazy-read from env ad ogni chiamata: no import-time
# freeze, cosi' test + tuning operational senza restart del processo

_DEFAULT_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
_DEFAULT_MAX_FILES = 5000
_DEFAULT_MAX_COMPRESSION_RATIO = 100  # file_size / compress_size

# Soglia sotto la quale non applichiamo il ratio check: file piccoli
# con ratio alto (es. un .txt di 10 byte che comprime a 1 byte → ratio 10
# sono rumore statistico, non attacco. Sopra 1MB il ratio significativo
_RATIO_CHECK_MIN_UNCOMPRESSED = 1 * 1024 * 1024  # 1 MB


def _get_caps() -> tuple[int, int, int, int]:
    """Legge i 4 cap da env. Valori non-int o <=0 cadono sul default
    (silenziosamente: l'env puo' essere letto da operator non-dev).

    Returns: (max_total, max_single_file, max_files, max_ratio)
    `max_single_file` default = `max_total` (no constraint extra finche'
    non viene esplicitamente abbassato).
    """
    def _env_int(name: str, default: int) -> int:
        val = get_env(name)
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


def validate_zip_bomb_limits(zf: zipfile.ZipFile) -> None:
    """Controlli zip bomb PRE-extract: scansiona l'infolist senza
    toccare il disco. Solleva ZipBombError alla prima violazione.

    DEVE essere chiamata PRIMA di leggere member arbitrari (`z.read(name)`),
    altrimenti un attacker puo' decompress-bomb il processo (es.
    `z.read("evil.txt")` su un member da 10 GB uncompressed).
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
        # Cap singolo file: utile in scenari "molti file piccoli" (es
        # IMDF venue: ~50 GeoJSON da pochi KB ciascuno) per rifiutare
        # un member sospetto da gigabyte anche se il totale starebbe
        # nel cap
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
        # piccolo con ratio alto non rappresenta mai un vero attacco
        if size > _RATIO_CHECK_MIN_UNCOMPRESSED:
            ratio = size / comp
            if ratio > max_ratio:
                raise ZipBombError(
                    f"Zip bomb: compression ratio sospetto per "
                    f"{member.filename!r}: {ratio:.0f}x "
                    f"(size={size}, compressed={comp}, cap={max_ratio}x). "
                    f"Override via env ZIP_MAX_COMPRESSION_RATIO se legittimo."
                )


def pick_zip_member_by_extension(
    names: list[str],
    extensions: tuple[str, ...],
    detect_format: Callable[[str], str],
) -> tuple[Optional[str], Optional[str], str]:
    """Sceglie un member dello ZIP per priorita' di estensione.

    Usato dai flow RAM-only che navigano lo ZIP via `zipfile.ZipFile(
    BytesIO(...))` per selezionare il primo file rilevante senza
    estrarre tutto. Caso speciale `.gdb`: la GDB e' una directory
    multi-file dentro lo ZIP (es. `mygdb.gdb/a00000001.gdbtable`); il
    `format` corretto e' `'gdb'` e il `display_name` e' il nome
    della directory `.gdb`, NON del file interno matchato.

    Args:
        names: lista member del ZIP (`z.namelist()`).
        extensions: lista di estensioni con punto in ordine di
            priorita' (es. `('.shp', '.gpkg', '.gdb', '.csv')`).
        detect_format: callback che traduce filename -> format string
            (usato per file singoli; per `.gdb` ignorato in favore
            di `'gdb'` esplicito).

    Returns `(inner_name, inner_format, display_name)`:
      - `inner_name`: il path member nello ZIP (usato per `z.read(...)`)
        oppure per individuare la dir `.gdb`. `None` se nessun match.
      - `inner_format`: il formato derivato dall'estensione. `None`
        se nessun match.
      - `display_name`: il filename "pulito" da mostrare al caller.
        Stringa vuota se nessun match.
    """
    for ext in extensions:
        if ext == '.gdb':
            gdb_match = next(
                (n for n in names if '.gdb/' in n.lower()
                 or n.lower().endswith('.gdb')),
                None,
            )
            if gdb_match:
                lowered = gdb_match.lower()
                if '.gdb/' in lowered:
                    idx = lowered.index('.gdb/')
                    head = gdb_match[: idx + len('.gdb')]
                    display = head.split('/')[-1].split('\\')[-1]
                else:
                    display = Path(gdb_match).name
                return gdb_match, 'gdb', display
            continue
        match = next((n for n in names if n.lower().endswith(ext)), None)
        if match:
            display = Path(match).name
            return match, detect_format(display), display
    return None, None, ""
