"""I/O policy boundary — env-driven, no implicit state.

Policy enum + lettori che mappano la variabile d'ambiente
`PLENORA_IO_DISK_POLICY` a comportamenti consentiti.

Valori supportati:
  - `memory_only`: vietato qualunque disk write (test/staging RAM-only)
  - `temp_only_explicit` (default): temp disk solo via `io.materialize`
    con `reason=` obbligatorio; vietato persistent disk
  - `local_dev_allowed`: anche persistent disk locale (sviluppo)

Default = `temp_only_explicit`: ambiente di sviluppo/staging tipico
con DATA_DIR montato come temp, no persistent local stores. Le fasi
successive del refactor possono spostare il default a `memory_only`
quando i flussi principali saranno migrati.
"""

from __future__ import annotations

from enum import StrEnum

from app.environment import get_env


class DiskWritePolicy(StrEnum):
    """Policy enum canonica per le disk writes runtime."""

    MEMORY_ONLY = "memory_only"
    TEMP_ONLY_EXPLICIT = "temp_only_explicit"
    LOCAL_DEV_ALLOWED = "local_dev_allowed"


_DEFAULT_POLICY = DiskWritePolicy.TEMP_ONLY_EXPLICIT


def get_disk_policy() -> DiskWritePolicy:
    """Return the current disk write policy.

    Letto da `PLENORA_IO_DISK_POLICY`. Valori invalidi -> default
    `temp_only_explicit` con warning silente (no raise: il policy
    boundary non deve mai bloccare l'import del modulo applicativo).
    Caller che vogliono fail-fast su valore invalido devono usare
    direttamente `DiskWritePolicy(get_env(...))`.
    """
    raw = (get_env("PLENORA_IO_DISK_POLICY") or "").strip().lower()
    try:
        return DiskWritePolicy(raw) if raw else _DEFAULT_POLICY
    except ValueError:
        return _DEFAULT_POLICY


def is_temp_disk_allowed() -> bool:
    """True quando la policy consente temp disk via `io.materialize`."""
    policy = get_disk_policy()
    return policy != DiskWritePolicy.MEMORY_ONLY


def is_persistent_disk_allowed() -> bool:
    """True solo in `local_dev_allowed`."""
    policy = get_disk_policy()
    return policy == DiskWritePolicy.LOCAL_DEV_ALLOWED


__all__ = [
    "DiskWritePolicy",
    "get_disk_policy",
    "is_persistent_disk_allowed",
    "is_temp_disk_allowed",
]
