import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import useColumnPropagation from '../../src/hooks/useColumnPropagation';

function propagate(operation, config, sourceColumns) {
  let currentNodes = [
    { id: 'source', type: 'project_source', data: { columns: sourceColumns } },
    { id: 'transform', type: 'transform', data: { transform_op: operation, config } },
    { id: 'next', type: 'transform', data: { transform_op: 'filter', config: {} } },
  ];
  const edges = [
    { source: 'source', target: 'transform' },
    { source: 'transform', target: 'next' },
  ];
  const setNodes = updater => {
    currentNodes = typeof updater === 'function' ? updater(currentNodes) : updater;
  };
  const { result } = renderHook(() => useColumnPropagation(setNodes));

  act(() => result.current.propagateColumns(currentNodes, edges));

  return currentNodes.find(node => node.id === 'next').data.inputColumns;
}

describe('useColumnPropagation - semantica runtime', () => {
  it.each([
    ['formula', {}, 'calculated', 'real'],
    ['concat_columns', {}, 'concatenated', 'str'],
    ['conditional', {}, 'result', 'str'],
    ['geo_envelope', {}, 'envelope', 'geometry:polygon'],
  ])('applica il default output di %s', (operation, config, outputName, outputType) => {
    const output = propagate(operation, config, [
      { name: 'id', type: 'int' },
      { name: 'geometry', type: 'geometry:point' },
    ]);

    expect(output.at(-1)).toMatchObject({ name: outputName, type: outputType });
  });

  it('mantiene lo schema se aggregate non ha un group_by valido', () => {
    const output = propagate('aggregate', { aggregations: [] }, [
      { name: 'categoria', type: 'str' },
      { name: 'importo', type: 'int' },
    ]);

    expect(output.map(col => col.name)).toEqual(['categoria', 'importo']);
  });

  it('propaga count e nomi flat per le aggregazioni multiple', () => {
    const source = [
      { name: 'categoria', type: 'str' },
      { name: 'importo', type: 'int' },
    ];
    const countOutput = propagate('aggregate', {
      group_by: ['categoria'],
      aggregations: [],
    }, source);
    expect(countOutput.map(col => col.name)).toEqual(['categoria', 'count']);

    const multiOutput = propagate('aggregate', {
      group_by: ['categoria'],
      aggregations: [
        { column: 'importo', function: 'sum' },
        { column: 'importo', function: 'mean' },
      ],
    }, source);
    expect(multiOutput).toEqual([
      expect.objectContaining({ name: 'categoria', type: 'str' }),
      expect.objectContaining({ name: 'importo_sum', type: 'int' }),
      expect.objectContaining({ name: 'importo_mean', type: 'real' }),
    ]);
  });

  it('gestisce pivot multi-indice solo con mapping deterministico', () => {
    const source = [
      { name: 'azienda', type: 'str' },
      { name: 'anno', type: 'int' },
      { name: 'tipo', type: 'str' },
      { name: 'valore', type: 'real' },
    ];
    const incomplete = propagate('pivot', { index_col: 'azienda' }, source);
    expect(incomplete.map(col => col.name)).toEqual(['azienda', 'anno', 'tipo', 'valore']);

    const output = propagate('pivot', {
      index_col: 'azienda, anno',
      pivot_col: 'tipo',
      value_col: 'valore',
      mapping: { A: 'valore_a', B: 'valore_b' },
    }, source);
    expect(output).toEqual([
      expect.objectContaining({ name: 'azienda', type: 'str' }),
      expect.objectContaining({ name: 'anno', type: 'int' }),
      expect.objectContaining({ name: 'valore_a', type: 'real' }),
      expect.objectContaining({ name: 'valore_b', type: 'real' }),
    ]);
  });
});
