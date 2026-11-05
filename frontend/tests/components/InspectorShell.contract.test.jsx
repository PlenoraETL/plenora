/**
 * Contract del guscio del pannello laterale (Blocco A, regola 6) e dei
 * titoli (regola 7), per enumerazione.
 *
 * Per OGNI tipo di nodo di ogni mappa `nodeTypes` dei tre editor (Estrai,
 * Manipola, Arricchisci; ricavate dai file degli editor, come
 * compactCanvas.contract) NodeInspector monta il modulo completo e:
 *  - c'e' esattamente un controllo di chiusura e un solo "Elimina passo",
 *    che chiede conferma (dialog del progetto, ConfirmProvider) e poi elimina;
 *  - nel corpo nessun elemento con classe che termina in "__header" e
 *    nessun altro pulsante che elimini il nodo (× o "Elimina nodo");
 *  - il modulo non riceve `selected` forzato;
 *  - in sola lettura niente "Elimina passo";
 *  - fuoco e uscita dal campo titolo senza modifiche non scrivono e non
 *    rendono il canvas "non salvato".
 * Un tipo nuovo in un editor, con un componente non elencato qui, fa
 * fallire il test.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import { answerConfirm } from '../helpers/confirmDialog';

const { canvasStateRef } = vi.hoisted(() => ({ canvasStateRef: { current: { nodes: [], edges: [] } } }));

vi.mock('@xyflow/react', () => ({
  ...globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }),
  // Fuori dal canvas: NodeHandle non disegna nulla, come nel pannello vero.
  useNodeId: () => null,
}));
// Nessuna risposta del server: i moduli restano nel loro stato di caricamento.
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
vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(() => new Promise(() => {})),
  getCachedTransformRegistry: () => ({
    categories: { base: { id: 'base', name: 'Base', icon: '📊' } },
    transforms: {
      filter: { name: 'Filtra righe', category: 'base', icon: '🔍', inputs: 'single', parameters: [] },
      join: { name: 'Join', category: 'base', icon: '🔗', inputs: 'multi', parameters: [] },
    },
  }),
  isMultiInputOp: (op) => op === 'join',
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import TableNode from '../../src/components/nodes/TableNode';
import FeatureLayerNode from '../../src/components/nodes/FeatureLayerNode';
import DataSourceNode from '../../src/components/nodes/DataSourceNode';
import StaticValuesNode from '../../src/components/nodes/StaticValuesNode';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import ProjectSourceNode from '../../src/components/nodes/ProjectSourceNode';
import FileSourceNode from '../../src/components/nodes/FileSourceNode';
import ResultNode from '../../src/components/nodes/ResultNode';
import CaricanNode from '../../src/components/nodes/CaricanNode';
import CaricanArcGISNode from '../../src/components/nodes/CaricanArcGISNode';
import AnnotationNode from '../../src/components/nodes/AnnotationNode';
import TransformNode from '../../src/components/transforms/TransformNode';
import MultiJoinNode from '../../src/components/transforms/MultiJoinNode';
import ApiEnrichNode from '../../src/components/panels/ApiEnrichNode';

// Nome del componente nel file dell'editor -> componente.
const COMPONENTS = {
  TableNode, FeatureLayerNode, DataSourceNode, StaticValuesNode, ProjectInputNode,
  ProjectSourceNode, FileSourceNode, ResultNode, CaricanNode, CaricanArcGISNode,
  AnnotationNode, TransformNode, MultiJoinNode, ApiEnrichNode,
};

const EDITORS = [
  ['estrai', 'EstraiEditor.jsx'],
  ['manipola', 'ManipolaEditor.jsx'],
  ['arricchisci', 'ArricchisciEditor.jsx'],
];
const pages = path.resolve(import.meta.dirname, '../../src/pages');
const editorMaps = EDITORS.map(([mode, file]) => {
  const src = fs.readFileSync(path.join(pages, file), 'utf-8');
  const body = src.match(/const nodeTypes = \{([\s\S]*?)\n\};/)[1].replace(/\/\/[^\n]*/g, '');
  const pairs = [...body.matchAll(/\b(\w+):\s*(\w+)/g)].map(m => [m[1], m[2]]);
  return { mode, file, pairs };
});

// Dati minimi per tipo: quanto basta perche' il modulo si monti.
const SAMPLE = {
  transform: { transform_op: 'filter', config: {} },
  datasourceNode: { mode: 'source', columns: [] },
  datasource_node: { mode: 'source', columns: [] },
  resultNode: { fileName: 'output', format: 'csv', columns: [] },
  result: { fileName: 'output', format: 'csv', columns: [], isManipulate: true },
  multi_join: { config: { join_keys: [] }, columns: [] },
};

// Ogni tipo di ogni editor, note escluse (restano libere sul canvas).
const CASES = editorMaps.flatMap(({ mode, file, pairs }) => pairs
  .filter(([type]) => type !== 'comment')
  .map(([type, component]) => ({ mode, file, type, component })));

const deleteSpy = vi.fn();
let seenProps;

function mount({ mode, type, component }, { readOnly = false } = {}) {
  const store = useCanvasStore.getState();
  store.reset();
  store.init('p1', mode);
  store.setReadOnly(readOnly);
  store.loadGraph([{ id: 'n1', type, position: { x: 0, y: 0 }, selected: true, data: { ...(SAMPLE[type] || {}) } }], []);
  useCanvasStore.setState({ deleteNode: deleteSpy });
  canvasStateRef.current = useCanvasStore.getState();
  const Full = COMPONENTS[component];
  // Il modulo e' avvolto per leggere le props che riceve dal guscio.
  const Spy = (props) => { seenProps = props; return <Full {...props} />; };
  return render(<ConfirmProvider><NodeInspector fullNodeTypes={{ [type]: Spy }} prefix={mode} /></ConfirmProvider>);
}

const bodyOf = (container) => container.querySelector('.node-inspector__body');

beforeEach(() => {
  deleteSpy.mockReset();
  seenProps = null;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Blocco A, regola 6: il guscio possiede intestazione, chiusura ed eliminazione', () => {
  it('le mappe degli editor sono state lette e ogni componente e\' elencato', () => {
    expect(CASES.length).toBeGreaterThan(20);
    const unknown = editorMaps.flatMap(({ file, pairs }) => pairs
      .filter(([, c]) => !COMPONENTS[c]).map(([t, c]) => `${file}: ${t} -> ${c}`));
    expect(unknown).toEqual([]);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: una chiusura, un solo "Elimina passo", nessuna intestazione propria', async (_, c) => {
    const { container } = mount(c);
    expect(screen.getAllByRole('button', { name: 'Chiudi pannello' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Elimina passo' })).toHaveLength(1);

    const body = bodyOf(container);
    const headers = [...body.querySelectorAll('[class]')]
      .filter(el => [...el.classList].some(cls => cls.endsWith('__header')))
      .map(el => el.className);
    expect(headers).toEqual([]);

    // Nessun altro pulsante che elimini il nodo.
    const deleting = [...body.querySelectorAll('button')].filter(b => /Elimina nodo/i.test(
      `${b.getAttribute('title') || ''} ${b.getAttribute('aria-label') || ''}`,
    ));
    expect(deleting.map(b => b.outerHTML)).toEqual([]);
    for (const b of [...body.querySelectorAll('button')].filter(x => x.textContent.trim() === '×' && !x.disabled)) {
      await act(async () => { fireEvent.click(b); });
    }
    expect(deleteSpy).not.toHaveBeenCalled();

    // Niente `selected` forzato dentro il pannello.
    expect(seenProps).toBeTruthy();
    expect(seenProps.selected).toBeUndefined();
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: "Elimina passo" chiede conferma e poi elimina', async (_, c) => {
    mount(c);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina passo' })); });
    await answerConfirm(false);
    expect(deleteSpy).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Elimina passo' })); });
    await answerConfirm(true, { confirmText: 'Elimina' });
    expect(deleteSpy).toHaveBeenCalledWith('n1');
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: in sola lettura niente "Elimina passo" e titolo bloccato', (_, c) => {
    mount(c, { readOnly: true });
    expect(screen.queryByRole('button', { name: 'Elimina passo' })).toBeNull();
    expect(screen.getByLabelText('Titolo del passo').disabled).toBe(true);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: fuoco e uscita dal titolo senza modifiche non scrivono', (_, c) => {
    mount(c);
    const before = useCanvasStore.getState();
    const field = screen.getByLabelText('Titolo del passo');
    expect(field.value).toBe('');
    expect(field.placeholder).not.toBe('');
    fireEvent.focus(field);
    fireEvent.blur(field);
    fireEvent.keyDown(field, { key: 'Enter' });
    const after = useCanvasStore.getState();
    expect(after.nodes[0].data.title).toBeUndefined();
    expect(after.isDirty).toBe(before.isDirty);
    expect(after.past.length).toBe(before.past.length);
  });
});

// Fase 5, unione T5+T6, R1.10: in sola lettura i controlli del form sono
// spenti DAVVERO (fieldset disabled del pannello), per ogni pannello di ogni
// editor; fuori dal form restano solo la navigazione del pannello.
describe('R1.10: in sola lettura ogni controllo del form del passo e\' disabilitato', () => {
  const CONTROLS = 'input, select, textarea, button';
  let total = 0;

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: nessun controllo attivo nel form', (_, c) => {
    const { container } = mount(c, { readOnly: true });
    const form = container.querySelector('.node-inspector__form');
    expect(form).toBeTruthy();
    expect(form.disabled).toBe(true);
    const controls = [...form.querySelectorAll(CONTROLS)];
    total += controls.length;
    const active = controls.filter(el => !el.matches(':disabled')).map(el => el.outerHTML.slice(0, 120));
    expect(active).toEqual([]);
  });

  it.each(CASES.map(c => [`${c.mode} · ${c.type}`, c]))('%s: modificabile, il form non e\' spento', (_, c) => {
    const { container } = mount(c);
    expect(container.querySelector('.node-inspector__form').disabled).toBe(false);
  });

  it('la verifica ha visto dei controlli (i pannelli si sono montati)', () => {
    expect(total).toBeGreaterThan(CASES.length);
  });

  it('il form dell\'arco (join) e\' spento allo stesso modo', () => {
    const store = useCanvasStore.getState();
    store.reset();
    store.init('p1', 'estrai');
    store.setReadOnly(true);
    store.loadGraph([
      { id: 'a', type: 'tableNode', position: { x: 0, y: 0 }, data: {} },
      { id: 'b', type: 'tableNode', position: { x: 0, y: 0 }, data: {} },
    ], [{ id: 'e1', source: 'a', target: 'b', selected: true, data: {} }]);
    const edgeInspector = {
      kind: 'join', icon: '🔗', matches: () => true,
      entry: () => ({ title: 'Join', summary: '' }),
      Panel: () => <div><input aria-label="campo" /><select aria-label="scelta"><option>x</option></select><button type="button">Azione</button></div>,
    };
    const { container } = render(<ConfirmProvider><NodeInspector fullNodeTypes={{}} edgeInspector={edgeInspector} /></ConfirmProvider>);
    const form = container.querySelector('.node-inspector__form');
    expect(form).toBeTruthy();
    expect([...form.querySelectorAll(CONTROLS)].every(el => el.matches(':disabled'))).toBe(true);
    // La navigazione del pannello resta usabile.
    expect(screen.getByRole('button', { name: 'Chiudi pannello' }).disabled).toBe(false);
  });
});
