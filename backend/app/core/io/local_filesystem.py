"""Local filesystem facade — data plane esterno + operational boundary.

**Scope (out-of-contract RAM-first):**

Questo modulo NON e' una primitive RAM-first come gli altri sotto
`app/core/io/`. E' il **boundary unico** per le scritture su filesystem
locale che esistono per design:

  1. **Data plane endpoints** — `LocalDataSource.write_file` scrive
     l'output di un task `CARICA` su un path configurato dall'utente
     (mount NFS/SMB, volume host). E' una **feature**, non un leak
     interno.
  2. **Operational self-test** — `startup_validation` verifica che il
     `DATA_DIR` sia scrivibile prima di avviare il backend (touch +
     unlink). E' una **diagnostica** boot-time, non runtime.

**Razionale del boundary unico:**

Prima del 2026-05-26 ogni callsite faceva inline il makedirs + write
binary mode oppure un touch diretto. Senza un punto centralizzato,
era impossibile:
  - audit-are le destinazioni filesystem locale in un colpo solo
  - applicare cross-cutting (logging, metrics, size cap, path policy)
  - distinguere "data plane esterno user-configured" da "leak interno"

Adesso TUTTE le scritture filesystem fuori da `app/core/io/*`,
`app/core/storage/*`, e dai tombstone svuotati DEVONO passare per
questo modulo. Il gate (`tests2/io_boundary/`) escludera' i call site
qui dentro come scope dichiarato.

**API:**
  - `write_local_bytes(path, content)`: scrive `content` su `path`,
    creando le directory parent se mancanti.
  - `verify_local_writable(path)`: touch + unlink come self-test
    sulla scrivibilita' del path (boot-time diagnostic).
  - `LocalFilesystemError`: raised su PermissionError / OSError dei
    metodi sopra, con messaggio operativo standardizzato.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from app.utils.logger import get_logger

logger = get_logger(__name__)


class LocalFilesystemError(RuntimeError):
    """Errore boundary su filesystem locale (data plane / operational)."""


def write_local_bytes(path: Union[str, Path], content: bytes) -> None:
    """Scrive `content` su `path` (filesystem locale), creando i parent.

    Boundary per:
      - `LocalDataSource.write_file`: output di task `CARICA` verso un
        path configurato dall'utente (mount locale / NFS / SMB).

    Args:
        path: destinazione assoluta. Il caller e' responsabile della
            path policy (path traversal check, sandbox root). Questo
            modulo NON applica `assert_path_within` o simili — e' un
            facade tecnico, non un security boundary.
        content: bytes da scrivere.

    Raises:
        LocalFilesystemError: PermissionError / OSError wrapped per
            uniformare il messaggio operativo verso il caller.
    """
    target = str(path)
    parent = os.path.dirname(target)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
    except PermissionError as exc:
        raise LocalFilesystemError(
            f"Scrittura negata: {target}"
        ) from exc
    except OSError as exc:
        raise LocalFilesystemError(
            f"Errore scrittura locale: {exc}"
        ) from exc


def verify_local_writable(path: Union[str, Path]) -> None:
    """Self-test che `path` sia una directory scrivibile.

    Boundary per `startup_validation._validate_data_dir`: chiamato in
    boot per dare un errore esplicito se DATA_DIR non e' montato /
    permesso scrittura. Crea un file `.write_test` e lo elimina; non
    lascia residui.

    Args:
        path: directory da testare. Se non esiste, la crea.

    Raises:
        LocalFilesystemError: se mkdir / touch / unlink falliscono.
    """
    dir_path = Path(path)
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe = dir_path / ".write_test"
        probe.touch()
        probe.unlink()
    except (PermissionError, OSError) as exc:
        raise LocalFilesystemError(
            f"{dir_path} non scrivibile: {exc}"
        ) from exc


__all__ = [
    "LocalFilesystemError",
    "verify_local_writable",
    "write_local_bytes",
]
