"""
Filesystem cleanup helpers (STRUCT-001 Seam D-C).

Estratto da `app.services.file_service._rmtree_safe` + duplicato di
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Optional


_logger_module = logging.getLogger(__name__)


def rmtree_safe(
    path,
    retries: int = 3,
    *,
    logger: Optional[object] = None,
) -> None:
    """Rimuove file o directory con retry per handle locked.

    Args:
        path:    str o Path del file/dir da rimuovere.
        retries: numero di tentativi prima di rinunciare (default 3).
        logger:  LoggerProtocol-like opzionale; se fornito, riceve
                 messaggi `debug` su:
                   - chmod onerror fallito (per-file durante rmtree),
                   - cleanup definitivamente fallito dopo i retry.
                 Se None, fallback al logger del modulo (idle a meno
                 che il chiamante configuri il root logger).

    Returns:
        None. Best-effort: non solleva se il cleanup fallisce.

    Note:
        - File: tenta `Path.unlink()`, ritorna immediatamente al successo.
        - Directory: usa `shutil.rmtree(p, onerror=...)`. La callback
          onerror fa `chmod +write` sul file e ritenta `func(fpath)`.
          Se anche il chmod fallisce, logga debug (la callback NON
          deve raisare per contratto API di shutil).
        - Tra un retry e l'altro: `gc.collect()` per rilasciare handle
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
                        # Callback onerror di shutil.rmtree: non deve raisare
                        # (contratto API). Debug log per visibilita' su permessi.
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
