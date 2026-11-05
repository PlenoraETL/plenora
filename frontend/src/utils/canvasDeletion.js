/**
 * canvasDeletion — regola UNICA delle cancellazioni dal canvas (Fase 5, T4;
 * R1.7, R1.11a).
 *
 * Regola: ogni cancellazione chiede conferma con il dialog del progetto,
 * qualunque sia il gesto: Canc/Backspace (hooks/useCanvasKeyboard), tasto
 * destro su un arco (EditorShell), "Elimina passo"/"Elimina Join" del
 * pannello (NodeInspector, con il suo testo). Dopo la conferma resta
 * annullabile con Ctrl+Z (snapshot prima di cancellare). Senza chi chiede
 * (nessun ConfirmProvider) non si cancella nulla: fail-closed.
 */
import { nodeTitle } from '../components/canvas/nodeDescriptors';

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
    message: `${message} Puoi annullare con Ctrl+Z.`,
    confirmText: 'Elimina',
    variant: 'danger',
  };
}

/**
 * @param {((cfg: object) => Promise<boolean>) | null} confirm  dal ConfirmProvider
 * @returns {Promise<boolean>} true = si cancella
 */
export async function confirmCanvasDeletion(confirm, { nodes = [], edges = [] } = {}) {
  if (!nodes.length && !edges.length) return false;
  if (typeof confirm !== 'function') return false;
  return Boolean(await confirm(deletionDialog({ nodes, edges })));
}
