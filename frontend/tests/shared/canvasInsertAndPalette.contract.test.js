import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const { registryJson } = vi.hoisted(() => ({ registryJson: { value: null } }));
vi.mock('../../src/api/transforms', () => ({ getTransformRegistry: vi.fn(async () => registryJson.value) }));

import useCanvasStore from '../../src/store/useCanvasStore';
import { findFreePosition, planInsertAfter, NODE_W, NODE_H, COLUMN_STEP } from '../../src/utils/canvasPlacement';
import { KNOWN_MULTI_INPUT_OPS, isMultiInputOp, fetchTransformRegistry, resetTransformRegistry } from '../../src/utils/transformRegistry';
import { canvasContentKey } from '../../src/utils/canvasContentKey';
import { buildManipolaPaletteGroups } from '../../src/components/canvas/manipolaPalette';
import { displayIcon, nodeDescriptor } from '../../src/components/canvas/nodeDescriptors';
import { filterPalette } from '../../src/components/canvas/NodePalette';

/**
 * Contract Fase 4, correzione dopo il test dal vivo di M2+M3: una regola
 * per ogni classe di difetti, verificata su TUTTI i casi (registry vero,
 * ogni operazione, ogni aggiunta).
 */
const registry = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/app/core/manipola/transforms_registry.json'), 'utf-8'));
const ops = Object.entries(registry.transforms);
registryJson.value = registry;
const nonAscii = (s) => [...String(s)].some(ch => ch.codePointAt(0) > 127);

const n = (id, x = 0, y = 0, type = 'transform') => ({ id, type, position: { x, y }, data: {} });
const e = (source, target, extra = {}) => ({ id: `${source}-${target}`, source, target, ...extra });
const makeEdge = (p) => ({ ...p, id: `${p.source}-${p.target}` });
const overlapping = (nodes) => {
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const A = nodes[i]; const B = nodes[j];
      const [aw, ah] = [A.measured?.width || NODE_W, A.measured?.height || NODE_H];
      const [bw, bh] = [B.measured?.width || NODE_W, B.measured?.height || NODE_H];
      if (A.position.x < B.position.x + bw && B.position.x < A.position.x + aw
        && A.position.y < B.position.y + bh && B.position.y < A.position.y + ah) return [A.id, B.id];
    }
  }
  return null;
};

describe('multi-input operations: one source', () => {
  it('the fallback list is exactly the registry "multi" set', () => {
    const fromRegistry = ops.filter(([, d]) => d.inputs === 'multi').map(([op]) => op).sort();
    expect([...KNOWN_MULTI_INPUT_OPS].sort()).toEqual(fromRegistry);
  });

  it('every operation gets two inputs iff the registry says multi (loaded or not)', () => {
    for (const [op, def] of ops) {
      expect(isMultiInputOp(op, registry)).toBe(def.inputs === 'multi');
      expect(isMultiInputOp(op, null)).toBe(def.inputs === 'multi');
      const ids = nodeDescriptor('transform').defaultHandles({ transform_op: op })
        .filter(h => h.type === 'target').map(h => h.id);
      expect(ids.length, op).toBe(def.inputs === 'multi' ? 2 : 1);
    }
  });
});

describe('icons: always a glyph', () => {
  it('every registry icon and every palette item shows a non-text glyph', () => {
    for (const [op, def] of ops) expect(nonAscii(displayIcon(def.icon)), op).toBe(true);
    for (const item of buildManipolaPaletteGroups(registry).flatMap(g => g.items)) {
      expect(nonAscii(item.icon), item.id).toBe(true);
    }
    expect(displayIcon('columns')).not.toBe('columns');
    expect(displayIcon(undefined)).toBe('⚙️');
  });

  it('the cached registry already carries glyphs, for every consumer (TransformNode too)', async () => {
    resetTransformRegistry();
    const cached = await fetchTransformRegistry();
    for (const [op, def] of Object.entries(cached.transforms)) expect(nonAscii(def.icon), op).toBe(true);
    expect(registry.transforms.select_columns.icon).toBe('columns'); // il JSON originale non e' toccato
    resetTransformRegistry();
  });
});

describe('palette search ranks by name', () => {
  const groups = buildManipolaPaletteGroups(registry);

  it('searching the full name of ANY operation puts it first', () => {
    for (const [op, def] of ops) {
      const first = filterPalette(groups, def.name)[0]?.items[0];
      expect(first?.label, op).toBe(def.name);
    }
  });

  it('"filtra" proposes Filtra before Campiona', () => {
    const labels = filterPalette(groups, 'filtra')[0].items.map(i => i.label);
    expect(labels[0]).toBe('Filtra');
    expect(labels.indexOf('Filtra')).toBeLessThan(labels.indexOf('Campiona'));
  });
});

describe('placement', () => {
  it('findFreePosition never lands on an existing node', () => {
    let nodes = [];
    for (let i = 0; i < 30; i += 1) nodes = nodes.concat({ ...n(`n${i}`), position: findFreePosition(nodes, { x: 100, y: 100 }) });
    expect(overlapping(nodes)).toBeNull();
  });

  it('findFreePosition always ends on a free spot, even on a crowded column', () => {
    const column = Array.from({ length: 80 }, (_, i) => n(`c${i}`, 0, i * 104));
    expect(overlapping([...column, { ...n('new'), position: findFreePosition(column, { x: 0, y: 0 }) }])).toBeNull();
  });

  it('plan: one successor + pass-through = chain; otherwise a branch', () => {
    const nodes = [n('a'), n('b', COLUMN_STEP), n('c', 2 * COLUMN_STEP), n('x', 0, 300)];
    const edges = [e('a', 'b'), e('b', 'c')];
    const chain = planInsertAfter(nodes, edges, 'a', { passThrough: true });
    expect(chain.reroute.id).toBe('a-b');
    expect([...chain.shiftIds].sort()).toEqual(['b', 'c']);
    expect(planInsertAfter(nodes, edges, 'a', { passThrough: false }).reroute).toBeNull();
    expect(planInsertAfter(nodes, edges, 'c', { passThrough: true }).reroute).toBeNull();
    const fork = [...edges, e('a', 'x')];
    expect(planInsertAfter(nodes, fork, 'a', { passThrough: true }).reroute).toBeNull();
  });

  it('runtime-input bindings do not count as successors', () => {
    const nodes = [n('a'), n('b', COLUMN_STEP), n('p', 0, 300, 'project_input')];
    const edges = [e('a', 'b'), e('a', 'p', { targetHandle: 'parameter:v', data: { edge_kind: 'parameter_binding' } })];
    expect(planInsertAfter(nodes, edges, 'a', { passThrough: true }).reroute.id).toBe('a-b');
  });
});

describe('store.insertNode / addNodes', () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.getState().setReadOnly(false);
  });

  it('"after A" with A -> B inserts into the chain as ONE undoable action', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a'), n('b', COLUMN_STEP)], [e('a', 'b', { targetHandle: 'input-1' })]);
    const out = s.insertNode({ node: n('new'), afterId: 'a', passThrough: true, makeEdge });
    const st = useCanvasStore.getState();
    expect(st.edges.map(x => `${x.source}>${x.target}:${x.targetHandle ?? ''}`).sort())
      .toEqual(['a>new:', 'new>b:input-1']);
    expect(out.added).toHaveLength(2);
    expect(st.nodes.find(x => x.id === 'b').position.x).toBe(2 * COLUMN_STEP);
    expect(st.nodes.find(x => x.id === 'new').selected).toBe(true);
    expect(overlapping(st.nodes)).toBeNull();
    expect(st.isDirty).toBe(true);
    st.undo();
    const back = useCanvasStore.getState();
    expect(back.nodes.map(x => x.id)).toEqual(['a', 'b']);
    expect(back.edges.map(x => x.id)).toEqual(['a-b']);
  });

  it('shifting the chain never pushes a node onto an unrelated one', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a'), n('b', COLUMN_STEP), n('x', 2 * COLUMN_STEP), n('y', 3 * COLUMN_STEP)],
      [e('a', 'b'), e('x', 'y')]);
    s.insertNode({ node: n('new'), afterId: 'a', passThrough: true, makeEdge });
    expect(overlapping(useCanvasStore.getState().nodes)).toBeNull();
  });

  it('a large shifted node is settled with its real size', () => {
    const s = useCanvasStore.getState();
    const big = { ...n('b', COLUMN_STEP), measured: { width: 400, height: 300 } };
    s.loadGraph([n('a'), big, n('x', 2 * COLUMN_STEP + 300, 150)], [e('a', 'b')]);
    s.insertNode({ node: n('new'), afterId: 'a', passThrough: true, makeEdge });
    expect(overlapping(useCanvasStore.getState().nodes)).toBeNull();
  });

  it('a non pass-through step (Output) after A -> B becomes a branch in a free spot', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a'), n('b', COLUMN_STEP)], [e('a', 'b')]);
    s.insertNode({ node: n('out', 0, 0, 'result'), afterId: 'a', passThrough: false, makeEdge });
    const st = useCanvasStore.getState();
    expect(st.edges.map(x => x.id).sort()).toEqual(['a-b', 'a-out']);
    expect(overlapping(st.nodes)).toBeNull();
  });

  it('every addNodes call avoids overlaps, whatever position the editor asked for', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a', 200, 200)], []);
    for (let i = 0; i < 5; i += 1) s.addNodes(n(`k${i}`, 200, 200));
    s.addNodes([n('m1', 200, 200), n('m2', 200, 200)]);
    expect(overlapping(useCanvasStore.getState().nodes)).toBeNull();
  });

  it('is a no-op in read-only mode', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a')], []);
    s.setReadOnly(true);
    expect(s.insertNode({ node: n('new'), afterId: 'a', passThrough: true, makeEdge })).toBeNull();
    expect(useCanvasStore.getState().nodes).toHaveLength(1);
  });

  it('read-only users can still select nodes and get measurements', () => {
    const s = useCanvasStore.getState();
    s.loadGraph([n('a')], []);
    s.setReadOnly(true);
    s.onNodesChange([
      { type: 'select', id: 'a', selected: true },
      { type: 'dimensions', id: 'a', dimensions: { width: 200, height: 70 } },
      { type: 'position', id: 'a', position: { x: 50, y: 50 } },
    ]);
    const a = useCanvasStore.getState().nodes[0];
    expect(a.selected).toBe(true);
    expect(a.position).toEqual({ x: 0, y: 0 });
    expect(useCanvasStore.getState().isDirty).toBe(false);
  });

  it('the content key ignores layout, follows data, links, undo and derived fixes', () => {
    const s = useCanvasStore.getState();
    const key = () => canvasContentKey(useCanvasStore.getState().nodes, useCanvasStore.getState().edges);
    s.loadGraph([n('a'), n('b', COLUMN_STEP)], [e('a', 'b')]);
    const k0 = key();
    s.onNodesChange([{ type: 'position', id: 'a', position: { x: 10, y: 10 } }]);
    s.editNodes(nodes => nodes.map(x => ({ ...x, position: { x: x.position.x + 5, y: 0 } }))); // Riordina
    expect(key()).toBe(k0);
    s.updateNodeData('a', { config: { x: 1 } });
    const k1 = key();
    expect(k1).not.toBe(k0);
    s.patchNodeConfig('a', { y: 2 }, { derived: true });
    expect(key()).not.toBe(k1);
    const kBeforeSelect = key();
    s.onEdgesChange([{ type: 'select', id: 'a-b', selected: true }]);
    expect(useCanvasStore.getState().edges[0].selected).toBe(true);
    expect(key()).toBe(kBeforeSelect);
    s.removeEdges('a-b');
    expect(key()).not.toBe(k1);
  });
});

describe('read-only canvas props', () => {
  it('do not swallow node/edge changes (the store filters them)', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/hooks/useCanvasCore.js'), 'utf-8');
    expect(src).not.toMatch(/onNodesChange:\s*\(\)\s*=>/);
    expect(src).not.toMatch(/onEdgesChange:\s*\(\)\s*=>/);
  });
});
