"""Canonical scheduler runtime status enum.

Keeps this module's responsibility explicit and isolated from adjacent code.

`StrEnum` mantiene `.value` come `str` "puro" -> il DB row encoding (Pydantic
`ScheduleStatus = Literal[...]` -> JSON string) resta identico, nessuna
migrazione DB. Backward-compat: i caller possono passare `"running"` (str) o
`RuntimeStatus.RUNNING` (enum) — `set_runtime_status` normalizza.

Out-of-scope (NON portare qui):
  - `LogStep.status` valori (success/partial/failed/skipped) — enum logico
    separato per gli step di pipeline.
  - `LastExecution.status` valori (completed/partial/failed) — sotto-enum
    della snapshot di ultima esecuzione.
  - Lock-store marker `"running"` in concurrency_manager._set_nx — testo
    arbitrario nel DB lock table, non runtime status.
  - Return value `"acquired"/"queued"/"skipped"` di try_acquire — DSL interno
    del concurrency manager.
  - JSON response payload `{"status": "queued"}` esposto al FE — il FE legge
    string, non importa l'enum.
"""

from __future__ import annotations

from enum import StrEnum


class RuntimeStatus(StrEnum):
    """Stati canonici di `ScheduleRuntime.status`.

    Allineato 1:1 al `ScheduleStatus = Literal[...]` in
    `app/core/scheduler/models.py`. NON aggiungere valori senza aggiornare
    anche il Literal alias e il frontend status renderer.
    """

    IDLE = "idle"
    RUNNING = "running"
    QUEUED = "queued"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"
    ORPHAN = "orphan"


def coerce_runtime_status(value: "str | RuntimeStatus") -> str:
    """Accetta str o enum, ritorna sempre lo string canonico per il DB.

    Enum -> `.value`; stringa -> invariata per compatibilita' con caller
    ancora basati su literal.
    """
    if isinstance(value, RuntimeStatus):
        return value.value
    return value


__all__ = ["RuntimeStatus", "coerce_runtime_status"]
