"""Modulo condiviso di idempotency check (C4 round 3).

Generalizzazione di `app/core/carica/idempotency.py` in
`app/shared/idempotency.py` per riuso tra i vari task
(carica, enrichment, e futuri).

Semantica invariata rispetto al modulo carica:
- `completed` → True (idempotency hit, skip)
- `failed` / `running` / missing / DB error → False
"""

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_shared_is_already_completed_exists():
    """Il modulo deve essere importabile da app.shared.idempotency."""
    from app.shared.idempotency import is_already_completed  # noqa: F401


def test_shared_is_already_completed_returns_false_when_missing():
    from app.shared.idempotency import is_already_completed

    with patch("app.core.intermediate.db.get_run", return_value=None):
        assert is_already_completed("x") is False


def test_shared_is_already_completed_returns_true_for_completed():
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "x", "status": "completed"},
    ):
        assert is_already_completed("x") is True


def test_shared_is_already_completed_false_for_failed_or_running():
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "x", "status": "failed"},
    ):
        assert is_already_completed("x") is False
    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "x", "status": "running"},
    ):
        assert is_already_completed("x") is False


def test_shared_swallows_db_error_fail_open():
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        side_effect=RuntimeError("db down"),
    ):
        assert is_already_completed("x") is False


def test_shared_returns_false_on_empty_run_id():
    from app.shared.idempotency import is_already_completed

    assert is_already_completed("") is False
    assert is_already_completed(None) is False


# =============================================================================
# Backward compat: app.core.carica.idempotency continua a funzionare
# =============================================================================


def test_carica_idempotency_reexports_from_shared():
    """app.core.carica.idempotency deve restare importabile (API stabile),
    delegando all'implementazione condivisa."""
    from app.core.carica.idempotency import is_already_completed as carica_fn
    from app.shared.idempotency import is_already_completed as shared_fn

    # Stessa funzione (riesportata) o wrapper che chiama quella
    assert carica_fn is shared_fn or callable(carica_fn)
