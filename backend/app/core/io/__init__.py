"""Plenora I/O boundary — RAM-first.

Layer orizzontale per tutta la gestione di lettura/scrittura/archiviazione
fuori dal filesystem locale. Vedi refactor_io_storage_ram_only.md per
il piano completo.

Scope corrente (Fase 0):
  - `policy`: env-driven policy (memory_only / temp_only_explicit / ...)
  - `materialize`: unico punto autorizzato a creare path temporanei
  - `payloads`: BinaryPayload + StoredObjectRef tipati

Fasi successive aggiungeranno: memory, archives, geospatial, tabular,
responses, guards, metrics.

Contract:
  Fuori da questo package (e dai moduli storage autorizzati) NON e'
  ammesso usare tempfile, extractall, Path.write, df export su path,
  FileResponse path-based, shutil.copy, storage upload path-based.
  Il contract test (`tests2/io_boundary/test_no_direct_filesystem_contract.py`)
  blinda la regola contro nuovi usi.
"""

from app.core.io.payloads import BinaryPayload, StoredObjectRef
from app.core.io.policy import (
    DiskWritePolicy,
    get_disk_policy,
    is_persistent_disk_allowed,
    is_temp_disk_allowed,
)

__all__ = [
    "BinaryPayload",
    "DiskWritePolicy",
    "StoredObjectRef",
    "get_disk_policy",
    "is_persistent_disk_allowed",
    "is_temp_disk_allowed",
]
