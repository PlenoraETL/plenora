import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import useColumnPropagation from '../../src/hooks/useColumnPropagation';

describe('useColumnPropagation - alias aggregate downstream', () => {
  it('propaga il nome output scelto al nodo successivo', () => {
    let currentNodes = [
      {
        id: 'source',
        type: 'project_source',
        data: {
          columns: [
            { name: 'categoria', type: 'str' },
            { name: 'importo', type: 'real' },
          ],
        },
      },
      {
        id: 'aggregate',
        type: 'transform',
        data: {
          transform_op: 'aggregate',
          config: {
            group_by: ['categoria'],
            aggregations: [
              { column: 'importo', function: 'sum', alias: 'aggregazioni' },
            ],
          },
        },
      },
      {
        id: 'next-transform',
        type: 'transform',
        data: { transform_op: 'filter', config: {} },
      },
    ];
    const edges = [
      { source: 'source', target: 'aggregate' },
      { source: 'aggregate', target: 'next-transform' },
    ];
    const setNodes = vi.fn((updater) => {
      currentNodes = typeof updater === 'function' ? updater(currentNodes) : updater;
    });
    const { result } = renderHook(() => useColumnPropagation(setNodes));

    act(() => result.current.propagateColumns(currentNodes, edges));

    const aggregate = currentNodes.find(node => node.id === 'aggregate');
    const next = currentNodes.find(node => node.id === 'next-transform');
    expect(aggregate.data.columns.map(col => col.name)).toEqual([
      'categoria',
      'aggregazioni',
    ]);
    expect(next.data.inputColumns.map(col => col.name)).toEqual([
      'categoria',
      'aggregazioni',
    ]);
    expect(next.data.inputColumns.map(col => col.name)).not.toContain('importo');
  });
});
