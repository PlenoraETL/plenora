"""Contratti E1-E4: aspetti, opzionalità e oracolo eseguibile."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.projects.canvas_validation import validate_canvas
from app.core.manipola.node_validator import register_manipola_validator, ManipolaNodeValidator
from app.core.arricchisci.node_validator import ArricchisciNodeValidator
from app.core.carica.node_validator import CaricaNodeValidator
from app.shared.execution.schema_column_plans import AGGREGATION_TYPES, aggregation_output_type
from tests2.contracts.generate_aggregation_types import generate
from tests2.contracts.test_inference_runtime_parity import canvas_of, infer_canvas


def test_enumerate_inferred_schema_status_consumers():
    root = Path(__file__).parents[2] / 'app'
    violations = []
    consumers = []
    for path in root.rglob('*.py'):
        source = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        # Enumera tutti i lettori del contratto e dell'inferenza, inclusi nuovi validatori.
        if not any(token in source for token in ('InferredSchema', 'infer_node_schema_typed', 'names_status')):
            continue
        consumers.append(path)
        if path.name == 'schema_types.py':
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == 'status':
                violations.append(f'{path.relative_to(root)}:{node.lineno}')
    assert len(consumers) >= 5
    assert not violations, violations


@pytest.mark.parametrize('op,config', [
    ('uuid_generator', {}), ('string_length', {'column': 't'}),
    ('window_function', {'column': 'a'}),
    ('split_column', {'column': 't', 'new_columns': ['x', 'y']}),
])
def test_optional_columns_do_not_hide_absent_reference(op, config):
    register_manipola_validator()
    nodes = [{'id': 's', 'type': 'file_source', 'file_source_id': 'real-file',
              'data': {'columns': [{'name': 'a', 'type': 'int'}, {'name': 't', 'type': 'str'}]}},
             {'id': 'm', 'type': 'transform', 'transform_op': op, 'config': config},
             {'id': 'f', 'type': 'transform', 'transform_op': 'filter', 'config': {'column': 'inesistente', 'value': '1'}},
             {'id': 'r', 'type': 'result'}]
    canvas = canvas_of(nodes, [{'id': str(i), 'source': a['id'], 'target': b['id']} for i, (a,b) in enumerate(zip(nodes,nodes[1:]))])
    report = validate_canvas(canvas, project_id='p', project_type='manipola')
    assert any(i.severity == 'error' and 'inesistente' in i.message for i in report.issues)
    typed = infer_canvas(canvas, 'm')
    assert typed.names_status == 'certain'
    optional = [c['name'] for c in typed.columns if c.get('optional')]
    assert optional
    assert not any(c.get('optional') for c in typed.columns if c['name'] in {'a', 't'})
    for name in optional:
        node = SimpleNamespace(id='f', type='transform', transform_op='filter', data={}, config={'column': name, 'value': '1'})
        assert not any(i.severity == 'error' for i in ManipolaNodeValidator().validate_node(node, upstream=typed))


def test_real_source_missing_references_enrich_and_load():
    canvas = canvas_of([{'id': 's', 'type': 'file_source', 'file_source_id': 'real-file',
        'data': {'columns': [{'name': 'a', 'type': 'int'}]}}], [])
    upstream = infer_canvas(canvas, 's')
    enrich = SimpleNamespace(id='e', data={'api_id': 'api', 'parameters': [{'name': 'p', 'required': True}], 'param_mapping': {'p': 'inesistente'}})
    assert any(i.code == 'ARRICCHISCI_PARAM_MAPPING_MISSING' and i.severity == 'error' for i in ArricchisciNodeValidator().validate_node(enrich, upstream=upstream))
    load = SimpleNamespace(id='l', type='carica_db', data={'column_mappings': [{'source_column': 'inesistente', 'dest_column': 'dest'}]})
    assert any(i.code == 'mapping.source_missing' and i.severity == 'error' for i in CaricaNodeValidator().validate_node(load, upstream=upstream))


def test_generated_aggregation_table_matches_execution():
    assert generate() == AGGREGATION_TYPES


@pytest.mark.parametrize('function,kind', [(f,k) for f,row in AGGREGATION_TYPES.items() for k,v in row.items() if v == 'error'])
def test_invalid_aggregation_is_reported(function, kind):
    from app.shared.execution.schema_types import InferredSchema
    upstream = InferredSchema(columns=[{'name': 'v', 'type': kind}, {'name': 'g', 'type': 'int'}])
    node = SimpleNamespace(id='t', type='transform', transform_op='aggregate', data={}, config={'group_by': ['g'], 'aggregations': [{'column': 'v', 'function': function}]})
    # A3: lo riporta l'esecuzione a vuoto (stesso messaggio del runtime) oppure,
    # quando zero righe non portano l'errore di tipo, la tabella generata.
    issues = ManipolaNodeValidator().validate_node(node, upstream=upstream)
    assert any(i.code in {'aggregation.unsupported', 'preparation.invalid'} and i.severity == 'error' for i in issues)
    with pytest.raises(TypeError):
        aggregation_output_type(function, kind)

@pytest.mark.parametrize('function,kind,outcome', [(f,k,v) for f,row in AGGREGATION_TYPES.items() for k,v in row.items() if v != 'error'])
def test_inference_uses_generated_aggregation_table(function, kind, outcome):
    from app.core.projects.schema_inference import SchemaInferenceService
    from app.shared.execution.schema_types import ColumnSchema
    service = SchemaInferenceService(None)
    schema = service._infer_transform_schema(
        SimpleNamespace(id='t', transform_op='aggregate', data={}, config={
            'group_by': ['g'], 'aggregations': [{'column': 'v', 'function': function}]}),
        [[ColumnSchema('g', 'int'), ColumnSchema('v', kind)]], {})
    assert schema[1].type == outcome


def test_geo_nearest_optional_and_passthrough():
    from tests2.contracts.test_a1_correction import canvas_frames
    from tests2.contracts.test_inference_runtime_parity import frames
    from app.core.projects.schema_inference import SchemaInferenceService
    from app.shared.execution.schema_types import ColumnSchema
    typed = infer_canvas(canvas_frames('geo_nearest', frames('geo_nearest', False),
        {'left_geom_column': 'geometry', 'right_geom_column': 'geometry'}), 't')
    assert typed.names_status == 'certain'
    assert any(c.get('optional') for c in typed.columns)
    schema = [ColumnSchema(**c) for c in typed.columns]
    service = SchemaInferenceService(None)
    copied = service._infer_transform_schema(SimpleNamespace(id='f', transform_op='filter',
        data={}, config={'column': 'id', 'value': '1'}), [schema], {})
    assert [c.optional for c in copied] == [c.optional for c in schema]
