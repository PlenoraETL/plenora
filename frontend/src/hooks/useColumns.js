/**
 * useColumns — accesso React alla fonte unica delle colonne (Blocco A,
 * regola 4).
 *
 *  - useColumnSync(projectId): avvia l'UNICA sottoscrizione che ricalcola
 *    la mappa (store/useColumnStore.startColumnSync). La monta il guscio
 *    dell'editor (EditorShell): una per editor, nessuna chiamata sparsa.
 *  - useColumnsOf(nodeId, { side, source }): il selettore columnsOf
 *    (utils/columnSource) legato agli store, PER NODO e memorizzato
 *    (createColumnsMemo): finche' il risultato del nodo non cambia (mappa,
 *    archi, configurazioni che contano) resta lo STESSO oggetto, quindi il
 *    lettore non si ridisegna. Trascinare un nodo cambia `nodes` a ogni
 *    fotogramma ma non il risultato (salvo lo spostamento verticale che
 *    cambia l'ordine dei lati senza maniglia).
 *  - getColumnsOf(nodeId, opts): la stessa lettura fuori dal render (azioni).
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useShallow } from 'zustand/react/shallow';
import useCanvasStore from '../store/useCanvasStore';
import useColumnStore, { startColumnSync } from '../store/useColumnStore';
import { useCanvasDerived } from './useCanvasDerived';
import { columnsOf, createColumnsMemo } from '../utils/columnSource';

export function useColumnSync(projectId) {
  useEffect(() => {
    if (!projectId) return undefined;
    return startColumnSync({ projectId, canvasStore: useCanvasStore });
  }, [projectId]);
}

const columnPart = s => ({ schemas: s.schemas, states: s.states, status: s.status, error: s.error });

export function useColumnsOf(nodeId, { side = null, source = null } = {}) {
  const memoRef = useRef(null);
  if (!memoRef.current) memoRef.current = createColumnsMemo();
  const columns = useColumnStore(useShallow(columnPart));
  const select = useCallback(
    canvas => memoRef.current(columns, canvas, nodeId, { side, source }),
    [columns, nodeId, side, source],
  );
  return useCanvasStore(select);
}

/**
 * Tipo e dati dei nodi `ids` (null se assenti), per i titoli dei lati dei
 * lettori. Selettore per nodo: le posizioni non contano, quindi un
 * trascinamento non ridisegna chi lo usa.
 */
export function useNodesInfo(ids) {
  const key = ids.join('\u0000');
  // Ricalcolo solo quando cambia il contenuto (hooks/useCanvasDerived).
  const flat = useCanvasDerived(s => ids.flatMap((nodeId) => {
    const node = (s.nodes || []).find(n => n.id === nodeId);
    return node ? [node.type, node.data] : [null, null];
  }), { deps: [key] });
  return useMemo(
    () => (key ? key.split('\u0000') : []).map((nodeId, i) => (flat[2 * i] == null ? null
      : { id: nodeId, type: flat[2 * i], data: flat[2 * i + 1] || {} })),
    [flat, key],
  );
}

/**
 * Tutti i nodi SENZA posizioni: id, tipo, dati e selezione (Fase 5, T6,
 * R9-U N6). Per i pannelli che elencano i nodi (pannello laterale, vista
 * Passi, finestre): un trascinamento cambia solo le posizioni, quindi non li
 * ridisegna. Il test tests/shared/nodeLocalState.contract.test.jsx vieta
 * `useCanvasStore(s => s.nodes)` fuori dal canvas.
 */
export function useNodesContent() {
  const flat = useCanvasDerived(s => (s.nodes || []).flatMap(n => [n.id, n.type, n.data, Boolean(n.selected)]), { on: ['content', 'selection'] });
  return useMemo(() => {
    const out = [];
    for (let i = 0; i < flat.length; i += 4) out.push({ id: flat[i], type: flat[i + 1], data: flat[i + 2] || {}, selected: flat[i + 3] });
    return out;
  }, [flat]);
}

/** Snapshot corrente (mappa + grafo), per le azioni fuori dal render. */
export function columnSnapshot() {
  const columns = useColumnStore.getState();
  const canvas = useCanvasStore.getState();
  return { ...columnPart(columns), nodes: canvas.nodes, edges: canvas.edges, mode: canvas.mode };
}

export function getColumnsOf(nodeId, opts = {}) {
  return columnsOf(columnSnapshot(), nodeId, opts);
}

/** Ritenta subito il calcolo (pulsante "Riprova" dell'avviso). */
export function refreshColumns() {
  useColumnStore.getState().refresh();
}
