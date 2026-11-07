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

/** `text` con gli id dei nodi di `nodes` sostituiti dal titolo del passo. */
export function withStepTitles(text, nodes = []) {
  let out = String(text ?? '');
  if (!out) return out;
  const ids = (nodes || [])
    .filter(n => n?.id && String(n.id).length >= 3)
    .sort((a, b) => String(b.id).length - String(a.id).length);
  for (const n of ids) {
    const id = String(n.id);
    if (!out.includes(id)) continue;
    // L'id da solo o tra apici; mai dentro un'altra parola.
    const re = new RegExp(`['"«“]?(?<![\\w-])${escapeRe(id)}(?![\\w-])['"»”]?`, 'g');
    out = out.replace(re, `«${nodeTitle(n.type, n.data)}»`);
  }
  return out;
}

/** Il titolo del passo con quell'id, o null. */
export function stepTitleOf(nodeId, nodes = []) {
  const n = nodeId ? (nodes || []).find(x => x?.id === nodeId) : null;
  return n ? nodeTitle(n.type, n.data) : null;
}
