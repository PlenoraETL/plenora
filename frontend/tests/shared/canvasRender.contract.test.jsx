/**
 * UI-2 (06/11/2026, V4, V5, V6, V8): render del canvas.
 *
 *  (a) statico, su TUTTI i selettori di src (tests/shared/canvasRenderScan):
 *      nessuna iscrizione a `s.nodes`/`s.edges` interi fuori da FlowCanvas,
 *      nessun selettore che scorre creando liste o serializza il grafo a ogni
 *      aggiornamento (passano da hooks/useCanvasDerived);
 *  (b) conteggio dei render durante un trascinamento simulato (30
 *      fotogrammi di sola posizione del nodo selezionato): la pagina
 *      dell'editor, il guscio (EditorShell) e il form del pannello non si
 *      ridisegnano; il canvas (FlowCanvas -> React Flow) si'. Una modifica
 *      dei dati ridisegna il form (il contatore funziona).
 */
import React from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { scanSelectors } from './canvasRenderScan';
import { ALLOWED, violationsOf } from './canvasRenderRules';

const { counts } = vi.hoisted(() => ({ counts: { flow: 0, toolbar: 0, page: 0, form: 0 } }));

vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react');
  const base = globalThis.__frontendTestHelpers.createReactFlowMock();
  const ReactFlow = (props) => { counts.flow += 1; return base.ReactFlow(props); };
  return { ...base, ReactFlow, default: ReactFlow, applyNodeChanges: actual.applyNodeChanges, applyEdgeChanges: actual.applyEdgeChanges };
});
vi.mock('react-router-dom', async () => ({ ...(await vi.importActual('react-router-dom')), useNavigate: () => () => {} }));
vi.mock('../../src/utils/validateRouteParam', () => ({ isValidProjectId: v => v === 'p1' }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin', group_id: 'g1', user_id: 'u1' } }) }));
vi.mock('../../src/api', async () => ({
  ...(await vi.importActual('../../src/api')),
  inferProjectSchemas: vi.fn(async () => ({ schemas: {}, schema_states: {} })),
}));
// Barra e percorso di esecuzione: segnaposto che contano i render del guscio
// (EditorShell li disegna a ogni suo render, senza iscrizioni proprie).
vi.mock('../../src/components/editor/EditorToolbar', () => ({ default: () => { counts.toolbar += 1; return null; } }));
vi.mock('../../src/components/editor/PipelineRunner', () => ({ default: () => null }));
vi.mock('../../src/components/editor/SaveConflictNotice', () => ({ default: () => null }));
vi.mock('../../src/components/canvas/StepPreview', () => ({ default: () => null }));

import EditorShell from '../../src/components/editor/EditorShell';
import { useCanvasCore } from '../../src/hooks/useCanvasCore';
import useCanvasStore from '../../src/store/useCanvasStore';

describe('selettori sul grafo (statico)', () => {
  const selectors = scanSelectors();

  it('la scansione trova i selettori (sonda)', () => {
    expect(selectors.length).toBeGreaterThan(50);
    expect(selectors.some(s => s.file === 'components/editor/EditorShell.jsx' && /s\.nodes/.test(s.arg))).toBe(true);
  });

  it('le regole riconoscono i casi vietati (sonda)', () => {
    const fake = (arg, file = 'components/x.jsx') => ({ file, line: 1, arg, text: arg });
    expect(violationsOf([
      fake('s => s.nodes'),
      fake('state => state.edges'),
      fake('s => JSON.stringify(s.nodes)'),
      fake('useShallow(s => s.nodes.map(n => n.id))'),
      fake('s => s.edges.filter(e => e.target === id).length'),
      fake('s => structureKey(s.nodes, s.edges)'),
    ])).toHaveLength(6);
    expect(violationsOf([
      fake('s => s.nodes.some(n => n.selected)'),
      fake('s => s.isReadOnly'),
      fake('s => s.nodes', ALLOWED[0].file),
    ])).toEqual([]);
  });

  it('nessun selettore vietato in src', () => {
    expect(violationsOf(selectors)).toEqual([]);
  });
});

// Il pannello mostra il form completo del tipo: questo conta i suoi render.
function CountingForm() { counts.form += 1; return null; }
const NODE_TYPES = { transform: CountingForm, result: CountingForm };
const CANVAS_CONFIG = { nodeTypes: NODE_TYPES, isReadOnly: false };

// Pagina editor minima: gli stessi hook degli editor veri (useCanvasCore)
// e lo stesso guscio.
function EditorPage() {
  counts.page += 1;
  const { onNodesChange, onEdgesChange } = useCanvasCore('p1', { mode: 'manipola' });
  const handlers = React.useMemo(() => ({ onNodesChange, onEdgesChange }), [onNodesChange, onEdgesChange]);
  const toolbar = React.useMemo(() => ({ mode: 'manipola', title: 'Prova' }), []);
  const slots = React.useMemo(() => ({ toolbar }), [toolbar]);
  return (
    <EditorShell
      projectId="p1" prefix="manipola" project={{ metadata: { name: 'Prova' } }}
      canvasConfig={CANVAS_CONFIG} canvasHandlers={handlers} slots={slots}
    />
  );
}

describe('trascinamento simulato: chi si ridisegna', () => {
  afterEach(() => { cleanup(); useCanvasStore.getState().reset(); });

  it('30 fotogrammi di sola posizione: pagina, guscio e form fermi; il canvas segue', async () => {
    render(<EditorPage />);
    act(() => {
      useCanvasStore.getState().loadGraph([
        { id: 'a', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} } },
        { id: 'out', type: 'result', position: { x: 300, y: 0 }, data: {} },
      ], [{ id: 'e', source: 'a', target: 'out', targetHandle: 'input-0' }]);
    });
    act(() => useCanvasStore.getState().onNodesChange([{ id: 'a', type: 'select', selected: true }]));
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(counts.form).toBeGreaterThan(0);
    const before = { ...counts };

    for (let i = 1; i <= 30; i += 1) {
      act(() => useCanvasStore.getState().onNodesChange([
        { id: 'a', type: 'position', position: { x: i * 3, y: i * 2 }, dragging: true },
      ]));
    }
    act(() => useCanvasStore.getState().onNodesChange([
      { id: 'a', type: 'position', position: { x: 90, y: 60 }, dragging: false },
    ]));

    const during = Object.fromEntries(Object.keys(counts).map(k => [k, counts[k] - before[k]]));
    process.stdout.write(`[UI-2] render durante 31 fotogrammi: ${JSON.stringify(during)}\n`);
    expect(during.page).toBe(0);
    expect(during.toolbar).toBe(0);
    expect(during.form).toBe(0);
    expect(during.flow).toBeGreaterThanOrEqual(31);

    // Sonda: cambiare i dati del passo ridisegna il form.
    act(() => useCanvasStore.getState().updateNodeData('a', { config: { column: 'x' } }));
    expect(counts.form - before.form).toBeGreaterThan(0);
  });
});
