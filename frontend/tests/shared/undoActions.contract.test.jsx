/**
 * Undo per AZIONE (Fase 5, T5+T6, correzione dopo la conferma).
 *
 * Regola (store/typingAction, store/useCanvasStore takeSnapshot):
 *  (a) un gesto composto = tutte le mutazioni utente dello stesso turno
 *      sincrono: un solo Ctrl+Z;
 *  (b) una digitazione DICHIARATA = mutazioni nate dall'evento di un campo
 *      di testo, stesso campo nella stessa sessione di fuoco (e stesso nodo
 *      e campi): un solo Ctrl+Z finche' il campo ha il fuoco;
 *  (c) ogni azione discreta (casella, scelta, radio, pulsante, Applica) e'
 *      una sua azione, anche sullo stesso campo dei dati della precedente;
 *  (d) Salva, cambio della selezione, apertura/chiusura di un pannello,
 *      undo/redo e caricamenti chiudono qualunque gruppo;
 *  (e) nessuna finestra di tempo.
 * Casi del giro di conferma: (a) mostra colonna A poi B, (b) Applica i
 * filtri due volte, (c) digita, Salva, digita. Enumerazione: ogni azione
 * dello store e ogni controllo discreto dei pannelli dei tre editor.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { act, render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { canvasStateRef } = vi.hoisted(() => ({ canvasStateRef: { current: { nodes: [], edges: [] } } }));

vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({ categories: {}, transforms: {} }),
  isMultiInputOp: () => false,
}));
// Banco dei pannelli (tests/helpers/inspectorHarness): come InspectorShell.
vi.mock('@xyflow/react', () => ({
  ...globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }),
  useNodeId: () => null,
}));
vi.mock('../../src/api/http', () => {
  const pending = () => new Promise(() => {});
  return {
    default: {
      get: pending, post: pending, put: pending, patch: pending, delete: pending,
      interceptors: { request: { use: () => {} }, response: { use: () => {} } },
      defaults: { headers: { common: {} } },
    },
  };
});
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../src/components/modals/RunHistoryModal', () => ({ default: () => null }));

import useCanvasStore from '../../src/store/useCanvasStore';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import ModalWrapper from '../../src/components/common/ModalWrapper';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import { CASES, mountInspector } from '../helpers/inspectorHarness';

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

  it('statico: nessuna finestra di tempo nello store', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/store/useCanvasStore.js'), 'utf-8');
    expect(src).not.toMatch(/SNAPSHOT_COALESCE_MS|lastSnapshotTs/);
    const body = src.slice(src.indexOf('const snapshot = (scope'), src.indexOf("'takeSnapshot');"));
    expect(body.length).toBeGreaterThan(100);
    expect(body).not.toMatch(/Date\.now|setTimeout|performance\.now/);
  });
});

// Campi di prova: testo, area di testo, caselle, scelta, pulsante, campo
// con scrittura ritardata (timer).
function Fields({ nodeId = 'a' }) {
  const data = useCanvasStore(s => s.nodes.find(n => n.id === nodeId)?.data) || {};
  const update = useCanvasStore(s => s.updateNodeData);
  const cols = data.columns || [{ name: 'A', visible: true }, { name: 'B', visible: true }];
  return (
    <div>
      <input aria-label="alias" value={data.alias || ''} onChange={e => update(nodeId, { alias: e.target.value })} />
      <input aria-label="altro" value={data.other || ''} onChange={e => update(nodeId, { other: e.target.value })} />
      <textarea aria-label="nota" value={data.note || ''} onChange={e => update(nodeId, { note: e.target.value })} />
      {cols.map((c, i) => (
        <input key={c.name} type="checkbox" aria-label={`colonna ${c.name}`} checked={c.visible}
          onChange={() => update(nodeId, { columns: cols.map((x, j) => (j === i ? { ...x, visible: !x.visible } : x)) })} />
      ))}
      <select aria-label="formato" value={data.alias ? 'json' : (data.format || 'csv')} onChange={e => update(nodeId, { alias: e.target.value })}>
        <option value="csv">csv</option><option value="json">json</option><option value="xlsx">xlsx</option>
      </select>
      <button type="button" onClick={() => update(nodeId, { filters: [...(data.filters || []), { column: 'A' }] })}>Applica filtri</button>
      <input aria-label="ritardato" onChange={e => { const v = e.target.value; setTimeout(() => update(nodeId, { slow: v }), 0); }} />
    </div>
  );
}

const field = (name) => screen.getByLabelText(name);
async function type(el, values, { focus = true } = {}) {
  if (focus) act(() => { el.focus(); });
  for (const v of values) {
    await act(async () => { fireEvent.change(el, { target: { value: v } }); });
    await nextTurn();
  }
}
async function click(el) {
  await act(async () => { fireEvent.click(el); });
  await nextTurn();
}

describe('Undo per azione: digitazione dichiarata dal campo, azioni discrete separate', () => {
  it('(a) mostra colonna A poi colonna B: due Ctrl+Z', async () => {
    render(<Fields />);
    await click(field('colonna A'));
    await click(field('colonna B'));
    expect(st().past).toHaveLength(2);
    act(() => st().undo());
    expect(st().nodes[0].data.columns.map(c => c.visible)).toEqual([false, true]);
  });

  it('(a) la stessa casella due volte: due Ctrl+Z', async () => {
    render(<Fields />);
    await click(field('colonna A'));
    await click(field('colonna A'));
    expect(st().past).toHaveLength(2);
  });

  it('(a) dallo store, senza campo di testo: due patch dello stesso campo sono due azioni', async () => {
    st().updateNodeData('a', { columns: [{ name: 'A', visible: false }] }); await nextTurn();
    st().updateNodeData('a', { columns: [{ name: 'A', visible: true }] }); await nextTurn();
    expect(st().past).toHaveLength(2);
  });

  it('(b) Applica i filtri due volte: due Ctrl+Z', async () => {
    render(<Fields />);
    await click(screen.getByRole('button', { name: 'Applica filtri' }));
    await click(screen.getByRole('button', { name: 'Applica filtri' }));
    expect(st().past).toHaveLength(2);
    act(() => st().undo());
    expect(st().nodes[0].data.filters).toHaveLength(1);
  });

  it('(c) digita, Salva, digita: due Ctrl+Z (markClean e handleSaveQuiet)', async () => {
    render(<Fields />);
    await type(field('alias'), ['x', 'xy', 'xyz']);
    act(() => st().markClean());
    await type(field('alias'), ['xyzw'], { focus: false });
    expect(st().past).toHaveLength(2);
    act(() => st().undo());
    expect(st().nodes[0].data.alias).toBe('xyz');
    act(() => { seed(); useCanvasStore.setState({ projectId: null }); });
    await type(field('alias'), ['p', 'pq']);
    await act(async () => { await st().handleSaveQuiet(); });
    await type(field('alias'), ['pqr'], { focus: false });
    expect(st().past).toHaveLength(2);
  });

  it('digitazione nello stesso campo: un solo Ctrl+Z (input e textarea)', async () => {
    render(<Fields />);
    await type(field('alias'), ['A', 'An', 'Ann', 'Anno']);
    expect(st().past).toHaveLength(1);
    await type(field('nota'), ['n', 'no', 'not']);
    expect(st().past).toHaveLength(2);
    act(() => st().undo());
    act(() => st().undo());
    expect(st().nodes[0].data.alias).toBeUndefined();
  });

  it('uscita dal campo e nuovo fuoco: un\'azione nuova', async () => {
    render(<Fields />);
    await type(field('alias'), ['a', 'ab']);
    act(() => { field('alias').blur(); });
    await type(field('alias'), ['abc']);
    expect(st().past).toHaveLength(2);
  });

  it('un\'azione discreta in mezzo chiude la digitazione anche senza perdere il fuoco', async () => {
    render(<Fields />);
    await type(field('alias'), ['a', 'ab']);
    await act(async () => { fireEvent.change(field('formato'), { target: { value: 'xlsx' } }); });
    await nextTurn();
    await type(field('alias'), ['abc'], { focus: false });
    expect(st().past).toHaveLength(3);
  });

  it('cambio della selezione, cambio di vista e apertura di una finestra chiudono la digitazione', async () => {
    render(<Fields />);
    await type(field('alias'), ['a']);
    act(() => st().onNodesChange([{ id: 'b', type: 'select', selected: true }]));
    await type(field('alias'), ['ab'], { focus: false });
    act(() => st().setCanvasView('steps'));
    await type(field('alias'), ['abc'], { focus: false });
    const { unmount } = render(<ModalWrapper isOpen onClose={() => {}} title="Finestra"><p>x</p></ModalWrapper>);
    await type(field('alias'), ['abcd'], { focus: false });
    unmount();
    await type(field('alias'), ['abcde'], { focus: false });
    expect(st().past).toHaveLength(5);
  });

  it('una scrittura dopo un timer non e\' digitazione: ogni scrittura e\' un\'azione', async () => {
    vi.useFakeTimers();
    try {
      render(<Fields />);
      act(() => { field('ritardato').focus(); });
      for (const v of ['r', 'ri']) {
        act(() => { fireEvent.change(field('ritardato'), { target: { value: v } }); });
        await act(async () => { vi.runAllTimers(); await Promise.resolve(); });
      }
      expect(st().past).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('drag-start e cancellazione da tastiera non passano l\'evento come gruppo', async () => {
    const shell = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/components/editor/EditorShell.jsx'), 'utf-8');
    const core = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/hooks/useCanvasCore.js'), 'utf-8');
    expect(shell).not.toMatch(/onNodeDragStart=\{takeSnapshot\}/);
    expect(core).not.toMatch(/onBeforeDelete:\s*takeSnapshot\b/);
    // Anche con un argomento, takeSnapshot e' un'azione a se'.
    st().takeSnapshot({ type: 'mousedown' });
    st().editNodes(ns => ns.map(n => ({ ...n, position: { x: 5, y: 5 } })));
    await nextTurn();
    st().takeSnapshot({ type: 'mousedown' });
    st().editNodes(ns => ns.map(n => ({ ...n, position: { x: 9, y: 9 } })));
    await nextTurn();
    expect(st().past).toHaveLength(2);
  });

  it('statico: nessun gruppo passato dai componenti, nessuna chiave dai soli campi della patch', () => {
    const store = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/store/useCanvasStore.js'), 'utf-8');
    expect(store).not.toMatch(/opts\.group|patchGroup/);
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (
      e.isDirectory() ? walk(path.join(dir, e.name)) : (/\.jsx?$/.test(e.name) ? [path.join(dir, e.name)] : [])));
    const offenders = walk(path.resolve(import.meta.dirname, '../../src'))
      .filter(f => /(updateNodeData|patchNodeConfig)\([^;]*\{\s*group:/s.test(fs.readFileSync(f, 'utf-8')));
    expect(offenders).toEqual([]);
  });
});

// Enumerazione: ogni controllo DISCRETO (casella, radio, scelta, pulsante)
// del pannello di ogni tipo di nodo dei tre editor. Ogni attivazione che
// modifica il canvas e' esattamente UN Ctrl+Z, anche ripetuta sullo stesso
// controllo o su controlli che scrivono lo stesso campo.
describe('Undo per azione: le azioni discrete dei pannelli sono una ciascuna', () => {
  const DISCRETE = 'input[type="checkbox"], input[type="radio"], select, button';
  const SKIP = /Elimina|Chiudi|×|Cronologia|Valida|Anteprima|Esegui|Vedi /;
  let effective = 0;

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s', async (_, c) => {
    globalThis.__frontendConsolePolicy.allowError = true;
    const { container } = mountInspector(c, { canvasStateRef });
    const form = () => container.querySelector('.node-inspector__form');
    const controls = [...form().querySelectorAll(DISCRETE)]
      .filter(el => !el.disabled && !SKIP.test(`${el.textContent} ${el.getAttribute('aria-label') || ''} ${el.title || ''}`));
    for (const el of controls) {
      for (let round = 0; round < 2; round += 1) {
        if (!el.isConnected || el.disabled) break;
        useCanvasStore.setState({ isDirty: false });
        const before = st().past.length;
        await act(async () => {
          if (el.tagName === 'SELECT') {
            const options = [...el.options].filter(o => !o.disabled && o.value !== el.value);
            if (!options.length) return;
            fireEvent.change(el, { target: { value: options[round % options.length].value } });
          } else {
            fireEvent.click(el);
          }
        });
        await nextTurn();
        const delta = st().past.length - before;
        const label = `${el.tagName} ${el.textContent.trim().slice(0, 30)} ${el.getAttribute('aria-label') || ''}`;
        if (st().isDirty) {
          effective += 1;
          expect(delta, label).toBe(1);
        } else {
          expect(delta, label).toBe(0);
        }
      }
    }
    cleanup();
  });

  it('l\'enumerazione ha attivato azioni che modificano il canvas', () => {
    expect(effective).toBeGreaterThan(10);
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
