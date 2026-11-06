/**
 * canvasContentKey — impronte SEMANTICHE del canvas (UI-2, 06/11/2026: una
 * sola fonte, calcolata quando il contenuto cambia e non a ogni render).
 *
 * Regola unica per "un'anteprima/una mappa calcolata prima e' ancora
 * attuale?": spostare, selezionare o ridimensionare nodi non cambia
 * l'impronta; ogni modifica ai dati, ai collegamenti, una correzione
 * automatica, un undo o un ricaricamento si'. Nessuna azione deve
 * ricordarsi di segnalarlo.
 *
 * Costo: una serializzazione per CONTENUTO. Le impronte di una coppia
 * (nodes, edges) si ricordano (WeakMap); una coppia nuova che differisce
 * dall'ultima calcolata solo per posizioni, selezione o stato di
 * interfaccia (utils/canvasChange.graphChange) riusa le impronte senza
 * serializzare: un trascinamento non serializza mai.
 */
import { serializeCanvas } from './canvasSerializer';
import { contentChanged, graphChange } from './canvasChange';
import { registerSessionReset } from './session';

const EMPTY = Object.freeze([]);

function computeSignatures(nodes, edges) {
  const { nodes: sn, edges: se } = serializeCanvas(nodes || EMPTY, edges || EMPTY);
  // eslint-disable-next-line no-unused-vars
  const withoutPosition = sn.map(({ position, ...rest }) => rest);
  return {
    // Quello che il backend legge: niente note, archi solo per il flusso.
    content: JSON.stringify({
      nodes: withoutPosition.filter(n => n.type !== 'comment'),
      // Gli edge semplici sono serializzati interi (selected, style, ...):
      // conta solo cio' che definisce il flusso.
      edges: se.map(e => ('join_type' in e ? e : {
        source: e.source, sourceHandle: e.sourceHandle ?? null,
        target: e.target, targetHandle: e.targetHandle ?? null,
        data: e.data ?? null,
      })),
    }),
    // Quello che si SALVA, posizioni escluse (si confrontano a parte:
    // store/saveState): note comprese, archi senza lo stato di interfaccia.
    saved: JSON.stringify({
      nodes: withoutPosition,
      edges: se.map(e => ('join_type' in e ? e : {
        id: e.id, type: e.type ?? null,
        source: e.source, sourceHandle: e.sourceHandle ?? null,
        target: e.target, targetHandle: e.targetHandle ?? null,
        data: e.data ?? null,
      })),
    }),
  };
}

const byPair = new WeakMap();
let last = null;
// All'uscita l'ultima impronta (testo del canvas dell'utente) non resta in
// memoria (R5.5); le altre vivono finche' vivono gli array del canvas.
registerSessionReset('canvasSignatures', () => { last = null; });

/** { content, saved } della coppia (nodes, edges), memorizzate. */
export function canvasSignatures(nodes, edges) {
  if (!nodes || !edges) return computeSignatures(nodes, edges);
  let byEdges = byPair.get(nodes);
  if (!byEdges) { byEdges = new WeakMap(); byPair.set(nodes, byEdges); }
  let sig = byEdges.get(edges);
  if (sig) return sig;
  if (last && !contentChanged(graphChange(last.nodes, nodes, last.edges, edges))) sig = last.sig;
  else sig = computeSignatures(nodes, edges);
  byEdges.set(edges, sig);
  last = { nodes, edges, sig };
  return sig;
}

/** Impronta di cio' che il backend legge (anteprime, query, colonne). */
export function canvasContentKey(nodes, edges) {
  return canvasSignatures(nodes, edges).content;
}

/** Stesse posizioni, nodo per nodo (stesso ordine), senza serializzare. */
export function samePositions(a, b) {
  const left = a || EMPTY;
  const right = b || EMPTY;
  if (left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    const p = left[i]?.position;
    const q = right[i]?.position;
    if ((p?.x ?? null) !== (q?.x ?? null) || (p?.y ?? null) !== (q?.y ?? null)) return false;
  }
  return true;
}
