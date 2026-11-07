/**
 * canvasDeletion — regola UNICA delle cancellazioni dal canvas (Fase 5, T4;
 * R1.7, R1.11a; correzione dopo la conferma di T2+T4, punto 1).
 *
 * Regola: un solo punto decide se una cancellazione e' possibile
 * (`canDeleteFromCanvas`: il canvas NON e' in sola lettura) e un solo punto
 * la esegue (`requestCanvasDeletion`). Ogni gesto passa di qui:
 * Canc/Backspace (hooks/useCanvasKeyboard), tasto destro su un arco
 * (EditorShell), "Elimina passo"/"Elimina join" del pannello (NodeInspector),
 * ✕ del gruppo (AnnotationNode).
 *  - in sola lettura nessun gesto apre la conferma ne' cancella (e i
 *    pulsanti non si mostrano: `useCanvasDeletion().canDelete`);
 *  - altrimenti si chiede conferma col dialog del progetto; la decisione si
 *    ricontrolla dopo la risposta (il ruolo puo' cambiare mentre il dialog
 *    e' aperto); poi resta annullabile con Ctrl+Z (snapshot prima);
 *  - senza chi chiede (nessun ConfirmProvider) non si cancella nulla
 *    (fail-closed);
 *  - collaudo live L5: la conferma elenca i passi che resterebbero senza
 *    ingresso (`orphanedByDeletion`), per ogni gesto e anche col testo del
 *    chiamante. Il salvataggio poi non si blocca: il canvas li segna «senza
 *    ingresso» e Valida li riporta.
 */
import { useCallback } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { nodeTitle } from '../components/canvas/nodeDescriptors';
import { shortcutText } from './shortcuts';
import { nodeRole } from './canvasGraph';
import { dataFlowEdges } from './runtimeBindingEdges';

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Testo del dialog per una cancellazione di nodi e/o archi. */
export function deletionDialog({ nodes = [], edges = [] } = {}) {
  let message;
  if (nodes.length === 1 && edges.length === 0) {
    message = `Eliminare «${nodeTitle(nodes[0].type, nodes[0].data)}»? I collegamenti del passo vengono rimossi.`;
  } else if (nodes.length === 0 && edges.length === 1) {
    message = 'Eliminare il collegamento selezionato? Le configurazioni del collegamento (es. le condizioni di un join) vanno perse.';
  } else {
    const parts = [];
    if (nodes.length) parts.push(plural(nodes.length, 'passo', 'passi'));
    if (edges.length) parts.push(plural(edges.length, 'collegamento', 'collegamenti'));
    message = `Eliminare ${parts.join(' e ')}?`;
  }
  return {
    title: 'Elimina dal canvas',
    message: `${message} Puoi annullare con ${shortcutText('undo')}.`,
    confirmText: 'Elimina',
    variant: 'danger',
  };
}

// Ruoli che senza ingresso non hanno dati (sorgenti, parametri e note si').
const NEEDS_INPUT = new Set(['step', 'output', 'sink']);

/**
 * Nodi che avevano un ingresso di dati e lo perderebbero tutto togliendo
 * `nodes` e `edges` dal grafo `graph`.
 */
export function orphanedByDeletion(graph, { nodes = [], edges = [] } = {}) {
  const goneNodes = new Set(nodes.map(n => n.id));
  const goneEdges = new Set(edges.map(e => e.id));
  const before = new Set();
  const after = new Set();
  for (const e of dataFlowEdges(graph?.edges || [])) {
    before.add(e.target);
    if (!goneEdges.has(e.id) && !goneNodes.has(e.source)) after.add(e.target);
  }
  return (graph?.nodes || []).filter(n => !goneNodes.has(n.id) && NEEDS_INPUT.has(nodeRole(n)) && before.has(n.id) && !after.has(n.id));
}

/** Frase della conferma per i passi che resterebbero senza ingresso ('' se nessuno). */
function orphanedSentence(orphaned) {
  if (!orphaned.length) return '';
  const names = orphaned.map(n => `«${nodeTitle(n.type, n.data)}»`).join(', ');
  return orphaned.length === 1
    ? `Il passo ${names} resterebbe senza ingresso: il canvas lo segnala e, finché non lo ricolleghi, il progetto non si può eseguire.`
    : `I passi ${names} resterebbero senza ingresso: il canvas li segnala e, finché non li ricolleghi, il progetto non si può eseguire.`;
}

/** L'unica decisione: si puo' cancellare dal canvas? */
export function canDeleteFromCanvas(state = useCanvasStore.getState()) {
  return !state.isReadOnly;
}

/**
 * L'unico punto che cancella dal canvas.
 * @param {object} req
 * @param {((cfg: object) => Promise<boolean>) | null} req.confirm  dal ConfirmProvider
 * @param {Array} [req.nodes]  nodi da cancellare (per il testo e il conteggio)
 * @param {Array} [req.edges]  archi da cancellare
 * @param {object} [req.dialog]  testo del dialog (default: deletionDialog)
 * @param {() => void} req.perform  la cancellazione (snapshot compreso)
 * @returns {Promise<boolean>} true = cancellato
 */
export async function requestCanvasDeletion({ confirm, nodes = [], edges = [], dialog = null, perform } = {}) {
  if (!canDeleteFromCanvas()) return false;
  if (!nodes.length && !edges.length) return false;
  if (typeof confirm !== 'function' || typeof perform !== 'function') return false;
  const base = dialog || deletionDialog({ nodes, edges });
  const warn = orphanedSentence(orphanedByDeletion(useCanvasStore.getState(), { nodes, edges }));
  const ok = Boolean(await confirm(warn ? { ...base, message: `${base.message} ${warn}` } : base));
  if (!ok || !canDeleteFromCanvas()) return false;
  perform();
  return true;
}

/** Per i componenti: se mostrare i comandi di eliminazione. */
export function useCanvasDeletion() {
  const canDelete = useCanvasStore(useCallback(s => canDeleteFromCanvas(s), []));
  return { canDelete, requestDeletion: requestCanvasDeletion };
}
