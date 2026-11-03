"""B6: run the real enrichment loop, replacing only external boundaries."""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.arricchisci import executor as module
from app.core.arricchisci import canvas_orchestrator
from app.core.projects.canvas_models import CanvasState

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def canvas_fixture():
    def node(id, type, **kwargs):
        return dict(id=id, type=type, position=dict(x=0, y=0), **kwargs)
    return CanvasState.model_validate(dict(nodes=[
        node('src', 'project_source', source_project_id='upstream'),
        node('api1', 'api_enrich', api_id='one'),
        node('api2', 'api_enrich', api_id='two'),
        node('out', 'result'),
    ], edges=[dict(id=a+b, source=a, target=b) for a,b in [('src','api1'), ('api1','api2'), ('api2','out')]]))


@pytest.mark.parametrize('node_id,expected_calls', [('src', []), ('api1', ['api1']), ('out', ['api1', 'api2']), (None, ['api1', 'api2'])])
def test_scoped_canonical_loop(monkeypatch, node_id, expected_calls):
    canvas = canvas_fixture()
    before = canvas.model_dump_json()
    svc = MagicMock()
    svc.list_api_connections_decrypted.return_value = {'one': {}, 'two': {}}
    def load(node, force, *, ctx):
        assert ctx.group_id == 'tenant' and ctx.preview_limit == 2
        return pd.DataFrame({'n': [1, 2, 3]}).head(ctx.preview_limit)
    svc._load_source_project.side_effect = load
    svc._find_final_result.side_effect = lambda results, node, order: results[node['id']]
    calls = []
    def enrich(svc, node, connections, frame):
        calls.append(node['id'])
        assert len(frame) <= 2
        return frame.assign(**{node['id']: [10, 20]})
    monkeypatch.setattr(module, 'api_connection_service', svc)
    monkeypatch.setattr(canvas_orchestrator, 'run_single_enricher', enrich)
    real_run = module.execute_canvas_enrichment
    run = MagicMock(wraps=real_run)
    monkeypatch.setattr(module, 'execute_canvas_enrichment', run)
    context = {'group_id': 'tenant'}
    if node_id is not None:
        context['preview_node_id'] = node_id
    result = module.EnrichExecutor(MagicMock()).preview('p', canvas, 2, execution_context=context)
    assert result['success'], result
    assert calls == expected_calls
    assert [c['name'] for c in result['columns']] == ['n', *expected_calls]
    assert result['preview_count'] == 2
    assert result.get('approximate') == (True if node_id is not None else None)
    passed = run.call_args.kwargs['canvas']
    if node_id in ('src', 'api1'):
        assert passed['nodes'][-1]['type'] == 'result'
        assert passed['edges'][-1]['source'] == node_id
        assert 'api2' not in [n['id'] for n in passed['nodes']]
    if node_id is None:
        assert passed == canvas.model_dump()
        assert result == {'success': True, 'columns': [{'name': c, 'type': 'int'} for c in ['n', 'api1', 'api2']],
                          'rows': [{'n': 1, 'api1': 10, 'api2': 10}, {'n': 2, 'api1': 20, 'api2': 20}],
                          'total_count': 2, 'preview_count': 2, 'truncated': False}
    assert canvas.model_dump_json() == before
    svc._save_and_build_dataframe_response.assert_not_called()


def test_unknown_node_never_runs_loop(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(module, 'execute_canvas_enrichment', run)
    with pytest.raises(ValueError, match='non trovato'):
        module.EnrichExecutor(MagicMock()).preview('p', canvas_fixture(), 2, execution_context={'preview_node_id': 'missing'})
    run.assert_not_called()
