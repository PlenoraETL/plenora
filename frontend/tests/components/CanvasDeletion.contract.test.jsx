/**
 * Contract: regola unica delle cancellazioni dal canvas (utils/canvasDeletion;
 * Fase 5, correzione dopo la conferma di T2+T4, punto 1).
 *
 * Per OGNI gesto che cancella (Canc, Backspace, tasto destro su un arco,
 * "Elimina passo" e "Elimina join" del pannello, ✕ del gruppo) x
 * {modificabile, sola lettura}:
 *  - modificabile: si apre UNA conferma; "Elimina" cancella;
 *  - sola lettura: nessuna conferma, nessuna cancellazione, nessun pulsante.
 * In piu' (statico): nei componenti e negli hook del canvas le cancellazioni
 * dell'utente passano solo da requestCanvasDeletion.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, renderHook, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { reactFlowPropsRef, canvasStateRef } = vi.hoisted(() => ({
  reactFlowPropsRef: { current: null },
  canvasStateRef: { current: { nodes: [], edges: [] } },
}));

vi.mock('@xyflow/react', () => {
  const base = globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef });
  const capture = (props) => { reactFlowPropsRef.current = props; return base.ReactFlow(props); };
  return { ...base, ReactFlow: capture, default: capture };
});
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock('../../src/utils/validateRouteParam', () => ({ isValidProjectId: () => true }));
vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({ categories: {}, transforms: {} }),
  isMultiInputOp: () => false,
}));
vi.mock('../../src/components/common/ErrorBoundary', () => ({
  CanvasErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import { ConfirmProvider } from '../../src/context/ConfirmContext';
import useCanvasStore from '../../src/store/useCanvasStore';
import useCanvasKeyboard from '../../src/hooks/useCanvasKeyboard';
import EditorShell from '../../src/components/editor/EditorShell';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import AnnotationNode from '../../src/components/nodes/AnnotationNode';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import { answerConfirm } from '../helpers/confirmDialog';

const wrapper = ({ children }) => <ConfirmProvider>{children}</ConfirmProvider>;

const NODE = { id: 'n1', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'Filtra' } };
const OTHER = { id: 'n2', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'Altro' } };
const EDGE = { id: 'e1', source: 'n1', target: 'n2' };
const GROUP = { id: 'g1', type: 'comment', position: { x: 0, y: 0 }, data: { title: 'Area' } };
const INPUT = { id: 'in1', type: 'project_input', position: { x: 0, y: 0 }, data: { input_key: 'anno', label: 'Anno', input_kind: 'text' } };
const BINDING = { id: 'b1', source: 'in1', target: 'n2', sourceHandle: 'parameter', data: { edge_kind: 'parameter_binding', target_field: 'config.x' } };

function seed({ readOnly, selectNode = false, selectEdge = false }) {
  const st = useCanvasStore.getState();
  st.reset();
  useCanvasStore.setState({
    nodes: [{ ...NODE, selected: selectNode }, OTHER, GROUP, INPUT],
    edges: [{ ...EDGE, selected: selectEdge }, BINDING],
  });
  st.setReadOnly(readOnly);
}

const state = () => useCanvasStore.getState();
const dialogs = () => screen.queryAllByRole('dialog');

// Ogni gesto: monta, compie il gesto, dice se e' stato cancellato.
const GESTURES = {
  Canc: {
    selection: { selectNode: true },
    async run() {
      renderHook(() => useCanvasKeyboard({
        nodes: state().nodes, edges: state().edges,
        onDeleteNode: state().deleteNode, onDeleteEdges: state().removeEdges, onBeforeDelete: state().takeSnapshot,
      }), { wrapper });
      await act(async () => { fireEvent.keyDown(document.body, { key: 'Delete' }); });
      return true;
    },
    deleted: () => !state().nodes.some(n => n.id === 'n1'),
  },
  Backspace: {
    selection: { selectEdge: true },
    async run() {
      renderHook(() => useCanvasKeyboard({
        nodes: state().nodes, edges: state().edges,
        onDeleteNode: state().deleteNode, onDeleteEdges: state().removeEdges, onBeforeDelete: state().takeSnapshot,
      }), { wrapper });
      await act(async () => { fireEvent.keyDown(document.body, { key: 'Backspace' }); });
      return true;
    },
    deleted: () => !state().edges.some(e => e.id === 'e1'),
  },
  'tasto destro arco': {
    selection: {},
    async run() {
      reactFlowPropsRef.current = null;
      render(
        <ConfirmProvider>
          <EditorShell
            projectId="p1" prefix="estrai" loading={false} loadError={null}
            project={{ metadata: { name: 'P' }, canvas: { nodes: [], edges: [] } }}
            reload={vi.fn()} onBack={vi.fn()} nodes={state().nodes} edges={state().edges}
            canvasConfig={{ nodeTypes: {}, isReadOnly: state().isReadOnly }}
            canvasHandlers={{
              onNodesChange: vi.fn(), onEdgesChange: vi.fn(),
              onEdgeContextMenu: (event, edge) => state().removeEdges(edge.id),
            }}
            slots={{}}
          />
        </ConfirmProvider>,
      );
      const handler = reactFlowPropsRef.current?.onEdgeContextMenu;
      expect(handler).toBeTypeOf('function');
      await act(async () => { handler({ preventDefault: () => {} }, state().edges[0]); });
      return true;
    },
    deleted: () => !state().edges.some(e => e.id === 'e1'),
  },
  'Elimina passo': {
    selection: { selectNode: true },
    async run() {
      render(<ConfirmProvider><NodeInspector fullNodeTypes={{ transform: () => <div>form</div> }} /></ConfirmProvider>);
      const btn = screen.queryByRole('button', { name: /Elimina passo/ });
      if (!btn) return false;
      await act(async () => { fireEvent.click(btn); });
      return true;
    },
    deleted: () => !state().nodes.some(n => n.id === 'n1'),
  },
  'Elimina join (pannello dell\'arco)': {
    selection: { selectEdge: true },
    async run() {
      const edgeInspector = {
        kind: 'join', icon: '🔗', matches: () => true,
        entry: () => ({ title: 'Join A-B', summary: '' }),
        Panel: () => <div>join</div>,
      };
      render(<ConfirmProvider><NodeInspector fullNodeTypes={{}} edgeInspector={edgeInspector} /></ConfirmProvider>);
      const btn = screen.queryByRole('button', { name: /Elimina join/ });
      if (!btn) return false;
      await act(async () => { fireEvent.click(btn); });
      return true;
    },
    deleted: () => !state().edges.some(e => e.id === 'e1'),
  },
  '× «Rimuovi collegamento» (input runtime)': {
    selection: {},
    async run() {
      render(<ConfirmProvider><ProjectInputNode id="in1" data={INPUT.data} /></ConfirmProvider>);
      const btn = screen.queryByTitle('Rimuovi collegamento');
      if (!btn) return false;
      await act(async () => { fireEvent.click(btn); });
      return true;
    },
    deleted: () => !state().edges.some(e => e.id === 'b1'),
  },
  '✕ del gruppo': {
    selection: {},
    async run() {
      render(<ConfirmProvider><AnnotationNode id="g1" data={GROUP.data} selected /></ConfirmProvider>);
      const btn = screen.queryByTitle('Elimina gruppo');
      if (!btn) return false;
      await act(async () => { fireEvent.click(btn); });
      return true;
    },
    deleted: () => !state().nodes.some(n => n.id === 'g1'),
  },
};

beforeEach(() => { useCanvasStore.getState().reset(); });
afterEach(() => { cleanup(); useCanvasStore.getState().setReadOnly(false); });

describe('cancellazione dal canvas: ogni gesto x {modificabile, sola lettura}', () => {
  for (const [name, g] of Object.entries(GESTURES)) {
    it(`${name} — modificabile: una conferma, poi cancella`, async () => {
      seed({ readOnly: false, ...g.selection });
      expect(await g.run()).toBe(true);
      expect(dialogs()).toHaveLength(1);
      expect(g.deleted()).toBe(false);
      await answerConfirm(true, { confirmText: 'Elimina' });
      expect(g.deleted()).toBe(true);
    });

    it(`${name} — sola lettura: nessuna conferma, nessuna cancellazione`, async () => {
      seed({ readOnly: true, ...g.selection });
      await g.run();
      expect(dialogs()).toHaveLength(0);
      expect(g.deleted()).toBe(false);
    });
  }

  it('il ruolo che diventa sola lettura mentre la conferma e\' aperta non cancella', async () => {
    seed({ readOnly: false, selectNode: true });
    await GESTURES.Canc.run();
    expect(dialogs()).toHaveLength(1);
    act(() => { useCanvasStore.getState().setReadOnly(true); });
    await answerConfirm(true, { confirmText: 'Elimina' });
    expect(GESTURES.Canc.deleted()).toBe(false);
  });
});

describe('cancellazione dal canvas: un solo punto (statico)', () => {
  const ROOT = path.resolve(__dirname, '../../src');
  const FILES = [
    'hooks/useCanvasKeyboard.js',
    'components/editor/EditorShell.jsx',
    'components/canvas/NodeInspector.jsx',
    'components/nodes/AnnotationNode.jsx',
    'components/nodes/ProjectInputNode.jsx',
  ];
  it('le cancellazioni dell\'utente stanno dentro requestCanvasDeletion({... perform })', () => {
    for (const rel of FILES) {
      const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
      expect(src, rel).toMatch(/requestCanvasDeletion\(/);
      expect(src, rel).not.toMatch(/confirmCanvasDeletion|\.isReadOnly\)\s*return/);
    }
  });

  // Enumerazione su TUTTO src (unione T5+T6): ogni chiamata di removeEdges,
  // deleteNode o deleteEdge e' il `perform` di requestCanvasDeletion, oppure
  // sta in una funzione dichiarata qui che si chiama SOLO come perform.
  const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : (/\.(jsx?|tsx?)$/.test(e.name) ? [p] : []);
  });
  // Chi definisce le azioni e la regola.
  const OWNERS = new Set(['store/useCanvasStore.js', 'utils/canvasDeletion.js']);
  const ALL = walk(ROOT).map(p => path.relative(ROOT, p).replace(/\\/g, '/')).filter(r => !OWNERS.has(r));
  // file -> perche' la chiamata e' comunque dentro un perform (verificato sotto).
  const PERFORM_TARGETS = {
    'hooks/useEdgeHandlers.js': 'onEdgeContextMenu: EditorShell lo chiama solo come perform',
  };
  // file che tengono il riferimento all'azione dello store senza chiamarla.
  const REFERENCE_HOLDERS = {
    'hooks/useCanvasCore.js': 'lo passa a useCanvasKeyboard (onDeleteNode/onDeleteEdges), chiamati nel perform',
  };
  const CALL = /\b(removeEdges|deleteNode|deleteEdge)\s*\(/;
  const PERFORM = /perform:\s*\(\)\s*=>/;

  it('in tutto src ogni removeEdges/deleteNode/deleteEdge(...) e\' un perform di requestCanvasDeletion', () => {
    const offenders = [];
    const seen = new Set();
    for (const rel of ALL) {
      const src = strip(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
      for (const line of src.split('\n')) {
        if (!CALL.test(line)) continue;
        seen.add(rel);
        if (PERFORM_TARGETS[rel]) continue;
        if (!PERFORM.test(line) || !/requestCanvasDeletion\(/.test(src)) offenders.push(`${rel}: ${line.trim()}`);
      }
    }
    // La scansione vede davvero i chiamanti (pannello, gruppo, input runtime, archi).
    expect([...seen].sort()).toEqual(expect.arrayContaining([
      'components/canvas/NodeInspector.jsx',
      'components/nodes/AnnotationNode.jsx',
      'components/nodes/ProjectInputNode.jsx',
      'hooks/useEdgeHandlers.js',
    ]));
    expect(offenders).toEqual([]);
  });

  it('i riferimenti alle azioni di cancellazione dello store finiscono solo in un perform', () => {
    const offenders = [];
    for (const rel of ALL) {
      const src = strip(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
      if (!/\b(removeEdges|deleteNode)\b/.test(src)) continue;
      if (REFERENCE_HOLDERS[rel] || PERFORM_TARGETS[rel]) continue;
      // Nome locale preso dallo store (selettore o destrutturazione): ogni
      // suo uso e' nel perform.
      const locals = [
        ...[...src.matchAll(/const\s+(\w+)\s*=\s*useCanvasStore\(\s*\(?\s*(?:s|state)\s*\)?\s*=>\s*(?:s|state)\.(?:removeEdges|deleteNode)\s*\)/g)].map(m => m[1]),
        ...[...src.matchAll(/\{[^}]*\b(removeEdges|deleteNode)\b[^}]*\}\s*=/g)].map(m => m[1]),
      ];
      for (const name of locals) {
        const uses = src.split('\n').filter(l => new RegExp(`\\b${name}\\b`).test(l) && !/useCanvasStore\(/.test(l) && !/\}\s*=/.test(l));
        for (const l of uses) if (!PERFORM.test(l)) offenders.push(`${rel}: ${l.trim()}`);
        if (uses.length === 0) offenders.push(`${rel}: ${name} preso e mai usato`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('le eccezioni dichiarate sono davvero dentro un perform', () => {
    const shell = fs.readFileSync(path.join(ROOT, 'components/editor/EditorShell.jsx'), 'utf8');
    expect(shell).toMatch(/requestCanvasDeletion\(\{[^}]*perform:\s*\(\)\s*=>\s*onEdgeContextMenu\(/);
    const core = strip(fs.readFileSync(path.join(ROOT, 'hooks/useCanvasCore.js'), 'utf8'));
    const coreUses = core.split('\n').filter(l => /\b(removeEdges|deleteNode)\b/.test(l) && !/useCanvasStore\(/.test(l)).map(l => l.trim());
    expect(coreUses).toEqual(['onDeleteNode: deleteNode,', 'onDeleteEdges: removeEdges,']);
    const keyboard = strip(fs.readFileSync(path.join(ROOT, 'hooks/useCanvasKeyboard.js'), 'utf8'));
    const start = keyboard.indexOf('perform: () => {');
    const end = keyboard.indexOf('}).finally', start);
    expect(start).toBeGreaterThan(0);
    const calls = [...keyboard.matchAll(/\b(onDeleteNode|onDeleteEdges)\s*\(/g)];
    expect(calls.length).toBeGreaterThan(0);
    for (const m of calls) expect(m.index > start && m.index < end, m[0]).toBe(true);
  });
  it('nessun componente del canvas confronta isReadOnly per decidere un\'eliminazione', () => {
    const dirs = ['components/canvas', 'components/nodes', 'components/editor', 'components/transforms'];
    const offenders = [];
    for (const d of dirs) {
      for (const f of fs.readdirSync(path.join(ROOT, d))) {
        if (!/\.jsx?$/.test(f)) continue;
        const src = fs.readFileSync(path.join(ROOT, d, f), 'utf8');
        if (/onDelete=\{\s*isReadOnly/.test(src) || /isReadOnly[^\n]*deleteNode|deleteNode[^\n]*isReadOnly/.test(src)) offenders.push(`${d}/${f}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
