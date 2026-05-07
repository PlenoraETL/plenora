"""
Shared protocol per il pattern task-scoped blob storage.

Source-of-truth portable per il pattern "blob persistito su object
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable


@dataclass(frozen=True)
class TaskBlobSaveResult:
    """Esito tipato di un'operazione di save su task-scoped blob store.

    `primary_key`: object storage key principale (e.g. `imdf-bundles/
    {task_id}.zip` oppure `eseguibili-outputs/{task_id}/{filename}`).

    `sidecar_key`: opzionale. Solo per i wrapper che persistono un
    sidecar JSON separato (oggi `app.core.imdf.storage.save_bundle`
    salva `{task_id}.meta.json` accanto allo zip). `None` per i
    wrapper single-file (eseguibili).
    """
    primary_key: str
    sidecar_key: str | None = None


@runtime_checkable
class TaskScopedBlobStore(Protocol):
    """Common denominator del pattern task-scoped blob storage.

    Cattura le 3 operazioni che entrambe le impl concrete devono
    esporre: save (con shape diversa per dominio), find/list per
    task_id, delete per task_id.

    NON cattura le specializzazioni:
    - IMDF: `ensure_local_bundle(task_id) -> Path` (cache lazy),
      `get_meta_only(task_id) -> dict | None` (sidecar accessor),
      `list_task_ids -> list[str]` (enumeration globale).
    - Eseguibili: `open_stream(key) -> dict` (streaming download).

    Quando un terzo consumer richiedera' una di queste capabilities
    cross-cluster, il Protocol andra' esteso (o splittato in un
    sotto-Protocol opt-in).

    Attributi:
        prefix: object storage root prefix per il dominio (es.
            `"imdf-bundles"`, `"eseguibili-outputs"`). Source-of-truth
            in `app.shared.resources.storage_layout`. Letto-only sul Protocol;
            l'impl lo expose come module-level constant.
    """

    prefix: str

    def find_keys(self, task_id: str) -> List[str]:
        """Lista tutte le object storage key sotto `{prefix}/{task_id}*`.

        Eseguibili: list keys via `s3.list_objects(prefix={prefix}/{task_id}/)`.
        IMDF: stesso pattern, ma normalmente ritorna 2 key
        (`{task_id}.zip` + `{task_id}.meta.json`) che condividono il
        prefix `{prefix}/{task_id}`.
        """
        ...

    def delete(self, task_id: str) -> int:
        """Cancella tutti gli oggetti del task. Idempotente.

        Ritorna il numero di oggetti cancellati. IMDF rimuove anche
        eventuale cache locale.
        """
        ...


__all__ = [
    "TaskBlobSaveResult",
    "TaskScopedBlobStore",
]
