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
});
