"""Contracts for scheduler destination preflight extraction."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.scheduler.scheduler_preflight import pre_check_destination_connection


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parents[3]
EXECUTION_MANAGER = BACKEND_ROOT / "app" / "core" / "scheduler" / "execution_manager.py"
PREFLIGHT = BACKEND_ROOT / "app" / "core" / "scheduler" / "scheduler_preflight.py"


def _service(connection=None, result=None):
    service = MagicMock()
    service.get_connection.return_value = connection
    service.test_connection.return_value = result
    return service


def test_preflight_returns_true_without_destination(schedule_config):
    config = schedule_config.model_copy(update={"destination": None})
    service = _service()

    assert pre_check_destination_connection(config, service) is True
    service.get_connection.assert_not_called()
    service.test_connection.assert_not_called()


def test_preflight_tests_configured_destination(schedule_config):
    service = _service(connection=object(), result=SimpleNamespace(success=True))

    assert pre_check_destination_connection(schedule_config, service) is True
    service.get_connection.assert_called_once_with("conn-1")
    service.test_connection.assert_called_once_with("conn-1")


def test_preflight_fails_when_connection_missing(schedule_config):
    service = _service(connection=None)

    assert pre_check_destination_connection(schedule_config, service) is False
    service.get_connection.assert_called_once_with("conn-1")
    service.test_connection.assert_not_called()


def test_preflight_logic_stays_outside_execution_manager():
    execution_src = EXECUTION_MANAGER.read_text(encoding="utf-8")
    preflight_src = PREFLIGHT.read_text(encoding="utf-8")
    tree = ast.parse(execution_src)

    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_pre_check_connection"
    )
    calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "pre_check_destination_connection"
    ]

    assert len(calls) == 1
    assert "connection_service.test_connection" not in execution_src
    assert "connection_service.get_connection" not in execution_src
    assert "connection_service.test_connection" in preflight_src
    assert "connection_service.get_connection" in preflight_src
