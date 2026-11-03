import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import useColumnPropagation from '../../src/hooks/useColumnPropagation';

/** Fase 4 M4/M5: la previsione delle colonne a valle segue il backend. */
function run(nodes, edges, targetId = 'next') {
  let current = nodes;
  const setNodes = updater => { current = typeof updater === 'function' ? updater(current) : updater; };
  const { result } = renderHook(() => useColumnPropagation(setNodes));
  act(() => result.current.propagateColumns(current, edges));
  return current.find(n => n.id === targetId).data.inputColumns.map(c => c.name);
}

const src = (id, names) => ({ id, type: 'project_source', data: { columns: names.map(name => ({ name, type: 'str' })) } });
const tx = (id, op, config) => ({ id, type: 'transform', data: { transform_op: op, config } });
const next = { id: 'next', type: 'transform', data: { transform_op: 'filter', config: {} } };

describe('useColumnPropagation - pannelli M4/M5', () => {
  it('formula with items adds every new column; an empty list adds none', () => {
    const edges = [{ source: 's', target: 't' }, { source: 't', target: 'next' }];
    expect(run([src('s', ['a']), tx('t', 'formula', { items: [{ new_column: 'x', formula: 'a' }, { new_column: 'y', formula: 'x' }] }), next], edges))
      .toEqual(['a', 'x', 'y']);
    expect(run([src('s', ['a']), tx('t', 'formula', { items: [], formula: 'a' }), next], edges)).toEqual(['a']);
  });

  it('select_columns keeps unlisted columns at the end (backend semantics)', () => {
    const edges = [{ source: 's', target: 't' }, { source: 't', target: 'next' }];
    expect(run([src('s', ['a', 'b', 'c']), tx('t', 'select_columns', { columns: [{ name: 'c', label: 'C' }, { name: 'a', keep: false }] }), next], edges))
      .toEqual(['C', 'b']);
  });

  it('join with name_conflict uses the new plan, without _L/_R on kept names', () => {
    const edges = [
      { source: 'l', target: 'j', targetHandle: 'input-0' },
      { source: 'r', target: 'j', targetHandle: 'input-1' },
      { source: 'j', target: 'next' },
    ];
    const nodes = [
      src('l', ['id', 'nome']), src('r', ['ls_id', 'nome', 'w']),
      tx('j', 'join', { left_keys: ['id'], right_keys: ['ls_id'], name_conflict: 'keep_left' }), next,
    ];
    expect(run(nodes, edges)).toEqual(['id', 'nome', 'ls_id', 'w']);
  });
});
