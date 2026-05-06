"""Arricchisci execution context — typed boundary.

BE-GO-READY-TRANSFORMATION-ENGINE-015-ARRICCHISCI-EXECUTION-CONTEXT.
BE-GO-READY-ORCHESTRATION-ENGINE-012-KILL-APPLY-TO-LEGACY-STATE.

Introduce un boundary tipato per il contesto di una singola
esecuzione canvas-arricchisci. Post-OE011 / OE012 il modulo
NON scrive piu' alcuno stato sul singleton
`api_connection_service`: i mixin di produzione consumano
direttamente il `ctx`, e il vecchio scaffold "mirror legacy"
(`apply_to_legacy_state`) e' stato eliminato.

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
# I mixin di produzione ricevono il ctx come parametro esplicito
# (signature *_with_ctx); nessuno stato viene mirror-ato sul
# singleton.
```

Concurrency
===========
`@dataclass(frozen=True)` produce un'istanza immutabile dopo la
costruzione (raise `FrozenInstanceError` su setattr). Due
esecuzioni concorrenti che istanziano due
`ArricchisciExecutionContext` distinti hanno tenant/preview
state isolato byte-per-byte. Il `workers/tasks/enrichment_task.py`
istanzia una nuova `ApiConnectionService` per task; questo
modulo offre lo stesso pattern come boundary tipato esplicito.

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
from typing import Optional

import psycopg2

import app.db.db_service as db_svc
from app.shared.exceptions import ExecutionContextError


__all__ = [
    "ArricchisciExecutionContext",
    "build_execution_context",
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
    Post-OE012 il singleton non viene piu' mirror-ato (la
    funzione `apply_to_legacy_state` e' stata cancellata): i
    mixin consumano `ctx` direttamente.

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

# BE-GO-READY-ORCHESTRATION-ENGINE-012: `apply_to_legacy_state`
# was deleted. Pre-012 it mirrored ctx values onto
# `svc._current_group_id` / `svc._preview_limit` for legacy
# mixin readers; OE011 retired the only production caller (the
# orchestrator) and made every mixin ctx-strict. The standalone
# function then existed only to keep retired tests green and was
# itself documented as test-surface-only. OE012 removes it
# entirely; the negative source-bound lock in
# `tests2/arricchisci/unit/test_external_consumers_ctx_migration_contract.py`
# enforces that no production / test code re-introduces it.
