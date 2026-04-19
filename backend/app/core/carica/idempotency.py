"""Thin re-export per retrocompat (C4 round 3).

L'implementazione e' stata promossa a `app.shared.idempotency` per
riuso cross-modulo (carica + arricchisci + futuri). Questo file resta
come alias per non rompere `from app.core.carica.idempotency import
is_already_completed` eventuali call site esistenti.
"""

from app.shared.idempotency import is_already_completed  # noqa: F401

__all__ = ["is_already_completed"]
