/**
 * canvasContentKey — impronta di cio' che il backend legge del canvas
 * (serializzazione salvata), senza posizioni ne' note.
 *
 * Regola unica per "un'anteprima calcolata prima e' ancora attuale?":
 * spostare, riordinare o ridimensionare nodi non cambia l'impronta; ogni
 * modifica ai dati, ai collegamenti, una correzione automatica, un undo o
 * un ricaricamento si'. Nessuna azione deve ricordarsi di segnalarlo.
 */
import { serializeCanvas } from './canvasSerializer';

export function canvasContentKey(nodes, edges) {
  const { nodes: sn, edges: se } = serializeCanvas(nodes || [], edges || []);
  return JSON.stringify({
    // eslint-disable-next-line no-unused-vars
    nodes: sn.filter(n => n.type !== 'comment').map(({ position, ...rest }) => rest),
    // Gli edge semplici sono serializzati interi (selected, style, ...):
    // conta solo cio' che definisce il flusso.
    edges: se.map(e => ('join_type' in e ? e : {
      source: e.source, sourceHandle: e.sourceHandle ?? null,
      target: e.target, targetHandle: e.targetHandle ?? null,
      data: e.data ?? null,
    })),
  });
}
