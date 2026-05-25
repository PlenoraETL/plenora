"""Unico punto autorizzato a materializzare bytes su filesystem.

Fuori da questo modulo, i `tempfile.NamedTemporaryFile` / `mkdtemp` /
`Path.write_bytes` sono vietati (vedi contract test
`tests2/io/test_no_direct_filesystem_contract.py` + allowlist
`.claude/io-refactor/allowlist-baseline.yaml` per i call-site
legacy ancora in migrazione).

Il context manager `temporary_path_from_bytes` esiste per i casi
dove una libreria esterna richiede DAVVERO un path su disco (es.
binari OGR che non supportano /vsimem, GeoPackage driver legacy,
file-based parser che non hanno BytesIO API). Ogni materializzazione
deve dichiarare un `reason` per audit.

Behavior dipende dalla policy attiva (`PLENORA_IO_DISK_POLICY`):
  - `memory_only`: solleva `DiskWriteDeniedError`
  - `temp_only_explicit` (default): scrive in `settings.TEMP_DIR`,
    cleanup garantito a fine context
  - `local_dev_allowed` / `legacy_compat`: come temp_only_explicit
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.core.io.policy import DiskWritePolicy, get_disk_policy
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DiskWriteDeniedError(RuntimeError):
    """Sollevata quando la policy attiva vieta il temp disk write."""


@contextmanager
def temporary_path_from_bytes(
    data: bytes,
    *,
    reason: str,
    suffix: str = ".bin",
    prefix: str = "plenora_io_",
) -> Iterator[Path]:
    """Materializza `data` in un file temp e yielda il `Path`.

    Il file e' creato in `settings.TEMP_DIR` (fallback su
    `tempfile.gettempdir()` se non configurato), scritto, chiuso,
    e cancellato a fine context (anche su eccezione).

    Args:
        data: bytes da scrivere su disco.
        reason: stringa human-readable obbligatoria per audit
            (es. "legacy_excel_reader_requires_path"). Loggata a
            INFO insieme al size del payload.
        suffix: estensione file (`.xlsx`, `.gpkg`, ...).
        prefix: prefisso filename, default `plenora_io_`.

    Yields:
        `Path` assoluto al file temporaneo.

    Raises:
        DiskWriteDeniedError: se la policy attiva e' `memory_only`.
            Il caller deve riformulare il flow in RAM (BytesIO /
            /vsimem) oppure cambiare policy.
    """
    policy = get_disk_policy()
    if policy == DiskWritePolicy.MEMORY_ONLY:
        raise DiskWriteDeniedError(
            f"Disk write denied by policy {policy.value!r}: "
            f"reason={reason!r}, size={len(data)} bytes. "
            f"Refactor caller a RAM-only (BytesIO / /vsimem) o "
            f"cambia PLENORA_IO_DISK_POLICY."
        )

    temp_dir = _resolve_temp_dir()
    fd, path_str = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=temp_dir)
    path = Path(path_str)
    try:
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        logger.info(
            "[io.materialize] reason=%s size=%d path=%s",
            reason, len(data), path,
        )
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "[io.materialize] cleanup fallito per %s: %s",
                path, exc,
            )


def _resolve_temp_dir() -> Optional[str]:
    """Ritorna `settings.TEMP_DIR` se configurato, altrimenti None.

    `None` lascia `tempfile.mkstemp` decidere autonomamente (tipicamente
    `/tmp` su Linux). Manteniamo l'opzione per import-safe: questo
    modulo viene importato da test che non hanno `settings` inizializzato.
    """
    try:
        from app.config import settings
        temp_dir = settings.get("TEMP_DIR")
        return str(temp_dir) if temp_dir else None
    except Exception:
        return None


__all__ = [
    "DiskWriteDeniedError",
    "temporary_path_from_bytes",
]
