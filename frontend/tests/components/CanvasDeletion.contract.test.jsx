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
vi.mock('../../src/components/common/ErrorBoundary', () => ({
  CanvasErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import { ConfirmProvider } from '../../src/context/ConfirmContext';
import useCanvasStore from '../../src/store/useCanvasStore';
import useCanvasKeyboard from '../../src/hooks/useCanvasKeyboard';
import EditorShell from '../../src/components/editor/EditorShell';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import AnnotationNode from '../../src/components/nodes/AnnotationNode';
import { answerConfirm } from '../helpers/confirmDialog';

const wrapper = ({ children }) => <ConfirmProvider>{children}</ConfirmProvider>;

const NODE = { id: 'n1', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'Filtra' } };
const OTHER = { id: 'n2', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'Altro' } };
const EDGE = { id: 'e1', source: 'n1', target: 'n2' };
const GROUP = { id: 'g1', type: 'comment', position: { x: 0, y: 0 }, data: { title: 'Area' } };

function seed({ readOnly, selectNode = false, selectEdge = false }) {
  const st = useCanvasStore.getState();
  st.reset();
  useCanvasStore.setState({
    nodes: [{ ...NODE, selected: selectNode }, OTHER, GROUP],
    edges: [{ ...EDGE, selected: selectEdge }],
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
  ];
  it('le cancellazioni dell\'utente stanno dentro requestCanvasDeletion({... perform })', () => {
    for (const rel of FILES) {
      const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
      expect(src, rel).toMatch(/requestCanvasDeletion\(/);
      expect(src, rel).not.toMatch(/confirmCanvasDeletion|\.isReadOnly\)\s*return/);
    }
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
