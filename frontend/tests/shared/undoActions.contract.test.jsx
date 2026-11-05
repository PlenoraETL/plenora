/**
 * Fase 5, unione T5+T6, R1.6 — undo raggruppato per AZIONE, non per tempo.
 *
 * Regola (store/useCanvasStore, takeSnapshot):
 *  (a) un gesto = tutte le mutazioni utente dello stesso turno sincrono:
 *      un solo Ctrl+Z;
 *  (b) una digitazione = mutazioni consecutive con la stessa chiave di gruppo
 *      (stesso nodo e stessi campi): un solo Ctrl+Z finche' non interviene
 *      un'altra azione, un undo/redo o un caricamento;
 *  (c) nessuna finestra di tempo: due azioni diverse a pochi ms restano due.
 * Enumerata su ogni azione utente dello store e su ProjectInputNode.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { act, render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({ categories: {}, transforms: {} }),
  isMultiInputOp: () => false,
}));

import useCanvasStore, { patchGroup } from '../../src/store/useCanvasStore';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import { labelEditGroup } from '../../src/components/nodes/result/standard/OutputColumnsSection';
import { ConfirmProvider } from '../../src/context/ConfirmContext';

const st = () => useCanvasStore.getState();
// Fine del turno sincrono (= fine del gesto).
const nextTurn = () => act(async () => { await Promise.resolve(); });

const A = { id: 'a', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'A', config: {} } };
const B = { id: 'b', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'B', config: {} } };
const E = { id: 'e1', source: 'a', target: 'b' };

function seed() {
  st().reset();
  st().init('p1', 'manipola');
  st().setReadOnly(false);
  st().loadGraph([A, B], [E]);
}

const undoCount = () => {
  let n = 0;
  while (st().past.length > 0) { st().undo(); n += 1; }
  return n;
};

beforeEach(seed);
afterEach(() => { cleanup(); });

// Ogni azione utente dello store, una per gesto.
const ACTIONS = {
  addNodes: () => st().addNodes({ id: 'c', type: 'transform', position: { x: 0, y: 0 }, data: {} }),
  addEdges: () => st().addEdges({ id: 'e2', source: 'b', target: 'a' }),
  removeEdges: () => st().removeEdges('e1'),
  editNodes: () => st().editNodes(ns => ns.map(n => ({ ...n, data: { ...n.data, x: 1 } }))),
  editEdges: () => st().editEdges(es => es.map(e => ({ ...e, data: { y: 1 } }))),
  editGraph: () => st().editGraph(({ nodes, edges }) => ({ nodes: nodes.slice(1), edges: edges.slice(1) })),
  updateEdge: () => st().updateEdge('e1', { data: { z: 1 } }),
  updateNodeData: () => st().updateNodeData('a', { label: 'A2' }),
  patchNodeConfig: () => st().patchNodeConfig('a', { k: 1 }),
  deleteNode: () => st().deleteNode('b'),
};

describe('R1.6: due azioni diverse sono due Ctrl+Z, senza finestra di tempo', () => {
  const names = Object.keys(ACTIONS);
  for (const first of names) {
    it(`${first} e poi un'altra azione (stesso millisecondo, turni diversi): due Ctrl+Z`, async () => {
      const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
      ACTIONS[first]();
      await nextTurn();
      st().updateNodeData('a', { title: 'altro campo' });
      await nextTurn();
      expect(undoCount()).toBe(2);
      expect(st().nodes.map(n => n.id)).toEqual(['a', 'b']);
      now.mockRestore();
    });
  }

  it('un gesto con piu\' mutazioni (stesso turno) e\' un solo Ctrl+Z', () => {
    st().takeSnapshot();
    st().deleteNode('a');
    st().deleteNode('b');
    st().removeEdges('e1');
    expect(st().past).toHaveLength(1);
    st().undo();
    expect(st().nodes.map(n => n.id)).toEqual(['a', 'b']);
    expect(st().edges.map(e => e.id)).toEqual(['e1']);
  });

  it('digitazione nello stesso campo (turni diversi): un solo Ctrl+Z', async () => {
    for (const label of ['A', 'An', 'Ann', 'Anno']) {
      st().updateNodeData('a', { label });
      await nextTurn();
    }
    expect(st().past).toHaveLength(1);
    st().undo();
    expect(st().nodes[0].data.label).toBe('A');
  });

  it('campo diverso, nodo diverso o un\'altra azione chiudono la digitazione', async () => {
    st().updateNodeData('a', { label: 'x' }); await nextTurn();
    st().updateNodeData('a', { label: 'xy' }); await nextTurn();
    st().updateNodeData('a', { title: 't' }); await nextTurn();
    st().updateNodeData('b', { title: 't' }); await nextTurn();
    st().addEdges({ id: 'e2', source: 'b', target: 'a' }); await nextTurn();
    st().updateNodeData('b', { title: 'tt' }); await nextTurn();
    expect(undoCount()).toBe(5);
  });

  it('dopo un undo la digitazione nello stesso campo e\' un\'azione nuova', async () => {
    st().updateNodeData('a', { label: 'x' }); await nextTurn();
    st().undo();
    st().updateNodeData('a', { label: 'y' }); await nextTurn();
    st().updateNodeData('a', { label: 'yz' }); await nextTurn();
    expect(st().past).toHaveLength(1);
  });

  it('le patch-funzione senza gruppo sono azioni; con `group` si raggruppano', async () => {
    st().updateNodeData('a', d => ({ n: (d.n || 0) + 1 })); await nextTurn();
    st().updateNodeData('a', d => ({ n: (d.n || 0) + 1 })); await nextTurn();
    expect(st().past).toHaveLength(2);
    seed();
    st().updateNodeData('a', () => ({ v: 'a' }), { group: 'g' }); await nextTurn();
    st().updateNodeData('a', () => ({ v: 'ab' }), { group: 'g' }); await nextTurn();
    expect(st().past).toHaveLength(1);
  });

  it('chiave di gruppo: nodo e campi della patch, mai il tempo', () => {
    expect(patchGroup('data', 'a', { label: 'x', _lastUpdate: 1 })).toBe('data:a:label');
    expect(patchGroup('data', 'a', { b: 1, a: 2 })).toBe('data:a:a,b');
    expect(patchGroup('data', 'a', () => ({}))).toBeNull();
    const rows = [{ name: 'x', keep: true, label: '' }, { name: 'y', keep: true, label: '' }];
    expect(labelEditGroup('o', rows, [{ ...rows[0], label: 'X' }, rows[1]])).toBe('output:o:label:x');
    expect(labelEditGroup('o', rows, [{ ...rows[0], keep: false }, rows[1]])).toBeNull();
    expect(labelEditGroup('o', rows, [rows[1], rows[0]])).toBeNull();
  });

  it('statico: nessuna finestra di tempo nello store', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/store/useCanvasStore.js'), 'utf-8');
    expect(src).not.toMatch(/SNAPSHOT_COALESCE_MS|lastSnapshotTs/);
    const body = src.slice(src.indexOf('takeSnapshot: (group'), src.indexOf("'takeSnapshot');"));
    expect(body).not.toMatch(/Date\.now|setTimeout|performance\.now/);
  });
});

describe('R1.6 su ProjectInputNode: un Ctrl+Z per azione', () => {
  const INPUT = { id: 'in1', type: 'project_input', position: { x: 0, y: 0 }, data: { input_key: 'anno', label: 'Anno', input_kind: 'text', validation: {} } };
  const BINDING = { id: 'b1', source: 'in1', target: 'a', sourceHandle: 'parameter', data: { edge_kind: 'parameter_binding', target_field: 'config.x' } };

  function mountInput() {
    st().reset();
    st().init('p1', 'manipola');
    st().setReadOnly(false);
    st().loadGraph([A, INPUT], [BINDING]);
    const Live = () => {
      const data = useCanvasStore(s => s.nodes.find(n => n.id === 'in1')?.data);
      return data ? <ProjectInputNode id="in1" data={data} /> : null;
    };
    return render(<ConfirmProvider><Live /></ConfirmProvider>);
  }

  it('cambio di tipo: tipo e collegamenti tornano con UN Ctrl+Z (editGraph)', async () => {
    mountInput();
    const kind = screen.getByDisplayValue(/testo/i);
    await act(async () => { fireEvent.change(kind, { target: { value: 'number' } }); });
    expect(st().edges).toHaveLength(0);
    expect(st().nodes[1].data.input_kind).toBe('number');
    expect(st().past).toHaveLength(1);
    act(() => st().undo());
    expect(st().nodes[1].data.input_kind).toBe('text');
    expect(st().edges.map(e => e.id)).toEqual(['b1']);
  });

  it('etichetta digitata in piu\' tasti: un Ctrl+Z; poi la chiave: un altro', async () => {
    const { container } = mountInput();
    const [label, key] = container.querySelectorAll('.pin__field input');
    for (const v of ['Anno d', 'Anno di', 'Anno di rif']) {
      await act(async () => { fireEvent.change(label, { target: { value: v } }); });
    }
    expect(st().past).toHaveLength(1);
    await act(async () => { fireEvent.change(key, { target: { value: 'anno_rif' } }); });
    await act(async () => { fireEvent.blur(key); });
    expect(st().past).toHaveLength(2);
    act(() => st().undo());
    act(() => st().undo());
    expect(st().nodes[1].data.label).toBe('Anno');
    expect(st().nodes[1].data.input_key).toBe('anno');
  });

  it('limiti della validazione: stesso campo raggruppato, campi diversi separati', async () => {
    mountInput();
    const min = screen.getByText('Lunghezza minima').parentElement.querySelector('input');
    const max = screen.getByText('Lunghezza massima').parentElement.querySelector('input');
    for (const v of ['1', '12']) await act(async () => { fireEvent.change(min, { target: { value: v } }); });
    for (const v of ['3', '30']) await act(async () => { fireEvent.change(max, { target: { value: v } }); });
    expect(st().nodes[1].data.validation).toMatchObject({ min_length: 12, max_length: 30 });
    expect(st().past).toHaveLength(2);
  });

  it('statico: niente removeEdges diretto ne\' patch della validazione dalla copia del render', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/components/nodes/ProjectInputNode.jsx'), 'utf-8');
    expect(src).toMatch(/editGraph\(/);
    expect(src).not.toMatch(/\.\.\.\(data\.validation/);
  });
});
