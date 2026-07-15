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

  it('propaga ai nodi downstream uno schema multi-input strutturale', () => {
    let currentNodes = [
      {
        id: 'left',
        type: 'project_source',
        data: { columns: [{ name: 'id', type: 'int' }, { name: 'geometry', type: 'geometry:point' }] },
      },
      {
        id: 'right',
        type: 'project_source',
        data: { columns: [{ name: 'id', type: 'int' }, { name: 'label', type: 'str' }, { name: 'geometry', type: 'geometry:point' }] },
      },
      {
        id: 'nearest',
        type: 'transform',
        data: { transform_op: 'geo_nearest', config: { output_column: 'distanza' } },
      },
      {
        id: 'next-transform',
        type: 'transform',
        data: { transform_op: 'filter', config: {} },
      },
    ];
    const edges = [
      { source: 'left', target: 'nearest', targetHandle: 'input-0' },
      { source: 'right', target: 'nearest', targetHandle: 'input-1' },
      { source: 'nearest', target: 'next-transform' },
    ];
    const setNodes = updater => {
      currentNodes = typeof updater === 'function' ? updater(currentNodes) : updater;
    };
    const { result } = renderHook(() => useColumnPropagation(setNodes));

    act(() => result.current.propagateColumns(currentNodes, edges));

    const next = currentNodes.find(node => node.id === 'next-transform');
    expect(next.data.inputColumns.map(col => col.name)).toEqual([
      'id',
      'geometry',
      'id_right',
      'label',
      'distanza',
    ]);
  });

  it('propaga l\'unione ordinata degli schemi per concat outer', () => {
    let currentNodes = [
      {
        id: 'first',
        type: 'project_source',
        data: { columns: [{ name: 'id', type: 'int' }, { name: 'nome', type: 'str' }] },
      },
      {
        id: 'second',
        type: 'project_source',
        data: { columns: [{ name: 'id', type: 'int' }, { name: 'citta', type: 'str' }] },
      },
      {
        id: 'concat',
        type: 'transform',
        data: { transform_op: 'concat', config: { join: 'outer' } },
      },
      {
        id: 'next-transform',
        type: 'transform',
        data: { transform_op: 'filter', config: {} },
      },
    ];
    const edges = [
      { source: 'first', target: 'concat', targetHandle: 'input-0' },
      { source: 'second', target: 'concat', targetHandle: 'input-1' },
      { source: 'concat', target: 'next-transform' },
    ];
    const setNodes = updater => {
      currentNodes = typeof updater === 'function' ? updater(currentNodes) : updater;
    };
    const { result } = renderHook(() => useColumnPropagation(setNodes));

    act(() => result.current.propagateColumns(currentNodes, edges));

    const next = currentNodes.find(node => node.id === 'next-transform');
    expect(next.data.inputColumns.map(col => col.name)).toEqual(['id', 'nome', 'citta']);
  });
});
