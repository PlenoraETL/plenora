/**
 * stepTitles — gli id dei nodi non arrivano mai all'utente (collaudo live L4).
 *
 * Il backend nomina i passi col loro id («transform_1730…») nei messaggi di
 * Valida e negli errori delle esecuzioni. Regola unica: un messaggio del
 * backend che riguarda il canvas aperto passa da qui prima di essere
 * mostrato, e ogni id di un nodo di quel canvas diventa «titolo del passo»
 * (lo stesso titolo del canvas, nodeDescriptors.nodeTitle). «Vai al passo»
 * usa il node_id strutturato dell'esito, mai il testo.
 */
import { nodeTitle } from '../components/canvas/nodeDescriptors';

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Forma degli id dei nodi (residui collaudo live M7): prefisso del tipo +
 * `_` + numero o uuid (`transform_1730…`, `node_1784…`, `file_1730…_2`,
 * copie `transform_1730…_0`). Solo un id con questa forma si sostituisce, e
 * solo come parola intera: un id breve o una parola («result», «a») non
 * tocca il testo del backend.
 */
export const NODE_ID_SHAPE = /^[A-Za-z]+(?:_[A-Za-z]+)*_(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:_[A-Za-z0-9]+)*$/;

// I nodi con un id citabile, i piu' lunghi prima (un id che ne contiene un altro).
const citable = (nodes) => (nodes || [])
  .filter(n => n?.id && NODE_ID_SHAPE.test(String(n.id)))
  .sort((a, b) => String(b.id).length - String(a.id).length);
// L'id da solo o tra apici; mai dentro un'altra parola.
const citation = (id, flags = '') => new RegExp(`['"«“]?(?<![\\w-])${escapeRe(id)}(?![\\w-])['"»”]?`, flags);

/** `text` con gli id dei nodi di `nodes` sostituiti dal titolo del passo. */
export function withStepTitles(text, nodes = []) {
  let out = String(text ?? '');
  if (!out) return out;
  for (const n of citable(nodes)) {
    out = out.replace(citation(String(n.id), 'g'), `«${nodeTitle(n.type, n.data)}»`);
  }
  return out;
}

/** Il primo nodo di `nodes` citato per id in `text` (stessa regola), o null. */
export function citedStep(text, nodes = []) {
  const s = String(text ?? '');
  return s ? citable(nodes).find(n => citation(String(n.id)).test(s)) || null : null;
}

/** Il titolo del passo con quell'id, o null. */
export function stepTitleOf(nodeId, nodes = []) {
  const n = nodeId ? (nodes || []).find(x => x?.id === nodeId) : null;
  return n ? nodeTitle(n.type, n.data) : null;
}
