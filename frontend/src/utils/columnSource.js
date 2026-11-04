/**
 * columnSource — UNA sola fonte di colonne nel frontend (Blocco A, regola 4).
 *
 * La fonte e' la mappa degli schemi calcolata dal backend sul canvas
 * dell'editor (POST /projects/{id}/schemas, vedi store/useColumnStore e
 * hooks/useColumns): per ogni nodo le colonne in uscita
 * {id, name, type, source_name, source_node, is_calculated, optional} e lo
 * stato per aspetto (names_status, types_status, order_status, reason).
 *
 * Qui c'e' l'unico selettore, `columnsOf(snapshot, nodeId, { side, source })`:
 *  - senza opzioni: le colonne in USCITA del nodo;
 *  - `side: 'input'`: le colonne in INGRESSO, tutti i lati nell'ordine delle
 *    maniglie (input-0, input-1, ...; senza maniglia la posizione verticale,
 *    come incoming_edge_sort_key del backend);
 *  - `side: 'left' | 'right' | <indice>`: un solo lato;
 *  - `source: <nodeId>`: l'ingresso che arriva da quel nodo.
 * L'Output di Estrai riceve in ingresso tutte le tabelle e i layer a monte
 * (la FROM della query), non il solo nodo collegato.
 *
 * Nessun calcolo delle colonne a valle: se il server non ha (ancora) uno
 * schema per un nodo, l'unico ripiego DICHIARATO sono le colonne che le
 * sorgenti dichiarano nella loro configurazione (visibili, con l'alias),
 * marcate come incerte. Un errore di rete non svuota nulla: restano le
 * ultime colonne note (vedi useColumnStore) con l'avviso.
 */
import { canonicalNodeType, DERIVED_COLUMNS_TYPES } from './nodeTypeRegistry';
import { dataFlowEdges } from './runtimeBindingEdges';
import { canvasContentKey } from './canvasContentKey';

// Tipi di nodo con colonne DERIVATE (calcolate dal server): non vivono nel
// nodo (nodeTypeRegistry.DERIVED_COLUMNS_TYPES). Le sorgenti invece tengono
// la loro configurazione (visibile, alias, colonne dichiarate dei file).
export { DERIVED_COLUMNS_TYPES };

// Ruolo di ogni tipo di nodo (canonico) rispetto alle colonne. Un tipo nuovo
// senza voce fa fallire il test di enumerazione (columnSource.contract).
//  - declared: sorgente, colonne dalla propria configurazione;
//  - derived: colonne calcolate dal server a partire dagli ingressi;
//  - sink: nessuna colonna in uscita (Carica), legge solo l'ingresso;
//  - none: nessuna colonna (note).
export const COLUMN_ROLES = Object.freeze({
  source: 'declared',
  feature_source: 'declared',
  project_source: 'declared',
  file_source: 'declared',
  datasource_node: 'declared',
  staticValues: 'declared',
  project_input: 'declared',
  transform: 'derived',
  multi_join: 'derived',
  api_enrich: 'derived',
  result: 'derived',
  carica: 'sink',
  carica_arcgis: 'sink',
  comment: 'none',
});

// Capacita' del backend per tipo di progetto. `outputResolvesById`: le
// "Colonne in uscita" dell'Output (output_columns) si risolvono per id
// (name_column_id) sia nell'inferenza sia nell'esecuzione. Dove vale false
// il backend risolve SOLO per nome: l'Output scrive e legge per nome (una
// colonna rinominata a monte non e' piu' la stessa voce, come nel file).
// Arricchisci: per id dalla A3 (P6, resolve_output_column_refs nel backend).
// Un tipo di progetto senza voce fa fallire il test di enumerazione.
export const PROJECT_COLUMN_CAPABILITIES = Object.freeze({
  estrai: Object.freeze({ outputResolvesById: true }),
  manipola: Object.freeze({ outputResolvesById: true }),
  arricchisci: Object.freeze({ outputResolvesById: true }),
});

export const outputResolvesById = mode => PROJECT_COLUMN_CAPABILITIES[mode]?.outputResolvesById === true;

/** output_columns come le legge il backend di quel tipo di progetto (senza id dove risolve per nome). */
export function outputColumnsAsRead(config, mode) {
  if (!Array.isArray(config) || outputResolvesById(mode)) return config;
  return config.map((item) => {
    if (!item || typeof item !== 'object' || !('name_column_id' in item)) return item;
    // eslint-disable-next-line no-unused-vars
    const { name_column_id, ...rest } = item;
    return rest;
  });
}

export const CERTAIN_STATE = Object.freeze({
  names_status: 'certain', types_status: 'certain', order_status: 'certain', reason: '',
});
const DECLARED_STATE = Object.freeze({
  names_status: 'indeterminate', types_status: 'indeterminate', order_status: 'certain',
  reason: 'Colonne dichiarate dalla sorgente, non ancora confermate dal server',
});
const PENDING_STATE = Object.freeze({
  names_status: 'indeterminate', types_status: 'indeterminate', order_status: 'indeterminate',
  reason: 'Colonne non ancora calcolate dal server',
});

const EMPTY = Object.freeze([]);
const SIDE_INDEX = { left: 0, right: 1 };

/** Forma unica di una colonna: tutti i lettori ricevono questi campi. */
export function normalizeColumn(col, sourceNode = null) {
  if (typeof col === 'string') {
    return { id: null, name: col, type: 'str', source_name: col, source_node: sourceNode, is_calculated: false, optional: false };
  }
  const name = col?.name == null ? '' : String(col.name);
  return {
    id: col?.id || col?.column_id || null,
    name,
    type: col?.type || 'str',
    source_name: col?.source_name == null ? name : String(col.source_name),
    source_node: col?.source_node ?? sourceNode,
    is_calculated: Boolean(col?.is_calculated),
    optional: Boolean(col?.optional),
  };
}

/** Stato normalizzato di un nodo (certain se il server non ne manda). */
export function normalizeState(state) {
  if (!state || typeof state !== 'object') return CERTAIN_STATE;
  return {
    names_status: state.names_status === 'indeterminate' ? 'indeterminate' : 'certain',
    types_status: state.types_status === 'indeterminate' ? 'indeterminate' : 'certain',
    order_status: state.order_status === 'indeterminate' ? 'indeterminate' : 'certain',
    reason: state.reason ? String(state.reason) : '',
  };
}

/** Risposta di /schemas -> mappa normalizzata {schemas, states}. */
export function normalizeSchemasResponse(data) {
  const schemas = {};
  const states = {};
  for (const [nodeId, cols] of Object.entries(data?.schemas || {})) {
    schemas[nodeId] = (Array.isArray(cols) ? cols : []).map(c => normalizeColumn(c, nodeId));
  }
  for (const nodeId of Object.keys(schemas)) {
    states[nodeId] = normalizeState(data?.schema_states?.[nodeId]);
  }
  return { schemas, states };
}

// ── Ingressi di un nodo ───────────────────────────────────────────────────
function targetHandle(edge) {
  if (edge.targetHandle) return edge.targetHandle;
  return edge.id?.match(/(input-\d+)$/)?.[1] || null;
}

// Stesso ordinamento del backend (transform_ordering.incoming_edge_sort_key):
// input-N numerico, poi le altre maniglie, poi la posizione verticale.
function inputOrderKey(edge, byId) {
  const handle = targetHandle(edge);
  const match = handle?.match(/^input-(\d+)$/);
  if (match) return [0, Number(match[1]), ''];
  if (handle) return [0, Number.MAX_SAFE_INTEGER, handle];
  const source = byId.get(edge.source);
  if (source) return [1, Number(source.position?.y || 0), edge.source];
  return [2, 0, edge.source || ''];
}

/** Collegamenti di dati in ingresso, nell'ordine dei lati del backend. */
export function sortedInputEdges(nodeId, nodes, edges) {
  const byId = new Map((nodes || []).map(n => [n.id, n]));
  return dataFlowEdges(edges).filter(e => e.target === nodeId).sort((left, right) => {
    const a = inputOrderKey(left, byId);
    const b = inputOrderKey(right, byId);
    return a[0] - b[0] || a[1] - b[1] || String(a[2]).localeCompare(String(b[2]));
  });
}

const ESTRAI_FROM_TYPES = ['source', 'feature_source'];

/** Tabelle e layer a monte di un nodo di Estrai, in ordine di visita. */
export function upstreamSourceNodes(nodes, edges, nodeId) {
  const flow = dataFlowEdges(edges);
  const byId = new Map((nodes || []).map(n => [n.id, n]));
  const seen = new Set();
  const out = [];
  const visit = (id) => {
    if (seen.has(id)) return;
    seen.add(id);
    const node = byId.get(id);
    if (ESTRAI_FROM_TYPES.includes(canonicalNodeType(node?.type))) out.push(node);
    flow.filter(e => e.target === id).forEach(e => visit(e.source));
  };
  visit(nodeId);
  return out;
}

/** Nodi in ingresso: [{ nodeId, index }] secondo la semantica dell'editor. */
function inputNodes(snapshot, node) {
  if (snapshot.mode === 'estrai' && canonicalNodeType(node.type) === 'result') {
    return upstreamSourceNodes(snapshot.nodes, snapshot.edges, node.id).map((n, index) => ({ nodeId: n.id, index }));
  }
  return sortedInputEdges(node.id, snapshot.nodes, snapshot.edges).map((e, index) => ({ nodeId: e.source, index }));
}

// ── Ripiego dichiarato: le sorgenti, dalla loro configurazione ────────────
function declaredColumns(node) {
  const type = canonicalNodeType(node?.type);
  if (COLUMN_ROLES[type] !== 'declared') return null;
  const data = node.data || {};
  if (type === 'staticValues') {
    return (data.fields || []).filter(f => f?.name).map(f => normalizeColumn({ name: f.name, type: f.type || 'str' }, node.id));
  }
  if (type === 'project_input') {
    return (data.expected_columns || []).map(c => normalizeColumn(c, node.id)).filter(c => c.name);
  }
  return (data.columns || [])
    .filter(c => typeof c === 'string' || c?.visible !== false)
    .map(c => (typeof c === 'string'
      ? normalizeColumn(c, node.id)
      : normalizeColumn({ ...c, name: c.alias || c.name, source_name: c.name }, node.id)))
    .filter(c => c.name);
}

function worst(states) {
  if (states.length === 0) return CERTAIN_STATE;
  const pick = key => (states.some(s => s[key] === 'indeterminate') ? 'indeterminate' : 'certain');
  return {
    names_status: pick('names_status'),
    types_status: pick('types_status'),
    order_status: pick('order_status'),
    reason: [...new Set(states.map(s => s.reason).filter(Boolean))].join(' · '),
  };
}

function outputOf(snapshot, nodeId) {
  const schema = snapshot.schemas?.[nodeId];
  if (schema) return { columns: schema, state: snapshot.states?.[nodeId] || CERTAIN_STATE, known: true };
  const node = (snapshot.nodes || []).find(n => n.id === nodeId);
  const declared = declaredColumns(node);
  if (declared) return { columns: declared, state: DECLARED_STATE, known: false };
  return { columns: EMPTY, state: PENDING_STATE, known: false };
}

/**
 * Il selettore unico. `snapshot` = { schemas, states, status, error, nodes,
 * edges, mode }. Restituisce sempre { columns, state, status, error, known,
 * inputs? }: colonne nella forma di normalizeColumn (piu' `input_node` e
 * `input_index` per le colonne in ingresso).
 */
export function columnsOf(snapshot, nodeId, { side = null, source = null } = {}) {
  const base = { status: snapshot.status || 'idle', error: snapshot.error || null };
  if (side == null && source == null) return { ...base, ...outputOf(snapshot, nodeId) };
  const node = (snapshot.nodes || []).find(n => n.id === nodeId);
  if (!node) return { ...base, columns: EMPTY, state: PENDING_STATE, known: false, inputs: EMPTY };
  let picked = inputNodes(snapshot, node);
  if (source != null) picked = picked.filter(p => p.nodeId === source);
  else if (side !== 'input') {
    const index = typeof side === 'number' ? side : SIDE_INDEX[side] ?? Number(String(side).match(/^input-(\d+)$/)?.[1]);
    picked = picked.filter(p => p.index === index);
  }
  const inputs = picked.map(p => ({ ...p, ...outputOf(snapshot, p.nodeId) }));
  const columns = inputs.flatMap(p => p.columns.map(c => ({ ...c, input_node: p.nodeId, input_index: p.index })));
  return {
    ...base,
    columns,
    state: worst(inputs.map(p => p.state)),
    known: inputs.every(p => p.known),
    inputs,
  };
}

/**
 * Regola unica per le correzioni automatiche derivate dalle colonne
 * (riscrittura dei riferimenti, id mancanti, scelta della geometria, ritorno
 * a CSV): partono solo su colonne CONFERMATE, cioe' calcolate dal server per
 * la firma corrente (status 'ok'), note per ogni ingresso, con almeno un
 * ingresso collegato quando si leggono ingressi. Prima della risposta, durante
 * il calcolo o dopo un errore le colonne mancano solo per ora: niente si
 * azzera.
 */
export function columnsConfirmed(result) {
  if (!result || !result.known || result.status !== 'ok') return false;
  return !Array.isArray(result.inputs) || result.inputs.length > 0;
}

// Uguaglianza strutturale dei risultati di columnsOf (valori semplici,
// array e oggetti piani).
function sameValue(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => sameValue(v, b[i]));
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every(k => Object.prototype.hasOwnProperty.call(b, k) && sameValue(a[k], b[k]));
}

/**
 * Selettore memorizzato PER NODO (un'istanza per lettore, hooks/useColumns):
 * `memo(columns, canvas, nodeId, opts)` con columns = {schemas, states,
 * status, error} e canvas = {nodes, edges, mode}. Restituisce lo STESSO
 * oggetto finche' il risultato non cambia: con gli stessi ingressi senza
 * ricalcolo, altrimenti ricalcolando e confrontando la struttura (le
 * posizioni dei nodi non entrano nel risultato, quindi un trascinamento non
 * produce un risultato nuovo).
 */
export function createColumnsMemo() {
  let last = null;
  return (columns, canvas, nodeId, { side = null, source = null } = {}) => {
    if (last && last.columns === columns && last.nodes === canvas.nodes && last.edges === canvas.edges
      && last.mode === canvas.mode && last.nodeId === nodeId && last.side === side && last.source === source) {
      return last.result;
    }
    const next = columnsOf({ ...columns, nodes: canvas.nodes, edges: canvas.edges, mode: canvas.mode }, nodeId, { side, source });
    const result = last && sameValue(last.result, next) ? last.result : next;
    last = { columns, nodes: canvas.nodes, edges: canvas.edges, mode: canvas.mode, nodeId, side, source, result };
    return result;
  };
}

/**
 * Nodo da cui una colonna (per id) entra nel flusso, risalendo da `nodeId`:
 * il primo a monte che la produce senza riceverla. Per le etichette "da dove
 * viene" delle anteprime (Estrai: la tabella; Arricchisci: sorgente o API).
 */
export function columnOrigin(snapshot, nodeId, columnId) {
  if (!columnId) return null;
  const seen = new Set();
  const visit = (id) => {
    if (seen.has(id)) return null;
    seen.add(id);
    const out = outputOf(snapshot, id).columns;
    if (!out.some(c => c.id === columnId)) return null;
    const { inputs } = columnsOf(snapshot, id, { side: 'input' });
    for (const input of inputs) {
      const found = visit(input.nodeId);
      if (found) return found;
    }
    return id;
  };
  return visit(nodeId);
}

/** Colonne aggiunte da un nodo: in uscita, ma non nel suo ingresso (per id). */
export function addedColumns(snapshot, nodeId) {
  const inputIds = new Set(columnsOf(snapshot, nodeId, { side: 'input' }).columns.map(c => c.id).filter(Boolean));
  return outputOf(snapshot, nodeId).columns.filter(c => !c.id || !inputIds.has(c.id));
}

/**
 * Firma di cio' che decide le colonne: la serializzazione che il backend
 * legge (senza posizioni ne' note, canvasContentKey) piu' la posizione
 * verticale delle sorgenti dei collegamenti senza maniglia, che ne decide
 * l'ordine dei lati.
 */
export function columnSignature(nodes, edges) {
  const byId = new Map((nodes || []).map(n => [n.id, n]));
  const order = dataFlowEdges(edges).filter(e => !targetHandle(e))
    .map(e => [e.source, byId.get(e.source)?.position?.y ?? null]);
  return `${canvasContentKey(nodes, edges)}|${JSON.stringify(order)}`;
}

/** Aspetti incerti di uno stato, in parole: per gli avvisi sobri. */
export function uncertainAspects(state) {
  const s = state || CERTAIN_STATE;
  const out = [];
  if (s.names_status === 'indeterminate') out.push('nomi incerti');
  if (s.types_status === 'indeterminate') out.push('tipo incerto');
  if (s.order_status === 'indeterminate' && s.names_status !== 'indeterminate') out.push('ordine incerto');
  return out;
}

/** Nome della tabella o del layer di Estrai, come lo scrive la query. */
export function estraiSourceLabel(node) {
  return canonicalNodeType(node?.type) === 'feature_source'
    ? (node.data?.layer_name || 'feature')
    : (node?.data?.alias || node?.data?.table_name);
}
