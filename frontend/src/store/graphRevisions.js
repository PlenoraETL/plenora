/**
 * graphRevisions — contatori delle modifiche del canvas tenuti DALLO STORE
 * (UI-2, 06/11/2026). Ogni scrittura di `nodes`/`edges` (azioni dello
 * store, React Flow, `setState` dei test) passa da qui e fa salire i
 * contatori del livello toccato (utils/canvasChange):
 *
 *   structureRev  struttura (nodi/archi aggiunti, tolti, estremi);
 *   contentRev    contenuto letto dal backend senza posizioni (sale anche
 *                 con la struttura);
 *   layoutRev     posizioni;
 *   selectionRev  selezione (sale anche con la struttura: un nodo tolto
 *                 puo' cambiare la selezione).
 *
 * Chi deve reagire a «il canvas e' cambiato» legge il contatore giusto
 * (hooks/useCanvasDerived) invece di serializzare o scorrere il canvas a
 * ogni fotogramma: un trascinamento fa salire solo layoutRev.
 */
import { graphChange } from '../utils/canvasChange';

export const REVISION_FIELDS = Object.freeze({
  structure: 'structureRev',
  content: 'contentRev',
  layout: 'layoutRev',
  selection: 'selectionRev',
});

export const INITIAL_REVISIONS = Object.freeze({ structureRev: 0, contentRev: 0, layoutRev: 0, selectionRev: 0 });

/**
 * Contatori da scrivere insieme a `patch` (nessuno se il grafo non cambia).
 *
 * Collaudo live L2: caricare o togliere il grafo (`graphLoaded` cambia) e'
 * un cambio di TUTTI i livelli, anche quando i nodi restano gli stessi (un
 * progetto nuovo e' vuoto prima e dopo il caricamento): chi legge il grafo
 * con useCanvasDerived si ricalcola. Prima la guida a canvas vuoto restava
 * spenta su un progetto nuovo.
 */
export function revisionsFor(state, patch) {
  if (!patch || typeof patch !== 'object') return null;
  if ('graphLoaded' in patch && Boolean(patch.graphLoaded) !== Boolean(state.graphLoaded)) {
    return Object.fromEntries(Object.values(REVISION_FIELDS).map(f => [f, (state[f] || 0) + 1]));
  }
  const nodesTouched = 'nodes' in patch && patch.nodes !== state.nodes;
  const edgesTouched = 'edges' in patch && patch.edges !== state.edges;
  if (!nodesTouched && !edgesTouched) return null;
  const change = graphChange(
    state.nodes, nodesTouched ? patch.nodes : state.nodes,
    state.edges, edgesTouched ? patch.edges : state.edges,
  );
  const out = {};
  if (change.structure) out.structureRev = (state.structureRev || 0) + 1;
  if (change.structure || change.content) out.contentRev = (state.contentRev || 0) + 1;
  if (change.layout) out.layoutRev = (state.layoutRev || 0) + 1;
  if (change.structure || change.selection) out.selectionRev = (state.selectionRev || 0) + 1;
  return Object.keys(out).length ? out : null;
}

/**
 * Middleware zustand: avvolge sia il `set` passato allo store sia
 * `api.setState` (scritture da fuori: test, reset della sessione), cosi'
 * nessuna scrittura del grafo salta i contatori. Un aggiornamento che
 * restituisce lo stato invariato resta invariato (nessuna notifica).
 */
export const graphRevisions = (config) => (set, get, api) => {
  const wrap = (base) => (partial, replace, ...rest) => base((state) => {
    const patch = typeof partial === 'function' ? partial(state) : partial;
    if (!patch || patch === state) return patch;
    const revisions = revisionsFor(state, patch);
    return revisions ? { ...patch, ...revisions } : patch;
  }, replace, ...rest);
  api.setState = wrap(api.setState);
  return config(wrap(set), get, api);
};
