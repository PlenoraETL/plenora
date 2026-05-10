"""Intermediate run idempotency check contract."""

from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


def test_run_idempotency_is_already_completed_exists():
    """Il modulo deve essere importabile dal dominio intermediate."""
    from app.core.intermediate.run_idempotency import is_already_completed

    assert callable(is_already_completed)


def test_run_idempotency_returns_false_when_missing():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch("app.core.intermediate.runs_db.get_run", return_value=None):
        assert is_already_completed("x") is False


def test_run_idempotency_returns_true_for_completed():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "x", "status": "completed"},
    ):
        assert is_already_completed("x") is True


def test_run_idempotency_false_for_failed_or_running():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "x", "status": "failed"},
    ):
        assert is_already_completed("x") is False
    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "x", "status": "running"},
    ):
        assert is_already_completed("x") is False


def test_run_idempotency_swallows_db_error_fail_open():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        side_effect=RuntimeError("db down"),
    ):
        assert is_already_completed("x") is False


def test_run_idempotency_returns_false_on_empty_run_id():
    from app.core.intermediate.run_idempotency import is_already_completed

    assert is_already_completed("") is False
    assert is_already_completed(None) is False


def test_shared_idempotency_module_is_removed():
    shared_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "shared"
        / "idempotency.py"
    )

    assert not shared_path.exists()
