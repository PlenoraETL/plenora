/**
 * UI-2 (06/11/2026): firme SEMANTICHE del canvas e contatori delle modifiche.
 *
 *  (a) enumerazione della tabella dei livelli (utils/canvasChange): ogni
 *      campo dichiarato posizione/selezione/interfaccia non cambia le firme;
 *      un campo non dichiarato conta come contenuto;
 *  (b) parita' con le firme di prima (riscritte qui come riferimento): su
 *      una sequenza di modifiche di ogni tipo, per ogni tipo di nodo, firma
 *      del contenuto e «uguale all'ultimo salvato» danno lo stesso esito;
 *  (c) un trascinamento non serializza: le firme di una coppia nuova di sola
 *      posizione sono lo STESSO oggetto (nessun ricalcolo);
 *  (d) i contatori dello store (store/graphRevisions) salgono solo al
 *      livello toccato, anche per le scritture da setState;
 *  (e) C13: l'ordine dei lati senza maniglia conta, la coordinata no.
 */
import { describe, it, expect } from 'vitest';
import { serializeCanvas } from '../../src/utils/canvasSerializer';
import { canvasContentKey, canvasSignatures } from '../../src/utils/canvasContentKey';
import { GRAPH_CHANGE_TABLE, graphChange } from '../../src/utils/canvasChange';
import { matchesSavedGraph } from '../../src/store/saveState';
import { createCanvasStore } from '../../src/store/useCanvasStore';
import { columnSignature, COLUMN_ROLES } from '../../src/utils/columnSource';

// ── Riferimento: le firme com'erano prima di UI-2 (serializzazione intera) ──
function oldContentKey(nodes, edges) {
  const { nodes: sn, edges: se } = serializeCanvas(nodes || [], edges || []);
  return JSON.stringify({
    nodes: sn.filter(n => n.type !== 'comment').map(({ position, ...rest }) => rest),
    edges: se.map(e => ('join_type' in e ? e : {
      source: e.source, sourceHandle: e.sourceHandle ?? null,
      target: e.target, targetHandle: e.targetHandle ?? null,
      data: e.data ?? null,
    })),
  });
}
function oldSavedSignature(nodes, edges) {
  const { nodes: sn, edges: se } = serializeCanvas(nodes || [], edges || []);
  return JSON.stringify({
    nodes: sn,
    edges: se.map(e => ('join_type' in e ? e : {
      id: e.id, type: e.type ?? null,
      source: e.source, sourceHandle: e.sourceHandle ?? null,
      target: e.target, targetHandle: e.targetHandle ?? null,
      data: e.data ?? null,
    })),
  });
}

// Un nodo per ogni tipo canonico (COLUMN_ROLES li enumera tutti) e una nota.
const TYPES = Object.keys(COLUMN_ROLES);
const corpus = () => {
  const nodes = TYPES.map((type, i) => ({
    id: `n${i}`, type, position: { x: i * 100, y: (i % 3) * 80 },
    data: { label: type, config: { k: i }, columns: [{ name: `c${i}`, type: 'str' }], fields: [{ name: 'f', field_type: 'string' }] },
  }));
  const comment = nodes.find(n => n.type === 'comment');
  comment.width = 200; comment.height = 90;
  const edges = nodes.slice(1).filter(n => n.type !== 'comment').map((n, i) => ({
    id: `e${i}`, source: nodes[i].id, target: n.id, targetHandle: i % 2 ? 'input-0' : null,
    ...(i === 2 ? { data: { join_type: 'left', source_column: 'a', target_column: 'b' } } : {}),
  }));
  return { nodes, edges };
};

const mapAt = (list, i, fn) => list.map((x, j) => (j === i ? fn(x) : x));

// Modifiche di ogni livello: [nome, livello atteso, (nodes, edges) => {nodes, edges}].
const MUTATIONS = [
  ['sposta', 'layout', (n, e) => ({ nodes: mapAt(n, 1, x => ({ ...x, position: { x: x.position.x + 7, y: x.position.y + 3 } })), edges: e })],
  ['trascina (dragging)', 'ui', (n, e) => ({ nodes: mapAt(n, 2, x => ({ ...x, dragging: !x.dragging })), edges: e })],
  ['misura del DOM', 'ui', (n, e) => ({ nodes: mapAt(n, 3, x => ({ ...x, measured: { width: 180, height: Math.random() } })), edges: e })],
  ['seleziona nodo', 'selection', (n, e) => ({ nodes: mapAt(n, 0, x => ({ ...x, selected: !x.selected })), edges: e })],
  ['seleziona arco', 'selection', (n, e) => ({ nodes: n, edges: mapAt(e, 0, x => ({ ...x, selected: !x.selected })) })],
  ['stile arco', 'none', (n, e) => ({ nodes: n, edges: mapAt(e, 1, x => ({ ...x, style: { stroke: 'red' }, animated: true })) })],
  ['dati del nodo', 'content', (n, e) => ({ nodes: mapAt(n, 4, x => ({ ...x, data: { ...x.data, config: { k: Math.random() } } })), edges: e })],
  ['ridimensiona nota', 'content', (n, e) => ({ nodes: n.map(x => (x.type === 'comment' ? { ...x, width: (x.width || 0) + 10 } : x)), edges: e })],
  ['campo nuovo del nodo', 'content', (n, e) => ({ nodes: mapAt(n, 5, x => ({ ...x, access_denied: !x.access_denied })), edges: e })],
  ['dati dell\'arco', 'content', (n, e) => ({ nodes: n, edges: mapAt(e, 2, x => ({ ...x, data: { ...(x.data || {}), join_type: x.data?.join_type === 'left' ? 'inner' : 'left' } })) })],
  ['aggiungi arco', 'structure', (n, e) => ({ nodes: n, edges: e.concat({ id: `x${e.length}`, source: n[0].id, target: n[6].id }) })],
  ['togli nodo', 'structure', (n, e) => ({ nodes: n.slice(0, -1), edges: e })],
];

describe('tabella dei livelli di modifica (enumerazione)', () => {
  const base = corpus();
  const node = base.nodes[1];
  const edge = base.edges[0];

  it.each(Object.entries(GRAPH_CHANGE_TABLE.node).flatMap(([level, keys]) => keys.map(k => [level, k])))(
    'nodo, %s: %s', (level, key) => {
      const changed = { ...node, [key]: key === 'position' ? { x: 1, y: 2 } : `${key}-nuovo` };
      const change = graphChange([node], [changed], [], []);
      expect(change.structure).toBe(level === 'structure');
      expect(change.content).toBe(false);
      expect(change.layout).toBe(level === 'layout');
      expect(change.selection).toBe(level === 'selection');
    },
  );

  it.each(Object.entries(GRAPH_CHANGE_TABLE.edge).flatMap(([level, keys]) => keys.map(k => [level, k])))(
    'arco, %s: %s', (level, key) => {
      const change = graphChange([], [], [edge], [{ ...edge, [key]: `${key}-nuovo` }]);
      expect(change.structure).toBe(level === 'structure');
      expect(change.content).toBe(level === 'content');
      expect(change.selection).toBe(level === 'selection');
    },
  );

  it('un campo del nodo non dichiarato conta come contenuto (lista di esclusione)', () => {
    expect(graphChange([node], [{ ...node, campo_mai_visto: 1 }], [], []).content).toBe(true);
  });

  it('una posizione ricreata con le stesse coordinate non e\' uno spostamento', () => {
    expect(graphChange([node], [{ ...node, position: { ...node.position } }], [], []).layout).toBe(false);
  });
});

describe('parita\' con le firme di prima', () => {
  it('ogni modifica, in sequenza: stessa firma del contenuto e stesso «uguale al salvato»', () => {
    let { nodes, edges } = corpus();
    const saved = { nodes, edges };
    for (let round = 0; round < 4; round += 1) {
      for (const [name, , mutate] of MUTATIONS) {
        ({ nodes, edges } = mutate(nodes, edges));
        expect({ name, key: canvasContentKey(nodes, edges) }).toEqual({ name, key: oldContentKey(nodes, edges) });
        const state = { nodes, edges, savedGraph: saved };
        expect({ name, same: matchesSavedGraph(state) })
          .toEqual({ name, same: oldSavedSignature(nodes, edges) === oldSavedSignature(saved.nodes, saved.edges) });
      }
    }
  });

  it('tornare allo stato salvato (Ctrl+Z a mano) e\' di nuovo «salvato»', () => {
    const { nodes, edges } = corpus();
    const moved = mapAt(nodes, 1, x => ({ ...x, position: { x: 999, y: 999 } }));
    const back = mapAt(moved, 1, x => ({ ...x, position: { ...nodes[1].position } }));
    expect(matchesSavedGraph({ nodes: moved, edges, savedGraph: { nodes, edges } })).toBe(false);
    expect(matchesSavedGraph({ nodes: back, edges, savedGraph: { nodes, edges } })).toBe(true);
  });
});

describe('un trascinamento non serializza', () => {
  it('100 fotogrammi di sola posizione: le firme sono lo stesso oggetto, mai ricalcolate', () => {
    let { nodes } = corpus();
    const { edges } = corpus();
    const first = canvasSignatures(nodes, edges);
    for (let i = 0; i < 100; i += 1) {
      nodes = mapAt(nodes, 1, x => ({ ...x, position: { x: i, y: i * 2 }, dragging: true }));
      expect(canvasSignatures(nodes, edges)).toBe(first);
    }
    nodes = mapAt(nodes, 1, x => ({ ...x, data: { ...x.data, nuovo: true } }));
    expect(canvasSignatures(nodes, edges)).not.toBe(first);
  });
});

describe('contatori dello store (graphRevisions)', () => {
  const revs = s => ({ structure: s.structureRev, content: s.contentRev, layout: s.layoutRev, selection: s.selectionRev });
  const setup = () => {
    const store = createCanvasStore();
    store.getState().init('p1', 'manipola');
    const { nodes, edges } = corpus();
    store.getState().loadGraph(nodes, edges);
    return store;
  };
  const delta = (store, fn) => {
    const before = revs(store.getState());
    fn(store.getState());
    const after = revs(store.getState());
    return Object.fromEntries(Object.keys(before).map(k => [k, after[k] - before[k]]));
  };

  it.each([
    ['trascinamento (React Flow)', s => s.onNodesChange([{ id: 'n1', type: 'position', position: { x: 5, y: 5 }, dragging: true }]),
      { structure: 0, content: 0, layout: 1, selection: 0 }],
    ['selezione', s => s.onNodesChange([{ id: 'n1', type: 'select', selected: true }]),
      { structure: 0, content: 0, layout: 0, selection: 1 }],
    ['misura del DOM', s => s.onNodesChange([{ id: 'n1', type: 'dimensions', dimensions: { width: 10, height: 10 } }]),
      { structure: 0, content: 0, layout: 0, selection: 0 }],
    ['dati del nodo', s => s.updateNodeData('n1', { label: 'x' }), { structure: 0, content: 1, layout: 0, selection: 0 }],
    ['correzione derivata', s => s.updateNodeData('n1', { label: 'y' }, { derived: true }), { structure: 0, content: 1, layout: 0, selection: 0 }],
    ['nuovo arco', s => s.addEdges({ id: 'nuovo', source: 'n0', target: 'n3' }), { structure: 1, content: 1, layout: 0, selection: 1 }],
    ['derivato (applyDerivedNodes)', s => s.applyDerivedNodes(ns => ns.slice(0, -1)), { structure: 1, content: 1, layout: 0, selection: 1 }],
  ])('%s', (_, fn, expected) => {
    expect(delta(setup(), fn)).toEqual(expected);
  });

  it('anche useCanvasStore.setState aggiorna i contatori', () => {
    const store = setup();
    const before = store.getState().contentRev;
    store.setState({ nodes: store.getState().nodes.map((n, i) => (i ? n : { ...n, data: { x: 1 } })) });
    expect(store.getState().contentRev).toBe(before + 1);
    // Un aggiornamento che non tocca il grafo non notifica contatori.
    store.setState({ isDirty: true });
    expect(store.getState().contentRev).toBe(before + 1);
  });
});

describe('C13: ordine dei lati senza maniglia', () => {
  const graph = (ya, yb) => ({
    nodes: [
      { id: 'a', type: 'file_source', position: { x: 0, y: ya }, data: {} },
      { id: 'b', type: 'file_source', position: { x: 0, y: yb }, data: {} },
      { id: 'j', type: 'transform', position: { x: 300, y: 0 }, data: { transform_op: 'concat' } },
    ],
    edges: [{ id: 'aj', source: 'a', target: 'j' }, { id: 'bj', source: 'b', target: 'j' }],
  });
  it('spostare in verticale senza scavalcare: firma uguale; scavalcando: firma diversa', () => {
    const start = graph(0, 100);
    const key = columnSignature(start.nodes, start.edges);
    const moved = graph(40, 100);
    expect(columnSignature(moved.nodes, moved.edges)).toBe(key);
    const swapped = graph(200, 100);
    expect(columnSignature(swapped.nodes, swapped.edges)).not.toBe(key);
  });
  it('con le maniglie la posizione non conta mai', () => {
    const withHandles = (ya) => {
      const g = graph(ya, 100);
      g.edges = g.edges.map((e, i) => ({ ...e, targetHandle: `input-${i}` }));
      return g;
    };
    const a = withHandles(0);
    const b = withHandles(500);
    expect(columnSignature(a.nodes, a.edges)).toBe(columnSignature(b.nodes, b.edges));
  });
});
