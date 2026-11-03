import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/api', () => ({ updateCanvas: vi.fn() }));

import useCanvasStore from '../../src/store/useCanvasStore';

/**
 * Contract audit R1 (03/11/2026): le mutazioni del canvas passano da TRE
 * canali espliciti dello store.
 *
 *  - LOAD     loadGraph            -> pulito, cronologia vuota
 *  - UTENTE   addNodes/addEdges/removeEdges/editNodes/editEdges/updateEdge/
 *             patchNodeConfig/updateNodeData -> snapshot undo + dirty,
 *             no-op in sola lettura
 *  - DERIVATO applyDerivedNodes/applyDerivedEdges, opzione {derived:true}
 *             -> mai dirty ne' snapshot
 *
 * Prima esistevano solo setNodes/setEdges con un'euristica sugli id: la
 * modifica di un join esistente non marcava dirty (salvataggio saltato) e
 * gli effetti automatici all'apertura marcavano dirty. Il test enumera
 * anche il sorgente per impedire di reintrodurre i setter generici.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');

const node = (id, data = {}) => ({ id, type: 'transform', position: { x: 0, y: 0 }, data });
const joinEdge = (id) => ({ id, source: 'a', target: 'b', data: { join_type: 'left' } });

function freshStore({ readOnly = false } = {}) {
  const store = useCanvasStore.getState();
  store.reset();
  useCanvasStore.setState({ isReadOnly: readOnly });
  useCanvasStore.getState().loadGraph([node('a'), node('b', { config: { x: 1 } })], [joinEdge('e1')]);
  return useCanvasStore.getState();
}

describe('canvas mutation channels contract', () => {
  beforeEach(() => vi.useRealTimers());

  it('the store exposes the explicit channels and no generic setters', () => {
    const state = useCanvasStore.getState();
    for (const action of ['loadGraph', 'addNodes', 'addEdges', 'removeEdges', 'editNodes', 'editEdges',
      'updateEdge', 'patchNodeConfig', 'updateNodeData', 'applyDerivedNodes', 'applyDerivedEdges']) {
      expect(typeof state[action], action).toBe('function');
    }
    expect(state.setNodes).toBeUndefined();
    expect(state.setEdges).toBeUndefined();
  });

  it('loadGraph leaves the canvas clean with an empty history', () => {
    const s = freshStore();
    expect(s.isDirty).toBe(false);
    expect(s.past).toEqual([]);
    expect(s.nodes.map(n => n.id)).toEqual(['a', 'b']);
  });

  it('editing an existing join (same ids) marks dirty and is undoable (R1.1)', () => {
    freshStore();
    useCanvasStore.getState().editEdges(eds => eds.map(e => ({ ...e, data: { ...e.data, join_type: 'inner' } })));
    let s = useCanvasStore.getState();
    expect(s.isDirty).toBe(true);
    expect(s.edges[0].data.join_type).toBe('inner');
    s.undo();
    s = useCanvasStore.getState();
    expect(s.edges[0].data.join_type).toBe('left');
  });

  it('every user action marks dirty', () => {
    const actions = {
      addNodes: s => s.addNodes(node('c')),
      addEdges: s => s.addEdges(joinEdge('e2')),
      removeEdges: s => s.removeEdges('e1'),
      editNodes: s => s.editNodes(nds => nds.slice(0, 1)),
      editEdges: s => s.editEdges(eds => eds),
      updateEdge: s => s.updateEdge('e1', { label: 'x' }),
      patchNodeConfig: s => s.patchNodeConfig('b', { y: 2 }),
      updateNodeData: s => s.updateNodeData('a', { title: 't' }),
    };
    for (const [name, run] of Object.entries(actions)) {
      run(freshStore());
      expect(useCanvasStore.getState().isDirty, name).toBe(true);
    }
  });

  it('derived updates never mark dirty nor snapshot (R1.8)', () => {
    freshStore();
    const s = useCanvasStore.getState();
    s.applyDerivedNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, columns: [{ name: 'c' }] } })));
    s.applyDerivedEdges(eds => eds.map(e => ({ ...e, style: { stroke: 'red' } })));
    s.updateNodeData('a', { columns: [] }, { derived: true });
    s.patchNodeConfig('b', { auto: true }, { derived: true });
    const after = useCanvasStore.getState();
    expect(after.isDirty).toBe(false);
    expect(after.past).toEqual([]);
    expect(after.nodes[1].data.config).toEqual({ x: 1, auto: true });
  });

  it('a derived updater that returns the same array changes nothing', () => {
    freshStore();
    const before = useCanvasStore.getState().nodes;
    useCanvasStore.getState().applyDerivedNodes(nds => nds);
    expect(useCanvasStore.getState().nodes).toBe(before);
  });

  it('patchNodeConfig merges with the CURRENT config: same-tick patches do not overwrite (R1.5)', () => {
    freshStore();
    const s = useCanvasStore.getState();
    s.patchNodeConfig('b', { geom_column: 'geom' }, { derived: true });
    s.patchNodeConfig('b', { expression_column_refs: [] });
    expect(useCanvasStore.getState().nodes[1].data.config).toEqual({ x: 1, geom_column: 'geom', expression_column_refs: [] });
  });

  it('user actions are no-ops in read-only mode, derived updates still apply', () => {
    freshStore({ readOnly: true });
    const s = useCanvasStore.getState();
    s.addNodes(node('c'));
    s.removeEdges('e1');
    s.editEdges(() => []);
    s.patchNodeConfig('b', { y: 2 });
    let after = useCanvasStore.getState();
    expect(after.nodes).toHaveLength(2);
    expect(after.edges).toHaveLength(1);
    expect(after.isDirty).toBe(false);
    s.applyDerivedNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, columns: [] } })));
    after = useCanvasStore.getState();
    expect(after.nodes[0].data.columns).toEqual([]);
  });

  it('no source file calls generic setNodes/setEdges or mutates through useReactFlow()', () => {
    const violations = [];
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).forEach((entry) => {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) return walk(full);
      if (!/\.(jsx?|tsx?)$/.test(entry.name)) return;
      const code = fs.readFileSync(full, 'utf-8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:])\/\/.*$/gm, '$1');
      const rel = path.relative(srcRoot, full);
      if (/\bset(Nodes|Edges)\s*\(/.test(code)) violations.push(`${rel}: setNodes/setEdges`);
      if (/\{[^}]*\bset(Nodes|Edges)\b[^}]*\}\s*=\s*useReactFlow\(/.test(code)) violations.push(`${rel}: useReactFlow setter`);
      if (/state\.set(Nodes|Edges)|s\.set(Nodes|Edges)/.test(code)) violations.push(`${rel}: store setter`);
    });
    walk(srcRoot);
    expect(violations).toEqual([]);
  });
});
