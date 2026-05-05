"""Arricchisci execution context — typed boundary.

BE-GO-READY-TRANSFORMATION-ENGINE-015-ARRICCHISCI-EXECUTION-CONTEXT.

Introduce un boundary tipato per il contesto di una singola
esecuzione canvas-arricchisci, riducendo la dipendenza da stato
mutabile sul singleton `api_connection_service`.

Boundary contract
=================
`ArricchisciExecutionContext` e' un `@dataclass(frozen=True)`
immutabile che incapsula i 3 dati per-execution:

- `project_id` — id del progetto in esecuzione (input al
  `db_svc.get_project` lookup);
- `group_id` — tenant binding fail-closed; None significa
  "contesto di sicurezza non determinabile" (i mixin downstream
  rifiutano l'operazione con `ValueError` esplicito);
- `preview_limit` — opzionale `int>0` per truncating del
  DataFrame in modalita' preview (`_load_file_source`).

Pattern di uso
==============
```python
ctx = build_execution_context(project_id, preview_limit)
# ctx.group_id == _proj_row['group_id'] o None se progetto inesistente
# canvas_orchestrator scrive i valori sul singleton per KEEP_COMPAT
# coi mixin esistenti (Phase 1):
apply_to_legacy_state(svc, ctx)
```

Phase 1 vs Phase 2
==================
**Phase 1 (questo batch)**: il contesto vive accanto allo stato
singleton. `canvas_orchestrator` lo costruisce prima di scrivere i
valori su `svc._current_group_id` / `svc._preview_limit`. I mixin
legacy (`api_connection_source_loading`, `api_connection_rest_file`)
continuano a leggere `getattr(self, "_current_group_id", None)` —
KEEP_COMPAT preservata.

**Phase 2 (residuo TE15-R1)**: migrare i 6 mixin reader site a
ricevere il contesto tipato come kwarg, eliminare lo stato
mutabile dal singleton. Audit dedicato + 6 mixin updates.

Concurrency
===========
`@dataclass(frozen=True)` produce un'istanza immutabile dopo la
costruzione (raise `FrozenInstanceError` su setattr). Due
esecuzioni concorrenti che istanziano due `ArricchisciExecutionContext`
distinti hanno tenant/preview state isolato byte-per-byte —
contrast: due esecuzioni concorrenti sullo stesso singleton
condividerebbero `svc._current_group_id` (race window). Il
`workers/tasks/enrichment_task.py` mitiga gia' instanziando una
nuova `ApiConnectionService` per task (commento al line 254
pre-batch); questo modulo offre lo stesso pattern come boundary
tipato esplicito.

Go-ready mapping
================
    type ExecutionContext struct {
        ProjectID    string
        GroupID      *string  // nullable
        PreviewLimit *int     // nullable, >0 if set
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import psycopg2

import app.db.db_service as db_svc
from app.shared.exceptions import ExecutionContextError


if TYPE_CHECKING:
    from app.core.arricchisci.api_connector import ApiConnectionService


__all__ = [
    "ArricchisciExecutionContext",
    "build_execution_context",
    "apply_to_legacy_state",
]


@dataclass(frozen=True)
class ArricchisciExecutionContext:
    """Contesto immutabile di una singola esecuzione canvas arricchisci.

    Attributes:
        project_id: id del progetto in esecuzione. Sempre presente
            (input obbligatorio).
        group_id: tenant binding del progetto. `None` quando il
            progetto non esiste nel DB (`db_svc.get_project` ha
            ritornato `None`) — i mixin downstream sollevano
            "Contesto di sicurezza non determinabile" quando
            tentano operazioni cross-tenant. Non e' un errore
            di configurazione: e' un ramo dati legittimo.
        preview_limit: se `int>0`, tronca i DataFrame caricati
            via `_load_file_source` (preview UI). `None` o
            `<=0` = no truncating (esecuzione full).

    Frozen: immutabile dopo `__init__`. Due esecuzioni
    concorrenti istanziano due context distinti, niente race
    cross-tenant.
    """

    project_id: str
    group_id: Optional[str]
    preview_limit: Optional[int]


def build_execution_context(
    project_id: str,
    preview_limit: Optional[int] = None,
) -> ArricchisciExecutionContext:
    """Costruisce un `ArricchisciExecutionContext` con il
    `group_id` letto dal DB.

    Fail-closed su errori `psycopg2.Error`: re-raise come
    `ExecutionContextError` con `original_error` + `__cause__`
    impostati. Stesso contract del legacy
    `canvas_orchestrator.init_execution_context`.

    Su progetto inesistente (`_proj_row is None`):
    `group_id = None`, ramo dati legittimo. I mixin downstream
    fail-closed con messaggio "Contesto di sicurezza non
    determinabile (group_id mancante)".

    Pure function: nessun side effect su `svc` o stato globale.
    Per applicare il contesto al singleton (KEEP_COMPAT phase 1)
    usa `apply_to_legacy_state(svc, ctx)`.

    Args:
        project_id: id del progetto in esecuzione.
        preview_limit: opzionale `int>0` per truncating preview.

    Raises:
        ExecutionContextError: se il DB lookup fallisce con
            `psycopg2.Error` (pool exhausted / connection lost /
            deadlock / query error).

    Returns:
        `ArricchisciExecutionContext` immutabile.
    """
    try:
        _proj_row = db_svc.get_project(project_id)
    except psycopg2.Error as e:
        raise ExecutionContextError(
            project_id,
            reason="errore lettura progetto dal DB",
            original_error=e,
        ) from e
    group_id = _proj_row.get("group_id") if _proj_row else None
    return ArricchisciExecutionContext(
        project_id=project_id,
        group_id=group_id,
        preview_limit=preview_limit,
    )


def apply_to_legacy_state(
    svc: "ApiConnectionService",
    ctx: ArricchisciExecutionContext,
) -> None:
    """Mirror dei valori del contesto sullo stato legacy mutabile
    `svc._current_group_id` / `svc._preview_limit` (KEEP_COMPAT
    phase 1).

    Necessario perche' i mixin downstream
    (`api_connection_source_loading`, `api_connection_rest_file`)
    leggono ancora `getattr(self, "_current_group_id", None)`. Il
    workers task (`enrichment_task.py`) scrive direttamente questi
    attributi su un'istanza locale — pattern speculare alla
    dipendenza legacy.

    Phase 2 (TE15-R1, audit dedicato): migrare i 6 mixin reader
    site a ricevere `ctx` come kwarg esplicito; a quel punto
    questa funzione potra' essere rimossa e i 2 attributi
    `_current_group_id` / `_preview_limit` cancellati dal
    singleton.

    Order matters: azzera `_current_group_id` PRIMA di scrivere il
    nuovo valore. Questo replica l'invariant del legacy
    `init_execution_context` (non lasciare stato stale dal run
    precedente sul singleton se l'init solleva).
    """
    svc._preview_limit = ctx.preview_limit
    svc._current_group_id = None
    svc._current_group_id = ctx.group_id
