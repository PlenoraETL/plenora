import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  COLUMN_ROLES, DERIVED_COLUMNS_TYPES, PROJECT_COLUMN_CAPABILITIES, addedColumns, columnOrigin, columnSignature,
  columnsOf, createColumnsMemo, normalizeSchemasResponse, outputColumnsAsRead, outputResolvesById,
  sortedInputEdges, uncertainAspects,
} from '../../src/utils/columnSource';
import { canonicalNodeType, persistedNodeData } from '../../src/utils/nodeTypeRegistry';
import { deserializeNodes } from '../../src/utils/nodeFactories';
import { serializeCanvas } from '../../src/utils/canvasSerializer';
import { createColumnStore, startColumnSync } from '../../src/store/useColumnStore';
import { createCanvasStore } from '../../src/store/useCanvasStore';
import { gridConfig, gridRows } from '../../src/components/transforms/panels/panelConfig';

/**
 * Blocco A, regola 4 (F2): UNA sola fonte di colonne nel frontend.
 * Test di enumerazione:
 *  (a) nessun file in src scrive colonne derivate nei nodi (ogni chiave
 *      `columns` censita con il suo conteggio);
 *  (b) ogni tipo di nodo di ogni mappa nodeTypes dei tre editor e' servito
 *      da columnsOf: ripiego secondo il ruolo prima della risposta, schema
 *      del server (con id e stato) dopo, anche come ingresso del nodo a valle;
 *  (c) il selettore da' colonne con id, nell'ordine dei lati, ed e'
 *      memorizzato per nodo (stessa identita' trascinando; nuova solo se lo
 *      spostamento verticale cambia l'ordine dei lati). I lettori MONTATI
 *      sono in tests/components/ColumnReaders.contract.test.jsx;
 *  (d) la griglia dell'Output segue l'id SOLO dove il backend risolve per id
 *      (PROJECT_COLUMN_CAPABILITIES, enumerata sui tipi di progetto).
 * Piu' la sottoscrizione unica: ritardo, risposte superate (anche tornando
 * alla stessa firma), errori. Il salvataggio con la firma e' in
 * tests/shared/columnSignatureSave.contract.test.js.
 */
const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory()
  ? walk(path.join(dir, e.name))
  : (/\.(js|jsx)$/.test(e.name) ? [path.join(dir, e.name)] : [])));
const SOURCES = walk(SRC).map(f => [path.relative(SRC, f).replace(/\\/g, '/'), fs.readFileSync(f, 'utf-8')]);
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

const EDITORS = ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx'];
const editorNodeTypes = (rel) => {
  const block = code(read(rel)).match(/const nodeTypes\s*=\s*\{([\s\S]*?)\};/)[1];
  return block.split(',').map(e => e.trim()).filter(Boolean).map(e => e.match(/^['"]?(\w+)['"]?/)[1]);
};

const col = (name, id, extra = {}) => ({ id, name, type: 'str', source_name: name, source_node: 'x', is_calculated: false, optional: false, ...extra });
const snap = (over = {}) => ({ schemas: {}, states: {}, status: 'ok', error: null, nodes: [], edges: [], mode: 'manipola', ...over });

describe('(a) nessun file scrive colonne derivate nei nodi', () => {
  it('nessuno scrive inputColumns o availableColumns (come chiave o assegnazione)', () => {
    const bad = SOURCES.filter(([, text]) => /\b(inputColumns|availableColumns)\s*:|data\.(inputColumns|availableColumns)\s*=[^=]/.test(code(text)))
      .map(([rel]) => rel);
    expect(bad).toEqual([]);
  });

  it('data.columns si assegna solo dove e\' dichiarato: serializer (dalla mappa) e normalizzazione dei layer', () => {
    const writers = SOURCES.filter(([, text]) => /data\.columns\s*=[^=]/.test(code(text))).map(([rel]) => rel);
    expect(writers.sort()).toEqual(['utils/canvasSerializer.js', 'utils/nodeFactories.js']);
  });

  it('ogni chiave `columns` in un oggetto letterale e\' censita UNA PER UNA (elenco chiuso con i conteggi)', () => {
    // Chiave esplicita (`columns:`) o abbreviata nei dati (`{ ...n.data, columns }`).
    // file -> [quante, perche' non sono colonne derivate]. Una chiave in
    // piu' (anche in un file gia' elencato) o in meno fa fallire il test:
    // ogni nuova scrittura si guarda e si censisce.
    const ALLOWED = {
      'store/useCanvasStore.js': [3, 'toggleColumn/toggleAllColumns/updateColumnAlias: visibile e alias delle tabelle'],
      'components/nodes/DataSourceNode.jsx': [3, 'colonne dichiarate del Data Source'],
      'components/nodes/FileSourceNode.jsx': [2, 'colonne dichiarate del file'],
      'components/nodes/ProjectSourceNode.jsx': [4, 'colonne (visibile/alias) del progetto sorgente'],
      'components/nodes/result/standard/OutputColumnsSection.jsx': [1, 'Estrai: visibile/alias delle tabelle a monte (sorgenti)'],
      'pages/EstraiEditor.jsx': [3, 'creazione di tabelle e layer; anteprima (non un nodo)'],
      'pages/ManipolaEditor.jsx': [3, 'creazione delle sorgenti (progetto, file, Data Source)'],
      'pages/ArricchisciEditor.jsx': [3, 'creazione delle sorgenti; anteprima (non un nodo)'],
      'components/transforms/panels/SelectColumnsPanel.jsx': [1, 'config.columns di "Seleziona e rinomina"'],
      'components/transforms/ParameterInput.jsx': [1, 'config.columns di un parametro'],
      'components/transforms/MultiJoinNode.jsx': [1, 'vista locale delle sorgenti (non un nodo)'],
      'utils/columnSource.js': [4, 'risultato del selettore (non un nodo)'],
      'utils/nodeTypeRegistry.js': [5, 'default delle sorgenti (i tipi derivati non hanno il campo)'],
      'utils/displayIcon.js': [1, 'nome di icona'],
      'utils/glossary.js': [1, 'descrizione di un parametro'],
    };
    const KEY = /(^|[^\w.])columns\s*:|\.\.\.[\w.?]*data\s*,\s*columns\s*(?=[,}])/g;
    const found = Object.fromEntries(SOURCES
      .map(([rel, text]) => [rel, (code(text).match(KEY) || []).length])
      .filter(([, n]) => n > 0));
    expect(found).toEqual(Object.fromEntries(Object.entries(ALLOWED).map(([rel, [n]]) => [rel, n])));
  });

  it('i tipi derivati non tengono `columns` nei dati: ne\' al caricamento ne\' al salvataggio dal nodo', () => {
    for (const type of DERIVED_COLUMNS_TYPES) {
      const raw = { id: 'n', type, position: { x: 0, y: 0 }, columns: [{ name: 'vecchia' }], inputColumns: [{ name: 'x' }], availableColumns: [{ name: 'y' }] };
      const [loaded] = deserializeNodes([raw]);
      expect(loaded.data, type).not.toHaveProperty('columns');
      expect(loaded.data, type).not.toHaveProperty('inputColumns');
      expect(loaded.data, type).not.toHaveProperty('availableColumns');
      expect(persistedNodeData({ type, data: { columns: [{ name: 'z' }] } }), type).not.toHaveProperty('columns');
      expect(serializeCanvas([{ ...raw, data: { columns: [{ name: 'z' }] } }], []).nodes[0], type).not.toHaveProperty('columns');
    }
    // Le sorgenti invece tengono la loro configurazione.
    expect(persistedNodeData({ type: 'source', data: { columns: [{ name: 'a', visible: false }] } }).columns)
      .toEqual([{ name: 'a', visible: false }]);
  });

  it('nessuna chiamata sparsa: solo la sottoscrizione unica chiede gli schemi al server', () => {
    const callers = SOURCES.filter(([rel, text]) => !rel.startsWith('api/')
      && /\b(inferProjectSchemas|getProjectSchemas|getNodeSchema)\b/.test(code(text))).map(([rel]) => rel);
    expect(callers).toEqual(['store/useColumnStore.js']);
  });
});

describe('(b) ogni tipo di nodo dei tre editor e\' servito da columnsOf', () => {
  const backend = fs.readFileSync(path.resolve(import.meta.dirname, '../../../backend/app/core/projects/schema_inference.py'), 'utf-8');
  const handlers = backend.match(/NODE-DISPATCH-REGISTRY:start([\s\S]*?)NODE-DISPATCH-REGISTRY:end/)[1];
  const backendTypes = new Set([...handlers.matchAll(/"(\w+)":/g)].map(m => m[1]));

  it.each(EDITORS)('%s: ruolo dichiarato, gestore nel backend, colonne dal selettore', (rel) => {
    const keys = editorNodeTypes(rel);
    expect(keys.length).toBeGreaterThan(3);
    for (const key of keys) {
      const type = canonicalNodeType(key);
      expect(COLUMN_ROLES[type], `${rel}: ${key}`).toBeDefined();
      expect(backendTypes.has(type), `${rel}: ${key} senza gestore nel backend`).toBe(true);
      const role = COLUMN_ROLES[type];
      // La stessa configurazione dichiarata per tutti: conta solo il ruolo.
      const data = { columns: [{ name: 'dich' }], fields: [{ name: 'dich' }], expected_columns: [{ name: 'dich' }] };
      const node = { id: 'n', type: key, position: { x: 0, y: 0 }, data };
      const down = { id: 'd', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter' } };
      const edges = [{ id: 'e', source: 'n', target: 'd', targetHandle: 'input-0' }];
      // 1. Prima della risposta: il ripiego dipende dal ruolo, mai colonne inventate.
      const before = columnsOf(snap({ nodes: [node, down], edges, status: 'loading' }), 'n');
      expect(before.known, key).toBe(false);
      expect(before.columns.map(c => c.name), `${key} (${role})`).toEqual(role === 'declared' ? ['dich'] : []);
      // 2. Dopo la risposta: lo schema del SERVER vince sulla configurazione,
      //    e arriva con l'id al nodo a valle (ingresso), stato compreso.
      const states = normalizeSchemasResponse({ schemas: { n: [] }, schema_states: { n: { names_status: 'indeterminate' } } }).states;
      const after = snap({ nodes: [node, down], edges, schemas: { n: [col('srv', 'is')] }, states });
      expect(columnsOf(after, 'n').columns.map(c => [c.name, c.id]), key).toEqual([['srv', 'is']]);
      const input = columnsOf(after, 'd', { side: 'input' });
      expect(input.columns.map(c => [c.name, c.id, c.input_node]), key).toEqual([['srv', 'is', 'n']]);
      expect(input.state.names_status, key).toBe('indeterminate');
    }
  });

  it('i tipi derivati del registro coincidono con il ruolo "derived"', () => {
    const derived = Object.entries(COLUMN_ROLES).filter(([, r]) => r === 'derived').map(([t]) => t).sort();
    expect([...DERIVED_COLUMNS_TYPES].sort()).toEqual(derived);
  });
});

describe('(c) il selettore da\' colonne con id, per nodo e memorizzato', () => {
  const nodes = [
    { id: 'L', type: 'file_source', position: { y: 500 }, data: {} },
    { id: 'R', type: 'file_source', position: { y: 0 }, data: {} },
    { id: 'J', type: 'transform', data: { transform_op: 'join' } },
  ];
  // Archi in ordine "sbagliato": conta la maniglia, come nel backend.
  const edges = [
    { id: 'e2', source: 'R', target: 'J', targetHandle: 'input-1' },
    { id: 'e1', source: 'L', target: 'J', targetHandle: 'input-0' },
  ];
  const schemas = { L: [col('k', 'iL')], R: [col('k', 'iR'), col('v', 'iV')], J: [col('k', 'iL'), col('v', 'iV')] };
  const s = snap({ nodes, edges, schemas });

  it('ingresso per lato e per sorgente nell\'ordine delle maniglie (F1.13)', () => {
    expect(sortedInputEdges('J', nodes, edges).map(e => e.source)).toEqual(['L', 'R']);
    expect(columnsOf(s, 'J', { side: 'left' }).columns.map(c => c.id)).toEqual(['iL']);
    expect(columnsOf(s, 'J', { side: 'right' }).columns.map(c => c.id)).toEqual(['iR', 'iV']);
    expect(columnsOf(s, 'J', { source: 'R' }).columns.map(c => c.id)).toEqual(['iR', 'iV']);
    const all = columnsOf(s, 'J', { side: 'input' });
    expect(all.inputs.map(i => i.nodeId)).toEqual(['L', 'R']);
    for (const c of all.columns) expect(Object.keys(c)).toEqual(expect.arrayContaining(['id', 'name', 'type', 'source_name', 'optional', 'input_node']));
  });

  it('senza maniglia vale la posizione verticale (come incoming_edge_sort_key)', () => {
    const plain = [{ id: 'a', source: 'L', target: 'J' }, { id: 'b', source: 'R', target: 'J' }];
    expect(sortedInputEdges('J', nodes, plain).map(e => e.source)).toEqual(['R', 'L']);
  });

  it('l\'Output di Estrai riceve tutte le tabelle a monte (la FROM), in ordine di visita', () => {
    const en = [
      { id: 't1', type: 'tableNode', data: {} }, { id: 't2', type: 'tableNode', data: {} },
      { id: 'r', type: 'resultNode', data: {} },
    ];
    const ee = [{ id: 'j', source: 't1', target: 't2', data: { join_type: 'left' } }, { id: 'o', source: 't2', target: 'r' }];
    const r = columnsOf(snap({ mode: 'estrai', nodes: en, edges: ee, schemas: { t1: [col('a', 'i1')], t2: [col('b', 'i2')] } }), 'r', { side: 'input' });
    expect(r.columns.map(c => [c.input_node, c.id])).toEqual([['t2', 'i2'], ['t1', 'i1']]);
  });

  it('colonne aggiunte e origine per id (etichette delle anteprime, colonne dell\'API)', () => {
    const an = [{ id: 'src', type: 'file_source', data: {} }, { id: 'api', type: 'api_enrich', data: {} }, { id: 'res', type: 'result', data: {} }];
    const ae = [{ id: 'a', source: 'src', target: 'api' }, { id: 'b', source: 'api', target: 'res' }];
    const as = snap({ nodes: an, edges: ae, schemas: { src: [col('id', 'c1')], api: [col('id', 'c1'), col('meteo', 'c2')], res: [col('id', 'c1'), col('meteo', 'c2')] } });
    expect(addedColumns(as, 'api').map(c => c.name)).toEqual(['meteo']);
    expect(columnOrigin(as, 'res', 'c1')).toBe('src');
    expect(columnOrigin(as, 'res', 'c2')).toBe('api');
  });

  it('prima della risposta: solo il ripiego DICHIARATO delle sorgenti (visibili, con alias), marcato incerto', () => {
    const src = { id: 's', type: 'tableNode', data: { columns: [{ name: 'a', alias: 'A' }, { name: 'b', visible: false }] } };
    const out = columnsOf(snap({ nodes: [src] }), 's');
    expect(out.known).toBe(false);
    expect(out.columns.map(c => [c.name, c.source_name])).toEqual([['A', 'a']]);
    expect(uncertainAspects(out.state)).toContain('nomi incerti');
    // Un passo senza schema non inventa colonne.
    expect(columnsOf(snap({ nodes: [{ id: 't', type: 'transform', data: {} }] }), 't').columns).toEqual([]);
  });

  it('memorizzato per nodo: 10 fotogrammi di trascinamento -> stesso oggetto; lo spostamento verticale che cambia i lati -> nuovo', () => {
    const plainEdges = [{ id: 'a', source: 'L', target: 'J' }, { id: 'b', source: 'R', target: 'J' }];
    const columns = { schemas, states: {}, status: 'ok', error: null };
    const memo = createColumnsMemo();
    const memoOut = createColumnsMemo();
    let canvas = { nodes, edges: plainEdges, mode: 'manipola' };
    const first = memo(columns, canvas, 'J', { side: 'input' });
    const firstOut = memoOut(columns, canvas, 'J');
    expect(first.inputs.map(i => i.nodeId)).toEqual(['R', 'L']);
    for (let frame = 1; frame <= 10; frame += 1) {
      // Orizzontale (e un po' in verticale senza scavalcare): i lati non cambiano.
      canvas = { ...canvas, nodes: canvas.nodes.map(n => (n.id === 'L' ? { ...n, position: { x: frame * 10, y: 500 - frame } } : n)) };
      expect(memo(columns, canvas, 'J', { side: 'input' })).toBe(first);
      expect(memoOut(columns, canvas, 'J')).toBe(firstOut);
    }
    // L sale sopra R: senza maniglie l'ordine dei lati cambia (come il backend).
    canvas = { ...canvas, nodes: canvas.nodes.map(n => (n.id === 'L' ? { ...n, position: { x: 0, y: -10 } } : n)) };
    const moved = memo(columns, canvas, 'J', { side: 'input' });
    expect(moved).not.toBe(first);
    expect(moved.inputs.map(i => i.nodeId)).toEqual(['L', 'R']);
    // ...e la firma cambia (nuova richiesta), lo spostamento orizzontale no.
    expect(columnSignature(canvas.nodes, plainEdges)).not.toBe(columnSignature(nodes, plainEdges));
    const horizontal = nodes.map(n => (n.id === 'L' ? { ...n, position: { x: 999, y: 500 } } : n));
    expect(columnSignature(horizontal, plainEdges)).toBe(columnSignature(nodes, plainEdges));
    // Una mappa nuova con le stesse colonne non cambia l'identita'.
    const sameAgain = { ...columns, schemas: JSON.parse(JSON.stringify(schemas)) };
    expect(memo(sameAgain, canvas, 'J', { side: 'input' })).toBe(moved);
  });

  it('stato per aspetto: nomi e tipo incerti dal server (es. a valle di un Pivot)', () => {
    const { states } = normalizeSchemasResponse({
      schemas: { p: [{ id: 'x', name: 'x', type: 'str', source_name: 'x' }] },
      schema_states: { p: { names_status: 'indeterminate', types_status: 'indeterminate', order_status: 'indeterminate', reason: 'pivot' } },
    });
    expect(uncertainAspects(states.p)).toEqual(['nomi incerti', 'tipo incerto']);
  });
});

describe('(d) la griglia dell\'Output segue l\'id', () => {
  it('rinomina a monte: alias, scelta e ordine restano sulla stessa colonna', () => {
    const before = [col('città', 'ic'), col('pop', 'ip'), col('area', 'ia')];
    const saved = gridConfig([
      { name: 'area', keep: true, label: '' },
      { name: 'città', keep: true, label: 'Comune' },
      { name: 'pop', keep: false, label: '' },
    ], before);
    expect(saved.map(i => i.name_column_id)).toEqual(['ia', 'ic', 'ip']);
    // A monte "città" diventa "citta" (stesso id) e "pop" diventa "abitanti".
    const after = [col('citta', 'ic'), col('abitanti', 'ip'), col('area', 'ia')];
    const rows = gridRows(after, saved);
    expect(rows.map(r => [r.name, r.keep, r.label, r.missing])).toEqual([
      ['area', true, '', false],
      ['citta', true, 'Comune', false],
      ['abitanti', false, '', false],
    ]);
  });

  it('ogni tipo di progetto (i tre editor) dichiara se il backend risolve l\'Output per id', () => {
    const modes = EDITORS.map(rel => code(read(rel)).match(/useCanvasCore\(projectId,\s*\{\s*mode:\s*'(\w+)'/)[1]);
    expect(Object.keys(PROJECT_COLUMN_CAPABILITIES).sort()).toEqual([...modes].sort());
    for (const mode of modes) expect(typeof PROJECT_COLUMN_CAPABILITIES[mode].outputResolvesById, mode).toBe('boolean');
    // Stato attuale del backend: tutti e tre per id (Arricchisci dalla A3, P6:
    // resolve_output_column_refs in inferenza ed esecuzione).
    expect(outputResolvesById('manipola')).toBe(true);
    expect(outputResolvesById('estrai')).toBe(true);
    expect(outputResolvesById('arricchisci')).toBe(true);
  });

  it('Arricchisci (per id, come il backend): rinomina a monte di una colonna tolta -> resta tolta, come nel file', () => {
    const before = [col('citta', 'ic'), col('pop', 'ip')];
    const saved = gridConfig([
      { name: 'citta', keep: true, label: '' },
      { name: 'pop', keep: false, label: '' },
    ], before, { withIds: outputResolvesById('arricchisci') });
    expect(saved.every(i => 'name_column_id' in i)).toBe(true);
    // Una configurazione salvata con gli id si legge com'e'.
    const legacy = [{ name: 'citta', keep: true, name_column_id: 'ic' }, { name: 'pop', keep: false, name_column_id: 'ip' }];
    expect(outputColumnsAsRead(legacy, 'arricchisci')).toBe(legacy);
    // A monte "pop" diventa "abitanti" (stesso id): stessa voce, ancora tolta.
    const after = [col('citta', 'ic'), col('abitanti', 'ip')];
    for (const config of [saved, outputColumnsAsRead(legacy, 'arricchisci')]) {
      const rows = gridRows(after, config);
      expect(rows.map(r => [r.name, r.keep, r.missing])).toEqual([
        ['citta', true, false],
        ['abitanti', false, false],
      ]);
    }
  });

  it('le colonne facoltative sono marcate e non entrano nella configurazione', () => {
    const rows = gridRows([col('meteo', 'im', { optional: true })], []);
    expect(rows[0].optional).toBe(true);
    expect(gridConfig(rows, [col('meteo', 'im')])[0]).not.toHaveProperty('optional');
  });
});

describe('sottoscrizione unica', () => {
  afterEach(() => { vi.useRealTimers(); });

  const setup = (fetchSchemas) => {
    const canvasStore = createCanvasStore();
    const columnStore = createColumnStore();
    canvasStore.getState().init('p1', 'manipola');
    const stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 200 });
    return { canvasStore, columnStore, stop };
  };
  const load = (canvasStore, data = { output_mapping: [] }) => canvasStore.getState().loadGraph(
    [{ id: 's', type: 'file_source', position: { x: 0, y: 0 }, data: { columns: [{ name: 'a' }] } },
      { id: 'e', type: 'api_enrich', position: { x: 0, y: 0 }, data }],
    [{ id: 'x', source: 's', target: 'e' }],
  );
  const response = (name) => ({ schemas: { e: [{ id: `i_${name}`, name, type: 'str', source_name: name }] }, schema_states: {} });

  it('una richiesta dopo il caricamento, una sola per piu\' modifiche ravvicinate, nessuna per uno spostamento', async () => {
    vi.useFakeTimers();
    const fetchSchemas = vi.fn(async () => response('a'));
    const { canvasStore, columnStore, stop } = setup(fetchSchemas);
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    expect(columnStore.getState().schemas.e[0].id).toBe('i_a');
    // Il mapping di uscita dell'arricchimento cambia: la firma cambia (W5/W4 non servono).
    canvasStore.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'meteo' }] });
    canvasStore.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'meteo2' }] });
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    // Spostare un nodo non cambia le colonne.
    canvasStore.getState().onNodesChange([{ id: 's', type: 'position', position: { x: 50, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    stop();
  });

  it('una risposta superata da una modifica successiva si scarta', async () => {
    vi.useFakeTimers();
    let release;
    const fetchSchemas = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve(response('vecchia')); }))
      .mockImplementation(async () => response('nuova'));
    const { canvasStore, columnStore, stop } = setup(fetchSchemas);
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    canvasStore.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'z' }] });
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(columnStore.getState().schemas.e).toBeUndefined();
    await vi.advanceTimersByTimeAsync(250);
    expect(columnStore.getState().schemas.e[0].name).toBe('nuova');
    stop();
  });

  it.each([
    // [nome, allontanarsi dalla firma, tornarci]
    ['scrivi e cancella',
      cs => cs.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'z' }] }),
      cs => cs.getState().updateNodeData('e', { output_mapping: [] })],
    ['modifica e Annulla',
      cs => cs.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'z' }] }),
      cs => cs.getState().undo()],
  ])('risposta scartata e ritorno alla STESSA firma (%s): si richiede, niente "in calcolo" per sempre', async (_, away, back) => {
    vi.useFakeTimers();
    let release;
    const fetchSchemas = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve(response('k1')); }))
      .mockImplementation(async () => response('k1-di-nuovo'));
    const { canvasStore, columnStore, stop } = setup(fetchSchemas);
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    const k1 = columnSignature(canvasStore.getState().nodes, canvasStore.getState().edges);
    // Il canvas cambia mentre la richiesta e' in volo: la risposta si scarta...
    away(canvasStore);
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(columnStore.getState().key).toBeNull();
    // ...e il canvas torna alla firma della richiesta scartata prima del ritardo.
    back(canvasStore);
    expect(columnSignature(canvasStore.getState().nodes, canvasStore.getState().edges)).toBe(k1);
    await vi.advanceTimersByTimeAsync(250);
    const st = columnStore.getState();
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    expect(st.status).toBe('ok');
    expect(st.key).toBe(k1);
    expect(st.schemas.e[0].name).toBe('k1-di-nuovo');
    stop();
  });

  it('un errore (rete o 4xx/5xx) non svuota: restano le ultime colonne note, con l\'avviso', async () => {
    vi.useFakeTimers();
    const fetchSchemas = vi.fn()
      .mockImplementationOnce(async () => response('a'))
      .mockImplementationOnce(async () => { throw Object.assign(new Error('boom'), { response: { status: 500, data: {} } }); });
    const { canvasStore, columnStore, stop } = setup(fetchSchemas);
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    canvasStore.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column: 'z' }] });
    await vi.advanceTimersByTimeAsync(250);
    const st = columnStore.getState();
    expect(st.status).toBe('error');
    expect(st.error).toBeTruthy();
    expect(st.schemas.e.map(c => c.name)).toEqual(['a']);
    const r = columnsOf({ ...st, nodes: canvasStore.getState().nodes, edges: [], mode: 'manipola' }, 'e');
    expect(r.columns.map(c => c.name)).toEqual(['a']);
    expect(r.status).toBe('error');
    stop();
  });

  it('il canvas inviato non porta colonne derivate; la firma ignora le posizioni', () => {
    const nodes = [{ id: 't', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} } }];
    const moved = [{ ...nodes[0], position: { x: 99, y: 0 } }];
    expect(columnSignature(nodes, [])).toBe(columnSignature(moved, []));
    expect(serializeCanvas(nodes, []).nodes[0]).not.toHaveProperty('columns');
  });
});
