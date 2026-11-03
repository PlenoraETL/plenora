/**
 * useEdgeHandlers.js — Hook per gestione edges e apertura JoinModal
 *
 * Gestisce onConnect (creazione edge), onEdgesChange, e l'apertura
 * automatica di JoinModal quando si crea un edge tra due nodi.
 *
 * Ritorna: { onConnect, onEdgeClick, onEdgeContextMenu, joinModalProps, ... }
 *
 * Ogni operazione qui e' un'azione UTENTE: passa da `editEdges` dello store
 * (snapshot undo + dirty). Prima il setter generico + l'euristica strutturale
 * non marcava dirty la modifica di un join esistente (stessi id): Esegui usava
 * la config vecchia e Salva non salvava (audit R1.1).
 */
import { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { addEdge } from 'reactflow';
import {
  createParameterBindingEdge,
  isParameterBindingEdge,
  isScalarProjectInputNode,
} from '../utils/runtimeBindingEdges';

/**
 * useEdgeHandlers - Hook per gestione edges e modal join
 * 
 * @param {Array} nodes - Nodi React Flow
 * @param {Function} editEdges - Mutazione utente degli edge (useCanvasStore.editEdges)
 * @param {Object} options - Opzioni
 * @param {string} options.resultNodeType - Tipo nodo risultato (default: 'resultNode')
 * @param {Function} options.onEdgeCreated - Callback dopo creazione edge
 * @param {Function} options.onEdgeUpdated - Callback dopo aggiornamento edge
 * 
 * @returns {Object} Handlers e stato per gestione edges
 */
// Il nodo Carica ArcGIS esiste con due spellings (toolbar vs deserializer).
const isCaricaArcgisType = (type) => type === 'caricaArcgis' || type === 'carica_arcgis';

export function useEdgeHandlers(nodes, editEdges, options = {}) {
  const {
    resultNodeType = 'resultNode',
    onEdgeCreated,
    onEdgeUpdated
  } = options;

  // State per modal join
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [pendingEdge, setPendingEdge] = useState(null);
  const [editingEdge, setEditingEdge] = useState(null);

  // Pre-fix (P2 frontend-hooks): le callback `onConnect`,
  // `onEdgeClick`, `handleSaveJoin` avevano `nodes` nelle deps —
  // l'array intero di ReactFlow invalidava le callback ad ogni
  // drag/selezione/aggiornamento di un nodo qualsiasi, anche se
  // irrilevante per join. Le modal/toolbar che le ricevevano come
  // prop si rendevano di nuovo a frequenza elevata. Post-fix:
  // `nodesRef.current` letto lazy dentro le callback; le deps non
  // includono piu' `nodes` → callback referenzialmente stabili
  // attraverso l'intera vita del canvas.
  const nodesRef = useRef(nodes);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);

  // P1 (2026-05-26): refs per editingEdge e pendingEdge cosi'
  // handleSaveJoin non li ha nelle deps. Pre-fix ogni cambio
  // editing/pending modificava la reference della callback,
  // costringendo JoinModal (memo) a rimontare il form e perdere i
  // campi in compilazione. Pattern identico a `nodesRef`.
  const editingEdgeRef = useRef(editingEdge);
  const pendingEdgeRef = useRef(pendingEdge);
  useEffect(() => { editingEdgeRef.current = editingEdge; }, [editingEdge]);
  useEffect(() => { pendingEdgeRef.current = pendingEdge; }, [pendingEdge]);

  /**
   * Crea stile edge in base al tipo
   */
  const getEdgeStyle = useCallback((joinConfig) => {
    const isSpatial = joinConfig?.is_spatial;
    const isOutput = joinConfig?.join_type === 'output';
    const isCarica = joinConfig?.join_type === 'carica';
    
    if (isOutput) {
      return {
        label: 'output',
        style: { stroke: '#4CAF50', strokeWidth: 2 },
        animated: true
      };
    }
    
    if (joinConfig?.join_type === 'carica_arcgis') {
      return {
        label: 'Carica ArcGIS',
        style: { stroke: '#2DD4BF', strokeWidth: 2 },
        animated: true
      };
    }

    if (isCarica) {
      return {
        label: 'Carica DB',
        style: { stroke: '#7c3aed', strokeWidth: 2 },
        animated: true
      };
    }
    
    if (isSpatial) {
      return {
        label: `🌐 ${joinConfig.spatial_predicate?.replace('ST_', '') || 'spatial'}`,
        style: { stroke: '#E91E63', strokeWidth: 2 },
        animated: true
      };
    }
    
    return {
      label: `${joinConfig?.join_type || 'left'} join`,
      style: { stroke: '#2196F3', strokeWidth: 2 },
      animated: true
    };
  }, []);

  /**
   * Handler connessione nodi
   */
  const onConnect = useCallback((params) => {
    const sourceNode = nodesRef.current.find(n => n.id === params.source);
    if (isScalarProjectInputNode(sourceNode)) {
      const newEdge = createParameterBindingEdge(params);
      editEdges(eds => addEdge(newEdge, eds));
      if (onEdgeCreated) onEdgeCreated(newEdge);
      return;
    }
    const targetNode = nodesRef.current.find(n => n.id === params.target);
    
    // Se target è nodo risultato, crea edge output direttamente
    if (targetNode && (targetNode.type === resultNodeType || targetNode.type === 'result')) {
      const edgeStyle = getEdgeStyle({ join_type: 'output' });
      const newEdge = {
        id: `edge_${Date.now()}`,
        source: params.source,
        target: params.target,
        ...edgeStyle,
        data: { join_type: 'output' }
      };
      
      editEdges((eds) => addEdge(newEdge, eds));
      
      if (onEdgeCreated) {
        onEdgeCreated(newEdge);
      }
      return;
    }
    
    // Se target e' nodo CARICA o Data Source in modalita' carica, crea edge diretto.
    if (targetNode && (targetNode.type === 'carica' || isCaricaArcgisType(targetNode.type) ||
        (['datasourceNode', 'datasource_node'].includes(targetNode.type) && targetNode.data?.mode === 'carica'))) {
      const joinType = isCaricaArcgisType(targetNode.type) ? 'carica_arcgis' : 'carica';
      const edgeStyle = getEdgeStyle({ join_type: joinType });
      const newEdge = {
        id: `edge_carica_${Date.now()}`,
        source: params.source,
        target: params.target,
        ...edgeStyle,
        data: { join_type: joinType }
      };

      editEdges((eds) => addEdge(newEdge, eds));

      if (onEdgeCreated) {
        onEdgeCreated(newEdge);
      }
      return;
    }
    
    // Altrimenti apri modal per configurare join
    setPendingEdge({
      source: params.source,
      target: params.target
    });
    setShowJoinModal(true);
  }, [editEdges, resultNodeType, getEdgeStyle, onEdgeCreated]);

  /**
   * Handler click su edge (per editing)
   */
  const onEdgeClick = useCallback((event, edge) => {
    if (isParameterBindingEdge(edge)) return;
    // Non aprire modal per edge output
    if (edge.data?.join_type === 'output') return;
    
    // Non aprire modal per edge carica
    if (edge.data?.join_type === 'carica' || edge.data?.join_type === 'carica_arcgis') return;

    // Verifica anche il tipo del nodo target (fallback)
    const targetNode = nodesRef.current.find(n => n.id === edge.target);
    if (targetNode && (
      targetNode.type === 'carica' ||
      isCaricaArcgisType(targetNode.type) ||
      targetNode.type === 'resultNode' ||
      targetNode.type === 'result' ||
      (['datasourceNode', 'datasource_node'].includes(targetNode.type) && targetNode.data?.mode === 'carica')
    )) {
      return;
    }
    
    setEditingEdge(edge);
    setShowJoinModal(true);
  }, []);

  /**
   * Handler context menu su edge (per delete)
   */
  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    if (isParameterBindingEdge(edge)) {
      editEdges((eds) => eds.filter((item) => item.id !== edge.id));
      return;
    }
    
    // Edge output e carica: eliminazione diretta (context menu è già intenzionale)
    if (edge.data?.join_type === 'output' || edge.data?.join_type === 'carica' || edge.data?.join_type === 'carica_arcgis') {
      editEdges((eds) => eds.filter((e) => e.id !== edge.id));
      return;
    }
    
    // Per altri edge, apri modal per editing/delete
    setEditingEdge(edge);
    setShowJoinModal(true);
  }, [editEdges]);

  /**
   * Salva configurazione join
   */
  const handleSaveJoin = useCallback((joinConfig) => {
    const currentNodes = nodesRef.current;
    const currentEditingEdge = editingEdgeRef.current;
    const currentPendingEdge = pendingEdgeRef.current;
    const sourceNode = currentNodes.find(n => n.id === (currentEditingEdge?.source || currentPendingEdge?.source));
    const targetNode = currentNodes.find(n => n.id === (currentEditingEdge?.target || currentPendingEdge?.target));

    const edgeStyle = getEdgeStyle(joinConfig);

    if (currentEditingEdge) {
      // Aggiorna edge esistente: azione utente (dirty + undo).
      const updatedEdge = {
        ...currentEditingEdge,
        ...edgeStyle,
        data: {
          ...currentEditingEdge.data,
          ...joinConfig,
          source_table: sourceNode?.data?.table_name,
          target_table: targetNode?.data?.table_name
        }
      };
      editEdges((eds) => eds.map((e) => (
        e.id === currentEditingEdge.id ? { ...e, ...edgeStyle, data: { ...e.data, ...updatedEdge.data } } : e
      )));
      if (onEdgeUpdated) onEdgeUpdated(updatedEdge);
    } else if (currentPendingEdge) {
      // Crea nuovo edge
      const newEdge = {
        id: `edge_${Date.now()}`,
        source: currentPendingEdge.source,
        target: currentPendingEdge.target,
        ...edgeStyle,
        data: {
          ...joinConfig,
          source_table: sourceNode?.data?.table_name,
          target_table: targetNode?.data?.table_name
        }
      };

      editEdges((eds) => addEdge(newEdge, eds));

      if (onEdgeCreated) {
        onEdgeCreated(newEdge);
      }
    }

    // Chiudi modal
    setShowJoinModal(false);
    setPendingEdge(null);
    setEditingEdge(null);
  }, [editEdges, getEdgeStyle, onEdgeCreated, onEdgeUpdated]);

  /**
   * Annulla operazione
   */
  const handleCancelJoin = useCallback(() => {
    setShowJoinModal(false);
    setPendingEdge(null);
    setEditingEdge(null);
  }, []);

  /**
   * Elimina edge corrente
   */
  const handleDeleteEdge = useCallback(() => {
    if (editingEdge) {
      editEdges((eds) => eds.filter((e) => e.id !== editingEdge.id));
      setShowJoinModal(false);
      setEditingEdge(null);
    }
  }, [editingEdge, editEdges]);

  // Nodi sorgente/target per modal
  const modalNodes = useMemo(() => {
    const sourceNodeId = editingEdge?.source || pendingEdge?.source;
    const targetNodeId = editingEdge?.target || pendingEdge?.target;
    
    return {
      sourceNode: nodes.find(n => n.id === sourceNodeId),
      targetNode: nodes.find(n => n.id === targetNodeId)
    };
  }, [nodes, editingEdge, pendingEdge]);

  return {
    // State
    showJoinModal,
    pendingEdge,
    editingEdge,
    sourceNode: modalNodes.sourceNode,
    targetNode: modalNodes.targetNode,
    isEditing: !!editingEdge,
    
    // Handlers per React Flow
    onConnect,
    onEdgeClick,
    onEdgeContextMenu,
    
    // Handlers per modal
    handleSaveJoin,
    handleCancelJoin,
    handleDeleteEdge,
    
    // Utils
    getEdgeStyle
  };
}

export default useEdgeHandlers;
