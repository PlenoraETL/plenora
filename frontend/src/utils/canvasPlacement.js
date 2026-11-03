/**
 * canvasPlacement — dove nasce un nodo nuovo e come entra nel flusso (Fase 4).
 *
 * Due regole uniche, usate dallo store per OGNI aggiunta (palette, menu,
 * trascinamento, editor diversi):
 *  1. un nodo nuovo o spostato non si sovrappone mai a un altro
 *     (findFreePosition: scende di una riga finche' trova posto, e in
 *     ultima istanza va sotto a tutti; settleNodes per i nodi spostati);
 *  2. "aggiungi dopo A" quando A ha gia' un solo passo successivo B e il
 *     nuovo nodo lascia passare i dati: il nodo entra nella catena
 *     A -> nuovo -> B e i nodi a valle di B si spostano a destra di una
 *     colonna per fargli posto (planInsertAfter).
 */
import { isParameterBindingEdge } from './runtimeBindingEdges';

export const NODE_W = 232;
export const NODE_H = 80;
export const COLUMN_STEP = 290;
const ROW_STEP = NODE_H + 24;
const MAX_TRIES = 60;

export const nodeSize = (n) => ({
  w: n.measured?.width || n.width || NODE_W,
  h: n.measured?.height || n.height || NODE_H,
});

function overlaps(pos, nodes, size) {
  return nodes.some((n) => {
    if (n.type === 'comment' || !n.position) return false;
    const { w, h } = nodeSize(n);
    return pos.x < n.position.x + w && pos.x + size.w > n.position.x
      && pos.y < n.position.y + h && pos.y + size.h > n.position.y;
  });
}

/**
 * Prima posizione libera a partire da `desired`, scendendo di riga, per un
 * nodo grande `size` (default: nodo compatto). Sempre libera.
 */
export function findFreePosition(nodes, desired, size = { w: NODE_W, h: NODE_H }) {
  const start = { x: Math.round(desired?.x || 0), y: Math.round(desired?.y || 0) };
  for (let i = 0; i < MAX_TRIES; i += 1) {
    const pos = { x: start.x, y: start.y + i * ROW_STEP };
    if (!overlaps(pos, nodes, size)) return pos;
  }
  // Sotto a tutti i nodi: libero per costruzione.
  const bottom = nodes.reduce((max, n) => (n.type === 'comment' || !n.position
    ? max : Math.max(max, n.position.y + nodeSize(n).h)), start.y);
  return { x: start.x, y: bottom + ROW_STEP };
}

/**
 * Riposiziona i nodi `movedIds` (gia' alla posizione voluta) in modo che
 * nessuno si sovrapponga agli altri ne' tra loro: da sinistra a destra,
 * ognuno scende al primo posto libero. Gli altri nodi non si muovono.
 */
export function settleNodes(nodes, movedIds) {
  const fixed = nodes.filter(n => !movedIds.has(n.id));
  const moved = nodes.filter(n => movedIds.has(n.id))
    .sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
  const placed = new Map();
  const settled = [...fixed];
  for (const n of moved) {
    const m = n.type === 'comment' ? n : { ...n, position: findFreePosition(settled, n.position, nodeSize(n)) };
    placed.set(n.id, m);
    settled.push(m);
  }
  return nodes.map(n => placed.get(n.id) || n);
}

/** Edge di flusso dati (esclusi i binding degli input runtime). */
const dataEdges = (edges) => edges.filter(e => !isParameterBindingEdge(e));

/** Id dei nodi raggiungibili da `startId` (compreso) seguendo il flusso. */
function downstreamOf(startId, edges) {
  const out = new Map();
  for (const e of dataEdges(edges)) {
    if (!out.has(e.source)) out.set(e.source, []);
    out.get(e.source).push(e.target);
  }
  const seen = new Set([startId]);
  const queue = [startId];
  while (queue.length) {
    for (const next of out.get(queue.shift()) || []) {
      if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
  }
  return seen;
}

/**
 * Piano per "aggiungi dopo `afterId`".
 * @returns {{ position, reroute: object|null, shiftIds: Set<string> }}
 *   reroute = edge A->B da sostituire con A->nuovo->B (null = ramo nuovo);
 *   shiftIds = nodi da spostare a destra di COLUMN_STEP.
 */
export function planInsertAfter(nodes, edges, afterId, { passThrough = false } = {}) {
  const after = nodes.find(n => n.id === afterId);
  if (!after) return { position: null, reroute: null, shiftIds: new Set() };
  const outgoing = dataEdges(edges).filter(e => e.source === afterId);
  const desired = { x: after.position.x + COLUMN_STEP, y: after.position.y };
  if (passThrough && outgoing.length === 1) {
    const reroute = outgoing[0];
    const shiftIds = downstreamOf(reroute.target, edges);
    shiftIds.delete(afterId); // un ciclo non deve spostare il nodo di partenza
    return { position: desired, reroute, shiftIds };
  }
  return { position: findFreePosition(nodes, desired), reroute: null, shiftIds: new Set() };
}
