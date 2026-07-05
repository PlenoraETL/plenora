"""Local filesystem cleanup facade — boundary I/O orizzontale.

**Scope:** centralizza tutte le rimozioni file/directory sul filesystem
locale del backend (cleanup tmp, eviction cache, delete data plane).

**API:**
  - `rmtree_safe(path, retries=3, logger=None)`: cleanup best-effort
    file OR directory. Retry con `gc.collect`, onerror chmod+retry
    per handle locked Windows. Non solleva.
  - `delete_local_file(path, *, missing_ok=False)`: delete strict
    di un singolo file. Solleva `FileNotFoundError` se missing_ok=False
    e il file non esiste. Solleva `OSError` se permission denied o
    altri errori filesystem. Per data plane delete (LocalDataSource).

**Quando usare cosa:**
  - **`rmtree_safe`**: cleanup tmp dir / cache eviction / temp file
    dopo upload. Il caller logga ma non interrompe il flow se il
    cleanup fallisce.
  - **`delete_local_file`**: API delete della data plane (es.
    `LocalDataSource.delete_file`). Il caller DEVE sapere se la
    delete e' riuscita per ritornare status corretto al client.
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Optional, Union


_logger_module = logging.getLogger(__name__)


def rmtree_safe(
    path,
    retries: int = 3,
    *,
    logger: Optional[object] = None,
) -> None:
    """Rimuove file o directory con retry per handle locked.

    Args:
        path: str o Path del file/dir da rimuovere.
        retries: numero di tentativi prima di rinunciare (default 3).
        logger: LoggerProtocol-like opzionale; se fornito, riceve
                 messaggi `debug` su:
                   - chmod onerror fallito (per-file durante rmtree),
                   - cleanup definitivamente fallito dopo i retry.
                 Se None, fallback al logger del modulo (idle a meno
                 che il chiamante configuri il root logger).

    Returns:
        None. Best-effort: non solleva se il cleanup fallisce.

    Note:
        - File: tenta `Path.unlink`, ritorna immediatamente al successo.
        - Directory: usa `shutil.rmtree(p, onerror=...)`. La callback
          onerror fa `chmod +write` sul file e ritenta `func(fpath)`.
          Se anche il chmod fallisce, logga debug (la callback NON
          deve raisare per contratto API di shutil).
        - Tra un retry e l'altro: `gc.collect` per rilasciare handle
          Python pendenti, poi sleep 0.3s.
    """
    log = logger if logger is not None else _logger_module
    p = Path(path)
    last_exc: Optional[BaseException] = None

    for _attempt in range(retries):
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
                        # shutil.rmtree onerror callbacks must not raise.
                        log.debug(
                            f"rmtree_safe onerror chmod fallito per {fpath}: {e}"
                        )
                shutil.rmtree(p, onerror=_on_error)
                if not p.exists():
                    return
        except Exception as e:
            last_exc = e
        gc.collect()
        time.sleep(0.3)

    if last_exc is not None and p.exists():
        log.debug(
            f"rmtree_safe: cleanup fallito per {p} dopo {retries} tentativi: {last_exc}"
        )


def delete_local_file(path: Union[str, Path], *, missing_ok: bool = False) -> None:
    """Rimuove un singolo file locale (strict mode).

    Differenza con `rmtree_safe`: questa funzione solleva su error,
    non e' best-effort. Pensata per la data plane delete API
    (`LocalDataSource.delete_file`, result output cleanup)
    dove il caller DEVE sapere se l'operazione e' riuscita per
    rispondere correttamente al client.

    Args:
        path: file da rimuovere. NON gestisce directory (usare
            `rmtree_safe` per dir).
        missing_ok: se True, no-op silente quando il file non esiste.
            Se False (default), solleva `FileNotFoundError`.

    Raises:
        FileNotFoundError: file inesistente e `missing_ok=False`.
        IsADirectoryError: il path punta a una directory.
        PermissionError: lettura/scrittura negata.
        OSError: altri errori filesystem.
    """
    p = Path(path)
    if not p.exists():
        if missing_ok:
            return
        raise FileNotFoundError(f"File non trovato: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"delete_local_file: {p} e' una directory. Usare rmtree_safe."
        )
    p.unlink()


__all__ = [
    "delete_local_file",
    "rmtree_safe",
]
