import { describe, expect, it } from 'vitest';

import {
  createParameterBindingEdge,
  dataFlowEdges,
  getNodeBindableTargets,
  hydrateParameterBindingEdges,
  isParameterBindingEdge,
  serializedBindingsForInput,
} from '../../src/utils/runtimeBindingEdges';

describe('runtime parameter binding edges', () => {
  it('derives a complete binding when dropped on a parameter handle', () => {
    const edge = createParameterBindingEdge({
      source: 'input-province',
      target: 'filter-1',
      targetHandle: 'parameter:value',
    });

    expect(edge).toEqual(expect.objectContaining({
      sourceHandle: 'parameter',
      targetHandle: 'parameter:value',
      data: expect.objectContaining({
        edge_kind: 'parameter_binding',
        target_field: 'config.value',
        target_parameter: 'value',
      }),
    }));
    expect(isParameterBindingEdge(edge)).toBe(true);
    expect(dataFlowEdges([edge, { id: 'data-1' }])).toEqual([{ id: 'data-1' }]);
  });

  it('exposes scalar transform parameters but blocks schema-changing fields', () => {
    const registry = {
      transforms: {
        formula: {
          parameters: [
            { id: 'formula', type: 'string', label: 'Formula' },
            { id: 'new_column', type: 'string', label: 'Nuova colonna' },
            { id: 'strict', type: 'boolean', label: 'Modalita strict' },
          ],
          output: { adds_column: 'new_column' },
        },
      },
    };
    const targets = getNodeBindableTargets({
      id: 'formula-1',
      type: 'transform',
      data: { transform_op: 'formula', config: {} },
    }, registry);

    expect(targets.map(target => target.value)).toEqual([
      'config.formula',
      'config.strict',
    ]);
  });

  it('roundtrips one input bound to multiple target nodes', () => {
    const input = {
      id: 'input-province',
      type: 'project_input',
      data: {
        input_kind: 'text',
        bindings: [
          { target_node_id: 'filter-1', target_field: 'config.value' },
          { target_node_id: 'filter-2', target_field: 'config.value' },
        ],
      },
    };
    const edges = hydrateParameterBindingEdges([input]);

    expect(edges).toHaveLength(2);
    expect(serializedBindingsForInput(input, edges)).toEqual(input.data.bindings);
  });
  // Stessa tabella del backend (eseguibili/binding_capabilities.NODE_TARGET_FAMILIES):
  // ogni bersaglio offerto e' letto dall'esecuzione del suo tipo di nodo.
  it('offers only the targets that the execution of the node type reads', () => {
    const values = node => getNodeBindableTargets(node).map(target => target.value);
    const filters = [
      { column: 'a', operator: '=', value: '' },
      { column: 'a', operator: 'IS NULL', value: '' },
    ];
    expect(values({ id: 't', type: 'tableNode', data: { filters } })).toEqual(['filters.0.value']);
    const having = { having_enabled: true, group_by_enabled: true, having_conditions: [{ value: '0' }] };
    expect(values({ id: 'r', type: 'resultNode', data: having })).toEqual(['having_conditions.0.value']);
    expect(values({ id: 'r', type: 'result', data: { ...having, group_by_enabled: false } })).toEqual([]);
    expect(values({ id: 'a', type: 'api_enrich', data: { static_parameters: { utente: '', 'a.b': '' } } }))
      .toEqual(['static_parameters.utente']);
    const everything = {
      filters, conditions: [{ value: '', result: '' }], value: '', default_value: '',
      static_parameters: { k: '' }, ...having,
    };
    ['staticValues', 'project_input', 'file_source', 'feature_source', 'carica', 'multi_join'].forEach(type => {
      expect(values({ id: 'x', type, data: everything })).toEqual([]);
    });
  });

  it('does not offer a step parameter whose dependency is not satisfied', () => {
    const registry = { transforms: { add_row_number: { parameters: [
      { id: 'order_column', type: 'column' },
      { id: 'ascending', type: 'boolean', depends_on: { param: 'order_column', value: '_any_' } },
      { id: 'start', type: 'number' },
    ] } } };
    const targets = getNodeBindableTargets({
      id: 'n', type: 'transform', data: { transform_op: 'add_row_number', config: { order_column: 'a' } },
    }, registry);
    expect(targets.map(target => target.value)).toEqual(['config.start']);
  });
});
