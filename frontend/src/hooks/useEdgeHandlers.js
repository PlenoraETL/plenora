/**
 * useEdgeHandlers.js — Creazione ed eliminazione degli archi di Estrai.
 *
 * Regola (Blocco A, raccolta b): ogni configurazione si modifica nel
 * pannello laterale a partire da una SELEZIONE. Un join nuovo nasce "da
 * completare" (utils/estraiJoins.newJoinEdge: nessuna colonna scelta al
 * posto dell'utente) e resta selezionato, cosi' il pannello si apre su di
 * lui (canvas/JoinEdgePanel). Tutti i modi di creare un join passano da
 * `connectJoin`, che usa store.addSubgraph: tabella nuova e arco sono UN
 * passo di annullamento.
 *
 *  - onConnect          trascinamento (binding, Output, Carica o join);
 *  - connectBranch      "Collega" del pannello (rami, Fase 4 M6);
 *  - connectJoin        palette e tabella correlata ArcGIS ({ node, ... });
 *  - onEdgeContextMenu  tasto destro = elimina l'arco, come in Manipola e
 *                       Arricchisci (annullabile); niente in sola lettura.
 * Il clic sull'arco lo seleziona (React Flow): nessun handler.
 *
 * Le callback leggono lo stato corrente dallo store (getState): sono
 * stabili per tutta la vita del canvas e non usano istantanee.
 */
import { useCallback } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import {
  createParameterBindingEdge,
  isScalarProjectInputNode,
} from '../utils/runtimeBindingEdges';
import { isJoinEdge, newJoinEdge } from '../utils/estraiJoins';

// Il nodo Carica ArcGIS esiste con due spellings (toolbar vs deserializer).
const isCaricaArcgisType = (type) => type === 'caricaArcgis' || type === 'carica_arcgis';
const isCaricaTarget = (node) => node.type === 'carica' || isCaricaArcgisType(node.type)
  || (['datasourceNode', 'datasource_node'].includes(node.type) && node.data?.mode === 'carica');

// Archi verso Output e caricamenti: nessuna configurazione.
const FLOW_LOOK = {
  output: { label: 'output', style: { stroke: '#4CAF50', strokeWidth: 2 }, animated: true },
  carica: { label: 'Carica DB', style: { stroke: '#7c3aed', strokeWidth: 2 }, animated: true },
  carica_arcgis: { label: 'Carica ArcGIS', style: { stroke: '#2DD4BF', strokeWidth: 2 }, animated: true },
};

const samePair = (e, a, b) => (e.source === a && e.target === b) || (e.source === b && e.target === a);

/**
 * @param {Object} [options]
 * @param {string} [options.resultNodeType='resultNode'] tipo del nodo Output
 */
export function useEdgeHandlers(options = {}) {
  const { resultNodeType = 'resultNode' } = options;

  /**
   * Join tra `source` e `target`, con un nodo nuovo opzionale (palette,
   * tabella correlata). Un solo passo di annullamento; l'arco resta
   * selezionato. Tra due tabelle gia' unite si seleziona il join esistente.
   */
  const connectJoin = useCallback(({ source, target, node = null, join_type, suggestion = null }) => {
    const store = useCanvasStore.getState();
    if (store.isReadOnly) return null;
    if (!node) {
      const byId = new Map(store.nodes.map(n => [n.id, n]));
      const existing = store.edges.find(e => samePair(e, source, target) && isJoinEdge(e, byId));
      if (existing) { store.selectEdge(existing.id); return existing; }
    }
    const edge = newJoinEdge({ source, target, join_type, suggestion });
    store.addSubgraph({ nodes: node ? [node] : [], edges: [edge], selectEdgeId: edge.id });
    return edge;
  }, []);

  const onConnect = useCallback((params) => {
    const { nodes, editEdges } = useCanvasStore.getState();
    const sourceNode = nodes.find(n => n.id === params.source);
    const targetNode = nodes.find(n => n.id === params.target);
    if (isScalarProjectInputNode(sourceNode)) {
      const edge = createParameterBindingEdge(params);
      editEdges(eds => (eds.some(e => e.id === edge.id) ? eds : eds.concat(edge)));
      return;
    }
    const flowType = !targetNode ? null
      : (targetNode.type === resultNodeType || targetNode.type === 'result') ? 'output'
        : isCaricaTarget(targetNode) ? (isCaricaArcgisType(targetNode.type) ? 'carica_arcgis' : 'carica')
          : null;
    if (flowType) {
      const prefix = flowType === 'output' ? 'edge' : 'edge_carica';
      editEdges(eds => (eds.some(e => samePair(e, params.source, params.target)) ? eds : eds.concat({
        id: `${prefix}_${Date.now()}`, source: params.source, target: params.target,
        ...FLOW_LOOK[flowType], data: { join_type: flowType },
      })));
      return;
    }
    connectJoin({ source: params.source, target: params.target });
  }, [resultNodeType, connectJoin]);

  // "Collega" (Fase 4, M6): in Estrai e' un join, la tabella scollegata e'
  // la sorgente, come trascinandola.
  const connectBranch = useCallback((endId, targetId) => connectJoin({ source: endId, target: targetId }), [connectJoin]);

  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    useCanvasStore.getState().removeEdges(edge.id);
  }, []);

  return { onConnect, connectBranch, connectJoin, onEdgeContextMenu };
}

export default useEdgeHandlers;
