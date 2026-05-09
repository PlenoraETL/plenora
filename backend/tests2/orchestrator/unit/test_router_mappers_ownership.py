"""Orchestrator router response mapper ownership guards."""

import inspect

import pytest

from app.core.orchestrator import router
from app.core.orchestrator import router_mappers


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


def test_router_response_mappers_are_owned_by_router_mappers_module() -> None:
    assert router._task_info_to_dict is router_mappers.task_info_to_dict
    assert router._task_result_to_dict is router_mappers.task_result_to_dict
    assert router._task_result_project_id is router_mappers.task_result_project_id
    assert router_mappers.task_info_to_dict.__module__ == (
        "app.core.orchestrator.router_mappers"
    )

    router_source = inspect.getsource(router)
    assert "def _task_info_to_dict" not in router_source
    assert "def _task_result_to_dict" not in router_source
