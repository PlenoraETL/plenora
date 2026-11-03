"""B6 HTTP contract: validation must happen before enqueue."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException
import pytest

from app.core.orchestrator import router
from app.core.orchestrator.execution_contract import PipelineExecutionContract

pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.mark.parametrize('project_type', ['estrai', 'arricchisci', 'manipola'])
@pytest.mark.parametrize('node_id', [None, 'src', 'missing'])
def test_preview_router(monkeypatch, project_type, node_id):
    contract = PipelineExecutionContract(project_id='p', project_type=project_type, group_id='g',
        canvas={'nodes': [{'id': 'src', 'type': 'source', 'position': {'x': 0, 'y': 0}}], 'edges': []})
    monkeypatch.setattr(router, '_orchestrator', object())
    monkeypatch.setattr(router, '_assert_project_group', lambda *args: None)
    monkeypatch.setattr(router, '_project_service', lambda: object())
    monkeypatch.setattr(router, 'build_pipeline_execution_contract', lambda **kwargs: contract)
    enqueue = MagicMock(return_value={'task_id': 'task'})
    monkeypatch.setattr(router.task_service_module.task_service, 'create_preview_task', enqueue)
    monkeypatch.setattr(router, '_task_info_to_dict', lambda info: info)
    kwargs = dict(project_id='p', limit=2, current_user=SimpleNamespace(user_id='u'), node_id=node_id)
    if node_id == 'missing':
        with pytest.raises(HTTPException) as exc:
            router.preview_project(**kwargs)
        assert exc.value.status_code == 400
        enqueue.assert_not_called()
    else:
        assert router.preview_project(**kwargs) == {'task_id': 'task', 'async': True}
        payload = enqueue.call_args.kwargs['execution_contract']
        if node_id is None:
            assert payload == contract.to_payload()
        else:
            assert payload['runtime_context']['preview_node_id'] == node_id
