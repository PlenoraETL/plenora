/**
 * editorNodeCatalog — catalogo UNICO dei nodi che si aggiungono negli editor
 * (UI-3, 06/11/2026: P30, U11, U12, E3, U9).
 *
 * Prima ogni editor aveva i suoi `handleAdd*Node`: menu diversi (Manipola
 * senza «Carica su ArcGIS», Arricchisci senza «Nota»), posizioni fisse fuori
 * dalla vista (Arricchisci: Valori statici a 100,200, Output a 600,200),
 * voci spente senza il motivo (E3) e sorgenti senza nome (U9). Ora:
 *
 *  - NODES dichiara per ogni voce in quali editor esiste, i vincoli (una
 *    sola per progetto, richiede l'Output) e come nasce (tipo e dati);
 *  - availability() e' l'UNICA regola dei vincoli: lo stesso motivo spegne
 *    la voce del menu (scritto accanto, non solo nel tooltip), avvisa dalla
 *    palette e dalla scorciatoia;
 *  - buildCatalogNode() costruisce nodo e collegamento: il nodo nasce
 *    SEMPRE nella vista visibile (posizione data dall'editor = centro della
 *    vista; lo store lo sposta al primo posto libero);
 *  - catalogMenuItems() e catalogPaletteItems() generano menu "⋯" e palette
 *    dalla stessa tabella: stessi strumenti dove hanno senso.
 *  - sourceNodeData() e' l'unico costruttore dei nodi sorgente (progetto,
 *    file): il nome si salva insieme all'id, cosi' il titolo non e' l'id.
 *
 * Il test tests/shared/editorNodeCatalog.contract.test.js vieta posizioni
 * fisse e `handleAdd*Node` negli editor e controlla che ogni voce spenta
 * abbia il suo motivo.
 */
import { MarkerType } from '@xyflow/react';
import { addFirstMessage, flowEdgeLabel, menuEntry, onlyOneMessage, paletteEntry, termName } from '../../utils/glossary';
import { newProjectInputData } from '../../utils/uniqueName';
import { renderNodeType } from '../../utils/nodeTypeRegistry';
import { newJoinType } from '../../utils/joinTypes';

export const EDITOR_MODES = ['estrai', 'manipola', 'arricchisci'];
const ALL = EDITOR_MODES;

const OUTPUT_TYPES = new Set(['result', 'resultNode']);
const isOutput = (n) => OUTPUT_TYPES.has(n.type);
const isArcgisNodeType = (n) => n.type === 'carica_arcgis' || n.type === 'caricaArcgis';

/**
 * Destinazione di un nodo di caricamento: 'db' | 'arcgis' | 'rest' |
 * 'datasource' | null (non e' un caricamento). STESSA regola del backend
 * (core/carica/dispatch.select_carica_write_branch): in Manipola il nodo
 * «Carica su database» con `dest_type: 'arcgis'` scrive su ArcGIS, quindi
 * conta come «Carica su ArcGIS» (B-K11) e non come caricamento su database.
 */
export function loadDestination(n) {
  if (isArcgisNodeType(n)) return 'arcgis';
  if (n.type !== 'carica') return null;
  const dest = n.data?.dest_type || 'db';
  return dest === 'arcgis' || dest === 'rest' ? dest : 'db';
}
const isArcgisLoad = (n) => loadDestination(n) === 'arcgis';

/**
 * Nodi unici per progetto (Output e caricamenti di ogni destinazione): non
 * si copiano ne' si duplicano (canvasCommands). Un caricamento REST o su
 * ArcGIS fatto col nodo «Carica su database» resta un caricamento.
 */
export const isSingleUseNode = (n) => isOutput(n) || loadDestination(n) !== null;

const outputData = (mode) => {
  const base = {
    fileName: 'output', format: 'csv',
    order_by_enabled: false, order_by_columns: [],
    having_enabled: false, having_conditions: [],
  };
  if (mode === 'estrai') {
    return { ...base, distinct: false, limit: null, group_by_enabled: false, group_by_columns: [], aggregations: [] };
  }
  if (mode === 'manipola') {
    return { ...base, isManipulate: true, group_by_enabled: false, group_by_columns: [], aggregations: [] };
  }
  return { ...base, isArricchisci: true };
};

const loadDbData = (mode) => ({
  connection_id: '', schema_name: '', table_name: '', write_mode: 'create',
  column_mappings: [], match_columns: [], batch_size: 1000,
  transaction_mode: 'all_or_nothing', create_spatial_index: true, conflict_policy: 'abort',
  // Manipola: lo stesso nodo scrive anche su ArcGIS (dest_type).
  ...(mode === 'manipola' ? {
    dest_type: 'db',
    arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '', arcgis_operation: 'add',
    arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
  } : {}),
});

/**
 * Collegamento Output -> caricamento nello stile dell'editor. In Estrai gli
 * archi portano `data.join_type` (createEdgeFromData) e l'etichetta.
 */
function loadEdge(mode, kind, sourceId, targetId, stamp) {
  const color = kind === 'carica_arcgis' ? '#2DD4BF' : '#7c3aed';
  const id = `edge_result_${kind}_${stamp}`;
  if (mode === 'estrai' || kind === 'carica_arcgis') {
    return { id, source: sourceId, target: targetId, label: flowEdgeLabel(kind), animated: true,
      style: { stroke: color, strokeWidth: 2 }, data: { join_type: kind } };
  }
  return { id, source: sourceId, target: targetId, type: 'smoothstep', animated: true,
    style: { stroke: color, strokeWidth: 2 },
    markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color }, data: {} };
}

/*
 * Voci del catalogo. `editors`: dove la voce ha senso. `single`: una sola
 * per progetto (`present` riconosce i nodi gia' presenti). `idPrefix`: il
 * prefisso storico degli id dei nodi di quel tipo. `requires`:
 * serve prima un nodo (oggi solo l'Output). `build(ctx)` -> { type, data,
 * extra? } con ctx = { mode, nodes }.
 */
export const NODES = Object.freeze({
  runtime_input: {
    idPrefix: 'project_input',
    editors: ALL,
    build: ({ mode, nodes }) => ({
      // Chiave e ordine unici e stabili (utils/uniqueName, R8.19).
      type: 'project_input', data: newProjectInputData(nodes, { kind: mode === 'estrai' ? 'text' : 'file' }),
    }),
  },
  static_values: {
    idPrefix: 'static',
    // Estrai: i valori statici non sono una sorgente SQL.
    editors: ['manipola', 'arricchisci'],
    build: () => ({ type: 'staticValues', data: { label: termName('static_values'), fields: [] } }),
  },
  note: {
    idPrefix: 'comment',
    editors: ALL,
    build: () => ({ type: 'comment', extra: { zIndex: -1, style: { width: 250, height: 150 } },
      data: { level: '', label: '', title: '', color: '#fbbf24' } }),
  },
  output: {
    idPrefix: 'result',
    // `chain`: dalla palette dopo un nodo entra nel flusso (insertAfter).
    chain: true,
    editors: ALL, single: true, present: isOutput,
    build: ({ mode }) => ({ type: mode === 'estrai' ? 'resultNode' : 'result', data: outputData(mode) }),
  },
  multi_join: {
    idPrefix: 'multi_join',
    chain: true,
    editors: ['manipola'],
    // U21: tipo del Join nuovo dal catalogo unico (utils/joinTypes).
    build: () => ({ type: 'multi_join', data: { config: { join_keys: [], join_type: newJoinType('multi_join') } } }),
  },
  datasource: {
    idPrefix: 'datasource',
    // Arricchisci sceglie il Data Source da un elenco (finestra dell'editor).
    editors: ['manipola'],
    build: () => ({
      type: renderNodeType('datasource_node'),
      data: { mode: 'source', datasource_id: '', datasource_name: '', file_mode: 'fixed',
        file_path: '', folder_path: '', dynamic_rule: 'latest', pattern: '',
        dest_folder: '', dest_filename: '', dest_format: 'csv', columns: [] },
    }),
  },
  load_db: {
    idPrefix: 'carica',
    editors: ALL, single: true, requires: 'output', present: (n) => loadDestination(n) === 'db', edgeKind: 'carica',
    build: ({ mode }) => ({ type: 'carica', data: loadDbData(mode) }),
  },
  load_arcgis: {
    idPrefix: 'carica_arcgis',
    editors: ALL, single: true, requires: 'output', present: isArcgisLoad, edgeKind: 'carica_arcgis',
    build: () => ({
      type: renderNodeType('carica_arcgis'),
      data: { arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '',
        arcgis_operation: 'add', arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
        column_mappings: [] },
    }),
  },
});

/** Voci del catalogo disponibili in un editor, nell'ordine della tabella. */
export const catalogKeys = (mode) => Object.keys(NODES).filter(k => NODES[k].editors.includes(mode));

/**
 * UNICA regola dei vincoli: { ok: true } oppure { ok: false, reason }.
 * Il motivo e' quello mostrato accanto alla voce spenta e nell'avviso.
 */
export function availability(key, nodes = []) {
  const entry = NODES[key];
  if (!entry) return { ok: false, reason: 'Voce non disponibile in questo editor' };
  if (entry.requires === 'output' && !nodes.some(isOutput)) return { ok: false, reason: addFirstMessage('output') };
  if (entry.single && nodes.some(entry.present)) return { ok: false, reason: onlyOneMessage(key) };
  return { ok: true, reason: null };
}

/**
 * Nodo (e collegamento) di una voce, alla posizione data (centro della vista).
 * @returns {{ node, edges: object[] } | { error: string }}
 */
export function buildCatalogNode(key, { mode, nodes = [], position, stamp = Date.now() }) {
  const entry = NODES[key];
  if (!entry || !entry.editors.includes(mode)) return { error: 'Voce non disponibile in questo editor' };
  const check = availability(key, nodes);
  if (!check.ok) return { error: check.reason };
  const { type, data, extra = {} } = entry.build({ mode, nodes });
  const node = { id: `${entry.idPrefix}_${stamp}`, type, position, data, ...extra };
  const edges = [];
  if (entry.edgeKind) {
    const output = nodes.find(isOutput);
    edges.push(loadEdge(mode, entry.edgeKind, output.id, node.id, stamp));
  }
  return { node, edges };
}

/**
 * Voci del menu "⋯" di un editor dal catalogo: nome, icona e descrizione dal
 * glossario; una voce spenta porta il motivo (`reason`), che il menu scrive
 * sotto il nome (E3: niente voci sbiadite senza spiegazione).
 */
export function catalogMenuItems(mode, nodes, onAdd) {
  const out = [];
  let section = null;
  for (const key of catalogKeys(mode)) {
    // Strumenti (input, valori, nota) e flusso (Output, caricamenti): un
    // separatore tra i due gruppi, uguale nei tre editor.
    const next = FLOW_KEYS.has(key) ? 'flow' : 'tools';
    if (section && next !== section) out.push({ key: `div-${next}`, divider: true });
    section = next;
    const check = availability(key, nodes);
    out.push(menuEntry(key, { onClick: () => onAdd(key), disabled: !check.ok, reason: check.reason }));
  }
  return out;
}

const FLOW_KEYS = new Set(['output', 'multi_join', 'datasource', 'load_db', 'load_arcgis']);

/*
 * Selettori per hooks/useCanvasDerived (UI-2: l'editor non si iscrive al
 * grafo): i soli nodi che decidono i vincoli del catalogo (Output e
 * caricamenti) e il canvas vuoto.
 */
const CONSTRAINT_ENTRIES = Object.values(NODES).filter(e => e.present);
export const catalogConstraintNodes = (s) => (s.nodes || []).filter(n => CONSTRAINT_ENTRIES.some(e => e.present(n)));
export const isCanvasEmpty = (s) => (s.nodes || []).length === 0;

/** Voci della palette dal catalogo (gruppo «Uscite» e «Strumenti»). */
export function catalogPaletteItems(mode, keys) {
  return keys.filter(k => NODES[k]?.editors.includes(mode)).map(k => paletteEntry(k, { id: k, kind: 'catalog', catalogKey: k }));
}

// ── Sorgenti (U9, U11) ──────────────────────────────────────────────────────

/**
 * Dati di un nodo sorgente: l'id E il nome, cosi' il titolo del nodo non e'
 * mai l'id. Unico costruttore per trascinamento, clic, Invio e palette.
 * `item`: un progetto ({id, name, type}) o un file ({id, name, format}).
 */
export function sourceNodeData(kind, item) {
  if (kind === 'project_source') {
    return { source_project_id: item.source_project_id || item.id, project_name: item.name || item.project_name || '', columns: [] };
  }
  if (kind === 'file_source') {
    return { file_source_id: item.file_source_id || item.id, file_name: item.name || item.file_name || '', columns: [] };
  }
  throw new Error(`[editorNodeCatalog] sorgente sconosciuta: ${kind}`);
}

/** Nodo sorgente pronto per lo store, alla posizione data. */
export const buildSourceNode = (kind, item, position, stamp = Date.now()) => ({
  id: `${kind === 'project_source' ? 'source' : 'file'}_${stamp}`, type: kind, position, data: sourceNodeData(kind, item),
});

/*
 * Trascinamento di una sorgente dalla barra laterale: un solo formato per
 * Manipola e Arricchisci (prima 'itemData' in uno, 'nodeData' nell'altro).
 */
const SOURCE_DRAG_TYPE = 'application/x-plenora-source';

export function startSourceDrag(event, kind, item) {
  event.dataTransfer.setData(SOURCE_DRAG_TYPE, JSON.stringify({ kind, item: { id: item.id, name: item.name } }));
  event.dataTransfer.effectAllowed = 'move';
}

/** { kind, item } trascinato, o null se il trascinamento non e' una sorgente. */
export function readSourceDrop(event) {
  const raw = event.dataTransfer?.getData(SOURCE_DRAG_TYPE);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed?.kind && parsed?.item ? parsed : null;
  } catch {
    return null;
  }
}

/** Voci della palette per le sorgenti (progetti e file). */
export function sourcePaletteGroups({ projects = [], files = [] }) {
  const groups = [];
  if (projects.length) {
    groups.push({ label: 'Sorgenti: progetti', items: projects.map(p => ({
      ...paletteEntry('project_source', { id: `project:${p.id}`, kind: 'source', sourceKind: 'project_source' }),
      label: p.name || String(p.id), source: p, desc: p.project_type ? `Progetto ${String(p.project_type)}` : termName('project_source'),
    })), limit: 40 });
  }
  if (files.length) {
    groups.push({ label: 'Sorgenti: file', items: files.map(f => ({
      ...paletteEntry('file_source', { id: `file:${f.id}`, kind: 'source', sourceKind: 'file_source' }),
      label: f.name || String(f.id), source: f, desc: f.format ? String(f.format).toUpperCase() : termName('file_source'),
    })), limit: 40 });
  }
  return groups;
}

/**
 * Nome attuale di un nodo sorgente (U9, B-K9; regola in utils/sourceNames):
 * risolto dal catalogo delle sorgenti nel campo TRANSITORIO `_sourceName`,
 * cosi' il canvas non si riscrive e non risulta modificato.
 *  - Ogni nodo sorgente si risolve contro l'elenco: trovato -> il nome del
 *    catalogo; fuori dall'elenco -> `null` (risolto, nome sconosciuto: il
 *    titolo usa la copia salvata). Un nodo risolto non riaccende la
 *    risoluzione (`isUnresolvedSource`); un elenco nuovo ri-risolve tutti.
 *  - Con i nomi gia' giusti restituisce lo stesso array (nessun aggiornamento).
 *  - Al salvataggio la copia salvata (`project_name`/`file_name`) segue il
 *    nome attuale (utils/canvasSerializer).
 */
export function withResolvedSourceNames(nodes, { projects = [], files = [] }) {
  // Nessun elenco arrivato: niente contro cui risolvere.
  if (!projects.length && !files.length) return nodes;
  const projectNames = new Map(projects.map(p => [String(p.id), p.name]));
  const fileNames = new Map(files.map(f => [String(f.id), f.name]));
  let changed = false;
  const next = nodes.map((n) => {
    let name;
    if (n.type === 'project_source' && n.data?.source_project_id != null) name = projectNames.get(String(n.data.source_project_id));
    else if (n.type === 'file_source' && n.data?.file_source_id != null) name = fileNames.get(String(n.data.file_source_id));
    else return n;
    const resolved = name || null;
    if (n.data._sourceName === resolved) return n;
    changed = true;
    return { ...n, data: { ...n.data, _sourceName: resolved } };
  });
  return changed ? next : nodes;
}

/** Nodo sorgente non ancora risolto contro l'elenco delle sorgenti. */
export const isUnresolvedSource = (n) => (n.type === 'project_source' || n.type === 'file_source')
  && n.data?._sourceName === undefined;
