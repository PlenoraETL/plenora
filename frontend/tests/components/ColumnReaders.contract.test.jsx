/**
 * Blocco A, regola 4 (F2): i lettori montati leggono la fonte unica.
 *  - ResultNode (Manipola): la griglia "Colonne in uscita" riceve le colonne
 *    con l'id e lo scrive (name_column_id): dopo una rinomina a monte la
 *    riga resta la stessa;
 *  - MultiJoinNode: le sorgenti nell'ordine delle maniglie, solo le colonne
 *    che escono davvero (le nascoste no);
 *  - stato visibile: "nomi incerti" e l'avviso di rete con le ultime colonne.
 *
 * Enumerazione dei LETTORI (ogni file di src che chiama useColumnsOf),
 * montati davvero:
 *  - ricevono colonne con id dalla fonte unica;
 *  - mostrano ColumnStatusNote in modo uniforme, anche con zero colonne
 *    ("colonne in calcolo" prima della prima risposta, l'avviso di rete);
 *  - 10 fotogrammi di trascinamento: zero ridisegni.
 * Piu' l'Output di Arricchisci, che scrive e legge per id come il backend (A3, P6).
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { Profiler } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { canvasStateRef, readsRef } = vi.hoisted(() => ({
  canvasStateRef: { current: { nodes: [], edges: [] } },
  readsRef: { current: [] },
}));

vi.mock('@xyflow/react', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }));
vi.mock('../../src/components/modals/RunHistoryModal', () => ({ default: () => null }));
vi.mock('../../src/hooks/useInlinePreview', () => ({
  default: () => ({ run: vi.fn(), clear: vi.fn(), status: 'idle', data: null, error: null }),
}));
// Ogni lettura dei lettori passa da useColumnsOf: la registriamo (la vera).
vi.mock('../../src/hooks/useColumns', async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    useColumnsOf: (nodeId, opts) => {
      const result = real.useColumnsOf(nodeId, opts);
      readsRef.current.push({ nodeId, opts: opts || {}, result });
      return result;
    },
  };
});
vi.mock('../../src/utils/transformRegistry', () => ({
  fetchTransformRegistry: vi.fn(async () => null),
  getCachedTransformRegistry: () => null,
  isMultiInputOp: (op) => op === 'join',
}));
vi.mock('../../src/api/apiConnections', () => ({ getApiConnection: vi.fn(async () => ({})) }));
vi.mock('../../src/api/files', () => ({ uploadFile: vi.fn() }));
vi.mock('../../src/api/carica', () => ({
  getCaricaSchemas: vi.fn(async () => ({ schemas: [], db_type: null })),
  getCaricaTables: vi.fn(async () => []),
  getCaricaColumns: vi.fn(async () => []),
  postCaricaAutoMapping: vi.fn(),
  postCaricaCheckConflicts: vi.fn(),
}));
vi.mock('../../src/api/features', () => ({
  listFeatureConnections: vi.fn(async () => []),
  browseServices: vi.fn(async () => []),
  browseLayers: vi.fn(async () => []),
  getLayerSchema: vi.fn(async () => null),
  listFolders: vi.fn(async () => []),
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import useColumnStore from '../../src/store/useColumnStore';
import ResultNode from '../../src/components/nodes/ResultNode';
import MultiJoinNode from '../../src/components/transforms/MultiJoinNode';
import TransformNode from '../../src/components/transforms/TransformNode';
import ApiEnrichNode from '../../src/components/panels/ApiEnrichNode';
import CaricanNode from '../../src/components/nodes/CaricanNode';
import CaricanArcGISNode from '../../src/components/nodes/CaricanArcGISNode';

const col = (name, id, extra = {}) => ({ id, name, type: 'str', source_name: name, source_node: 's', is_calculated: false, optional: false, ...extra });

const loadCanvas = (nodes, edges, mode = 'manipola') => {
  const st = useCanvasStore.getState();
  st.init('p1', mode);
  st.loadGraph(nodes, edges);
  canvasStateRef.current = { nodes, edges };
};

beforeEach(() => {
  readsRef.current = [];
  useColumnStore.setState({ projectId: 'p1', schemas: {}, states: {}, status: 'ok', error: null });
});

// ── Enumerazione dei lettori ───────────────────────────────────────────────
const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory()
  ? walk(path.join(dir, e.name))
  : (/\.(js|jsx)$/.test(e.name) ? [path.join(dir, e.name)] : [])));
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
const readerFiles = () => walk(SRC)
  .map(f => [path.relative(SRC, f).split(path.sep).join('/'), stripComments(fs.readFileSync(f, 'utf-8'))])
  .filter(([rel, text]) => !rel.startsWith('hooks/') && /\buseColumnsOf\(/.test(text))
  .map(([rel]) => rel)
  .sort();

// file -> come montarlo: tipo del nodo nel canvas, dati, componente, editor.
const READERS = {
  'components/transforms/TransformNode.jsx': { type: 'transform', data: { transform_op: 'filter', config: {} }, Component: TransformNode },
  'components/transforms/MultiJoinNode.jsx': { type: 'multi_join', data: { config: { join_keys: [], join_type: 'inner' } }, Component: MultiJoinNode },
  'components/panels/ApiEnrichNode.jsx': { type: 'api_enrich', data: { parameters: [], output_mapping: [] }, Component: ApiEnrichNode, mode: 'arricchisci' },
  'components/nodes/ResultNode.jsx': { type: 'result', data: { isManipulate: true, fileName: 'out', format: 'csv' }, Component: ResultNode },
  'components/nodes/CaricanNode.jsx': { type: 'carica', data: { dest_type: 'db' }, Component: CaricanNode },
  'components/nodes/CaricanArcGISNode.jsx': { type: 'carica_arcgis', data: {}, Component: CaricanArcGISNode },
};
const READER_CASES = Object.entries(READERS);

const mountReader = async ([, reader]) => {
  const nodes = [
    { id: 's', type: 'file_source', position: { x: 0, y: 0 }, data: { columns: [] } },
    { id: 'r', type: reader.type, position: { x: 300, y: 0 }, data: reader.data },
  ];
  const edges = [{ id: 'e', source: 's', target: 'r', targetHandle: 'input-0' }];
  loadCanvas(nodes, edges, reader.mode || 'manipola');
  const commits = { count: 0 };
  const { Component } = reader;
  const view = render(
    <Profiler id="reader" onRender={() => { commits.count += 1; }}>
      <Component id="r" data={reader.data} />
    </Profiler>,
  );
  // Effetti asincroni del montaggio (registro, connessioni): si lasciano finire.
  await act(async () => { await Promise.resolve(); });
  return { view, commits };
};

describe('lettori della fonte unica: enumerazione (ogni file che chiama useColumnsOf)', () => {
  it('ogni lettore di src ha la sua voce qui (e nessuna voce in piu\')', () => {
    expect(readerFiles()).toEqual(Object.keys(READERS).sort());
  });

  it.each(READER_CASES)('%s: prima della prima risposta mostra "colonne in calcolo", anche con zero colonne', async (...c) => {
    useColumnStore.setState({ schemas: {}, states: {}, status: 'loading', error: null });
    await mountReader(c);
    expect(screen.getAllByTestId('column-status')[0].textContent).toContain('colonne in calcolo');
  });

  it.each(READER_CASES)('%s: errore del server -> avviso con Riprova, le ultime colonne restano', async (...c) => {
    useColumnStore.setState({ schemas: { s: [col('a', 'id_a')] }, states: {}, status: 'error', error: 'Errore del server' });
    await mountReader(c);
    expect(screen.getAllByTestId('column-status')[0].textContent).toMatch(/colonne non aggiornate/);
    expect(screen.getAllByRole('button', { name: 'Riprova' }).length).toBeGreaterThan(0);
  });

  it.each(READER_CASES)('%s: riceve le colonne in ingresso con l\'id dal server', async (...c) => {
    useColumnStore.setState({ schemas: { s: [col('a', 'id_a'), col('b', 'id_b')] }, states: {}, status: 'ok', error: null });
    await mountReader(c);
    const inputs = readsRef.current.filter(r => r.nodeId === 'r' && r.opts.side === 'input');
    expect(inputs.length).toBeGreaterThan(0);
    for (const read of inputs) {
      expect(read.result.columns.map(x => [x.id, x.name, x.input_node])).toEqual([['id_a', 'a', 's'], ['id_b', 'b', 's']]);
    }
    expect(screen.queryByTestId('column-status')).toBeNull();
  });

  it.each(READER_CASES)('%s: 10 fotogrammi di trascinamento -> zero ridisegni, stessa identita\'', async (...c) => {
    useColumnStore.setState({ schemas: { s: [col('a', 'id_a')], r: [col('a', 'id_a')] }, states: {}, status: 'ok', error: null });
    const { commits } = await mountReader(c);
    const before = commits.count;
    expect(before).toBeGreaterThan(0); // il Profiler conta davvero
    const lastRead = readsRef.current.filter(r => r.nodeId === 'r').at(-1).result;
    for (let frame = 1; frame <= 10; frame += 1) {
      act(() => {
        useCanvasStore.setState(st => ({
          nodes: st.nodes.map(n => ({ ...n, position: { x: n.position.x + frame, y: n.position.y + frame } })),
        }));
      });
    }
    expect(commits.count - before).toBe(0);
    expect(readsRef.current.filter(r => r.nodeId === 'r').at(-1).result).toBe(lastRead);
  });
});

describe('Output di Arricchisci: per id, come il backend (PROJECT_COLUMN_CAPABILITIES)', () => {
  it('togliere una colonna scrive name_column_id; dopo la rinomina a monte la riga resta tolta', () => {
    const nodes = [
      { id: 'a', type: 'api_enrich', position: { x: 0, y: 0 }, data: {} },
      { id: 'r', type: 'result', position: { x: 0, y: 0 }, data: { isArricchisci: true, fileName: 'out', format: 'csv' } },
    ];
    loadCanvas(nodes, [{ id: 'e', source: 'a', target: 'r', targetHandle: 'input' }], 'arricchisci');
    useColumnStore.setState({ schemas: { a: [col('citta', 'ic'), col('pop', 'ip')] } });
    const { rerender } = render(<ResultNode id="r" data={useCanvasStore.getState().nodes[1].data} />);
    fireEvent.click(screen.getByLabelText('Tieni pop'));
    const written = useCanvasStore.getState().nodes.find(n => n.id === 'r').data.output_columns;
    expect(written).toEqual([
      { name: 'citta', keep: true, name_column_id: 'ic' },
      { name: 'pop', keep: false, name_column_id: 'ip' },
    ]);
    // A monte "pop" diventa "abitanti" (stesso id): il backend la esclude, la griglia anche.
    act(() => { useColumnStore.setState({ schemas: { a: [col('citta', 'ic'), col('abitanti', 'ip')] } }); });
    rerender(<ResultNode id="r" data={useCanvasStore.getState().nodes[1].data} />);
    expect(screen.getByLabelText('Tieni abitanti').checked).toBe(false);
  });
});

describe('ResultNode: griglia dell\'Output sulla fonte unica, per id', () => {
  const nodes = [
    { id: 't', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} } },
    { id: 'r', type: 'result', position: { x: 0, y: 0 }, data: { isManipulate: true, fileName: 'out', format: 'csv' } },
  ];
  const edges = [{ id: 'e', source: 't', target: 'r', targetHandle: 'input' }];

  it('togliere una colonna scrive output_columns con name_column_id; dopo la rinomina a monte la riga resta', () => {
    loadCanvas(nodes, edges);
    useColumnStore.setState({ schemas: { t: [col('citta', 'ic'), col('pop', 'ip')] } });
    const { rerender } = render(<ResultNode id="r" data={useCanvasStore.getState().nodes[1].data} />);
    fireEvent.click(screen.getByLabelText('Tieni pop'));
    const written = useCanvasStore.getState().nodes.find(n => n.id === 'r').data.output_columns;
    expect(written).toEqual([
      { name: 'citta', keep: true, name_column_id: 'ic' },
      { name: 'pop', keep: false, name_column_id: 'ip' },
    ]);
    // A monte "pop" diventa "abitanti" (stesso id): la riga e' la stessa, ancora tolta.
    act(() => { useColumnStore.setState({ schemas: { t: [col('citta', 'ic'), col('abitanti', 'ip')] } }); });
    rerender(<ResultNode id="r" data={useCanvasStore.getState().nodes[1].data} />);
    expect(screen.getByLabelText('Tieni abitanti').checked).toBe(false);
    expect(screen.queryByLabelText('Tieni pop')).toBeNull();
    // Nessun dato derivato e' stato scritto nel nodo.
    const data = useCanvasStore.getState().nodes.find(n => n.id === 'r').data;
    expect(data).not.toHaveProperty('columns');
    expect(data).not.toHaveProperty('availableColumns');
  });

  it('nomi incerti e errore di rete: avviso sobrio, le ultime colonne restano', () => {
    loadCanvas(nodes, edges);
    useColumnStore.setState({
      schemas: { t: [col('x', 'ix')] },
      states: { t: { names_status: 'indeterminate', types_status: 'certain', order_status: 'indeterminate', reason: 'pivot' } },
      status: 'error', error: 'Errore del server',
    });
    render(<ResultNode id="r" data={useCanvasStore.getState().nodes[1].data} />);
    expect(screen.getByText('nomi incerti')).toBeTruthy();
    expect(screen.getByText(/colonne non aggiornate/)).toBeTruthy();
    expect(screen.getByLabelText('Tieni x')).toBeTruthy();
  });
});

describe('MultiJoinNode: sorgenti nell\'ordine delle maniglie (F1.13)', () => {
  it('ordine per maniglia, colonne che escono dalla sorgente, uscita prevista dal server', () => {
    const nodes = [
      { id: 'a', type: 'project_source', position: { x: 0, y: 0 }, data: { source_project_id: 'A', columns: [{ name: 'k' }, { name: 'segreta', visible: false }] } },
      { id: 'b', type: 'project_source', position: { x: 0, y: 0 }, data: { source_project_id: 'B', columns: [{ name: 'k' }] } },
      { id: 'm', type: 'multi_join', position: { x: 0, y: 0 }, data: { config: { join_keys: ['k'], join_type: 'inner' } } },
    ];
    // Archi in ordine inverso rispetto alle maniglie.
    const edges = [
      { id: 'e2', source: 'a', target: 'm', targetHandle: 'input-1' },
      { id: 'e1', source: 'b', target: 'm', targetHandle: 'input-0' },
    ];
    loadCanvas(nodes, edges);
    useColumnStore.setState({ schemas: {
      a: [col('k', 'ka')], b: [col('k', 'kb'), col('v', 'vb')], m: [col('k', 'kb'), col('v', 'vb')],
    } });
    render(<MultiJoinNode id="m" data={nodes[2].data} />);
    expect(screen.queryByText(/segreta/)).toBeNull();
    expect(screen.getByText(/Colonne in uscita \(2\)/)).toBeTruthy();
  });
});
