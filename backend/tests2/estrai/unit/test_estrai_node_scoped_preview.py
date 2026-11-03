"""B6: scoped SQL uses the canonical generator and bounded DB preview."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.estrai.executor import EstraiExecutor
from app.core.estrai.query_generator import QueryGenerator
from app.core.projects.canvas_models import CanvasState

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def canvas_fixture():
    def node(id, type, **kwargs):
        return dict(id=id, type=type, position=dict(x=0, y=0), **kwargs)
    return CanvasState.model_validate(dict(nodes=[
        node('a', 'source', table_name='customers', alias='a',
             columns=[dict(name='id', type='int'), dict(name='secret', visible=False)],
             filters=[dict(column='id', operator='>', value='1')]),
        node('b', 'source', table_name='orders', alias='b', columns=[dict(name='amount', type='int')]),
        node('out', 'result', distinct=True, limit=1),
        node('unused', 'source', table_name='unrelated'),
    ], edges=[dict(id='ab', source='a', target='b', join_type='inner', source_column='id', target_column='customer_id'),
              dict(id='bo', source='b', target='out', join_type='output')]))


@pytest.mark.parametrize('node_id,expected', [('a', ['a']), ('b', ['a', 'b']), ('out', ['a', 'b', 'out']), (None, ['a', 'b', 'out', 'unused'])])
def test_scoped_sql(monkeypatch, node_id, expected):
    canvas = canvas_fixture()
    before = canvas.model_dump_json()
    db = MagicMock()
    db.execute_raw.return_value = ([(2,), (3,), (4,)], ['id'])
    generator = QueryGenerator()
    generate = MagicMock(wraps=generator.generate_sql)
    monkeypatch.setattr(generator, 'generate_sql', generate)
    executor = EstraiExecutor(MagicMock(), db, generator, MagicMock())
    monkeypatch.setattr(executor, '_resolve_connection', lambda _: (SimpleNamespace(type='postgres', db_schema='public'), 'db'))
    visible = MagicMock()
    monkeypatch.setattr(executor, '_assert_connection_visible_for_project', visible)
    context = dict(default_connection_id='conn', group_id='tenant')
    if node_id is not None:
        context['preview_node_id'] = node_id
    result = executor.preview('p', canvas, 2, execution_context=context)
    assert result['success'], result
    nodes, edges, terminal = generate.call_args.args
    assert [n.id for n in nodes if not n.id.startswith('__preview_result__')] == expected
    if node_id in ('a', 'b'):
        assert next(n for n in nodes if n.id == terminal).type == 'result'
        assert any(e.source == node_id and e.target == terminal for e in edges)
    else:
        assert terminal == 'out'
    sql = result['query']
    assert 'customers' in sql and 'secret' not in sql and 'unrelated' not in sql
    assert ('JOIN' in sql) == (node_id != 'a')
    assert ('DISTINCT' in sql) == (node_id in ('out', None))
    assert db.execute_raw.call_args.args[2]  # Filters remain parameterized.
    assert result['preview_count'] == (1 if node_id in ('out', None) else 2)
    assert result['truncated'] is True
    assert result.get('approximate') == (True if node_id is not None else None)
    visible.assert_called_once()
    assert canvas.model_dump_json() == before
    if node_id is None:
        assert nodes is canvas.nodes and edges is canvas.edges
        assert set(result) == {'success', 'columns', 'rows', 'total_count', 'preview_count', 'truncated', 'query'}


def test_unknown_node_fails_before_connection():
    executor = EstraiExecutor(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    with pytest.raises(ValueError, match='non trovato'):
        executor.preview('p', canvas_fixture(), 2, execution_context={'preview_node_id': 'missing'})
    executor.connection_service.get_connection.assert_not_called()
