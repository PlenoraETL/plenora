"""Shared protocol per il pattern task-scoped blob storage.

Source-of-truth go-ready per il pattern "blob persistito su object
storage segregato per `task_id`, con prefix per dominio e cleanup
eager per task". Vivono oggi due implementazioni concrete:

- `app.core.imdf.storage` — bundle multi-file (`{task_id}.zip` +
  `{task_id}.meta.json` sidecar) + cache locale lazy. Specializzato
  per il flusso IMDF.
- `app.core.eseguibili.storage` — output single-file
  (`{task_id}/{filename}`). Specializzato per gli output eseguibili.

Le due shape sono strutturalmente diverse:

| Aspect | IMDF storage | Eseguibili storage |
|---|---|---|
| Payload | directory zippata + sidecar JSON | bytes o file singolo |
| Save returns | `dict[str, str]` (`zip_key` + `meta_key`) | `str` (single key) |
| List | `list_task_ids()` via sidecar enumeration | `find_keys(task_id)` via prefix list |
| Cache locale | `ensure_local_bundle(task_id)` lazy hydrate | n/a |
| Streaming | n/a (legge da cache locale) | `open_stream(key)` direct |
| Sidecar accessor | `get_meta_only(task_id)` | n/a |

BE-STORAGE-001A (audit-only outcome): il Protocol qui sotto cattura il
**common denominator** (save → key, find/list, delete). Le shape
specializzate (cache, sidecar, stream) restano sui wrapper concreti
finche' un terzo consumer non legittimi un'unificazione piu' invasiva.

Vincoli go-ready:
- modulo pure: zero I/O, zero env reads, zero side effect;
- niente import pesanti (`pandas`/`requests`/`boto3`/cloud_backends);
- type hints espliciti, niente `Any`;
- `@dataclass(frozen=True)` immutable per i tipi result.

Mapping target Go (futuro):

```go
type TaskBlobSaveResult struct {
    PrimaryKey  string
    SidecarKey  *string  // nil quando non c'e' sidecar
}

type TaskScopedBlobStore interface {
    Save(taskID string, ...) (TaskBlobSaveResult, error)
    FindKeys(taskID string) ([]string, error)
    Delete(taskID string) (int, error)
}
```
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
      `list_task_ids() -> list[str]` (enumeration globale).
    - Eseguibili: `open_stream(key) -> dict` (streaming download).

    Quando un terzo consumer richiedera' una di queste capabilities
    cross-cluster, il Protocol andra' esteso (o splittato in un
    sotto-Protocol opt-in).

    Attributi:
        prefix: object storage root prefix per il dominio (es.
            `"imdf-bundles"`, `"eseguibili-outputs"`). Source-of-truth
            in `app.shared.storage_layout`. Letto-only sul Protocol;
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
