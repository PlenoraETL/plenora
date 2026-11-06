/**
 * canvasChange — UNICA classificazione di una modifica del canvas (UI-2,
 * 06/11/2026). Chi deve reagire al canvas (firme per /schemas e
 * /query-preview, «modifiche non salvate», copie di sicurezza, analisi del
 * flusso, pannelli) non confronta piu' serializzazioni intere a ogni
 * fotogramma: chiede che TIPO di modifica c'e' stata fra due stati.
 *
 * Livelli (dal piu' forte; ognuno implica quelli sotto nel contatore):
 *  - structure: nodi aggiunti/tolti/riordinati, id o tipo diversi, archi
 *    aggiunti/tolti o con estremi/maniglie diversi;
 *  - content:   quello che il backend legge senza le posizioni: dati del
 *    nodo e ogni campo non dichiarato qui sotto (lista di ESCLUSIONE, come
 *    TRANSIENT_DATA_KEYS: un campo nuovo conta come contenuto finche' non lo
 *    si dichiara), dati e tipo degli archi;
 *  - layout:    posizione;
 *  - selection: selezione di nodi o archi;
 *  - ui:        tutto il resto dichiarato (trascinamento, misure del DOM).
 *
 * Le tabelle sono le stesse che usano le firme (utils/canvasContentKey):
 * una modifica di sola posizione o selezione non puo' cambiare una firma
 * semantica per costruzione (test tests/shared/canvasSignature.contract).
 */

// Campi del NODO che non sono contenuto. Tutto il resto lo e'.
const NODE_LAYOUT_KEYS = Object.freeze(['position']);
const NODE_SELECTION_KEYS = Object.freeze(['selected']);
// React Flow v12: `dragging` durante il trascinamento, `measured` per le
// misure del DOM, `resizing` durante il NodeResizer (la misura scelta
// dall'utente arriva in width/height, che restano contenuto: le note la
// salvano).
const NODE_UI_KEYS = Object.freeze(['dragging', 'measured', 'resizing']);
const NODE_STRUCTURE_KEYS = Object.freeze(['id', 'type']);

// Campi dell'ARCO che contano: gli stessi della firma salvata
// (savedCanvasSignature). Tutto il resto (selezione, stile, etichetta,
// animazione) e' aspetto.
const EDGE_STRUCTURE_KEYS = Object.freeze(['id', 'source', 'sourceHandle', 'target', 'targetHandle']);
const EDGE_CONTENT_KEYS = Object.freeze(['type', 'data']);
const EDGE_SELECTION_KEYS = Object.freeze(['selected']);

/** La tabella intera, per il test di enumerazione (canvasSignature.contract). */
export const GRAPH_CHANGE_TABLE = Object.freeze({
  node: Object.freeze({ structure: NODE_STRUCTURE_KEYS, layout: NODE_LAYOUT_KEYS, selection: NODE_SELECTION_KEYS, ui: NODE_UI_KEYS }),
  edge: Object.freeze({ structure: EDGE_STRUCTURE_KEYS, content: EDGE_CONTENT_KEYS, selection: EDGE_SELECTION_KEYS }),
});

const NONE = Object.freeze({ structure: false, content: false, layout: false, selection: false });

const nodeKeyLevel = (key) => {
  if (NODE_STRUCTURE_KEYS.includes(key)) return 'structure';
  if (NODE_LAYOUT_KEYS.includes(key)) return 'layout';
  if (NODE_SELECTION_KEYS.includes(key)) return 'selection';
  if (NODE_UI_KEYS.includes(key)) return null;
  return 'content';
};

const edgeKeyLevel = (key) => {
  if (EDGE_STRUCTURE_KEYS.includes(key)) return 'structure';
  if (EDGE_CONTENT_KEYS.includes(key)) return 'content';
  if (EDGE_SELECTION_KEYS.includes(key)) return 'selection';
  return null;
};

const samePosition = (a, b) => a === b || (Boolean(a) && Boolean(b) && a.x === b.x && a.y === b.y);

function itemChange(a, b, levelOf, out) {
  if (a === b) return;
  if (!a || !b) { out.structure = true; return; }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (a[key] === b[key]) continue;
    const level = levelOf(key);
    if (!level) continue;
    // Una posizione ricreata con le stesse coordinate non e' uno spostamento.
    if (level === 'layout' && samePosition(a[key], b[key])) continue;
    out[level] = true;
  }
}

/**
 * Che cosa e' cambiato fra (prevNodes, prevEdges) e (nodes, edges)?
 * { structure, content, layout, selection } (booleani). Costo: un confronto
 * per identita' per elemento; i campi si guardano solo per gli elementi
 * ricreati (in un trascinamento: il nodo spostato).
 */
export function graphChange(prevNodes, nodes, prevEdges, edges) {
  if (prevNodes === nodes && prevEdges === edges) return NONE;
  const out = { structure: false, content: false, layout: false, selection: false };
  if (prevNodes !== nodes) {
    const a = prevNodes || [];
    const b = nodes || [];
    if (a.length !== b.length) out.structure = true;
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i += 1) itemChange(a[i], b[i], nodeKeyLevel, out);
  }
  if (prevEdges !== edges) {
    const a = prevEdges || [];
    const b = edges || [];
    if (a.length !== b.length) out.structure = true;
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i += 1) itemChange(a[i], b[i], edgeKeyLevel, out);
  }
  return out;
}

/** Il contenuto (o la struttura) e' cambiato? */
export const contentChanged = (change) => change.structure || change.content;

