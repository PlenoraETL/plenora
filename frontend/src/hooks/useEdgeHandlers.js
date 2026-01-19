import { useState, useCallback, useMemo } from 'react';
import { addEdge } from 'reactflow';

/**
 * useEdgeHandlers - Hook per gestione edges e modal join
 * 
 * @param {Array} nodes - Nodi React Flow
 * @param {Function} setEdges - Setter edges React Flow
 * @param {Object} options - Opzioni
 * @param {string} options.resultNodeType - Tipo nodo risultato (default: 'resultNode')
 * @param {Function} options.onEdgeCreated - Callback dopo creazione edge
 * @param {Function} options.onEdgeUpdated - Callback dopo aggiornamento edge
 * 
 * @returns {Object} Handlers e stato per gestione edges
 */
export function useEdgeHandlers(nodes, setEdges, options = {}) {
  const {
    resultNodeType = 'resultNode',
    onEdgeCreated,
    onEdgeUpdated
  } = options;

  // State per modal join
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [pendingEdge, setPendingEdge] = useState(null);
  const [editingEdge, setEditingEdge] = useState(null);

  /**
   * Crea stile edge in base al tipo
   */
  const getEdgeStyle = useCallback((joinConfig) => {
    const isSpatial = joinConfig?.is_spatial;
    const isOutput = joinConfig?.join_type === 'output';
    
    if (isOutput) {
      return {
        label: 'output',
        style: { stroke: '#4CAF50', strokeWidth: 2 },
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
    const targetNode = nodes.find(n => n.id === params.target);
    
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
      
      setEdges((eds) => addEdge(newEdge, eds));
      
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
  }, [nodes, setEdges, resultNodeType, getEdgeStyle, onEdgeCreated]);

  /**
   * Handler click su edge (per editing)
   */
  const onEdgeClick = useCallback((event, edge) => {
    // Non aprire modal per edge output
    if (edge.data?.join_type === 'output') return;
    
    setEditingEdge(edge);
    setShowJoinModal(true);
  }, []);

  /**
   * Handler context menu su edge (per delete)
   */
  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    
    // Non permettere delete di edge output tramite context menu
    if (edge.data?.join_type === 'output') {
      if (window.confirm('Eliminare questo collegamento al nodo output?')) {
        setEdges((eds) => eds.filter((e) => e.id !== edge.id));
      }
      return;
    }
    
    // Per altri edge, apri modal per editing/delete
    setEditingEdge(edge);
    setShowJoinModal(true);
  }, [setEdges]);

  /**
   * Salva configurazione join
   */
  const handleSaveJoin = useCallback((joinConfig) => {
    const sourceNode = nodes.find(n => n.id === (editingEdge?.source || pendingEdge?.source));
    const targetNode = nodes.find(n => n.id === (editingEdge?.target || pendingEdge?.target));
    
    const edgeStyle = getEdgeStyle(joinConfig);
    
    if (editingEdge) {
      // Aggiorna edge esistente
      setEdges((eds) =>
        eds.map((e) => {
          if (e.id === editingEdge.id) {
            const updatedEdge = {
              ...e,
              ...edgeStyle,
              data: {
                ...e.data,
                ...joinConfig,
                source_table: sourceNode?.data?.table_name,
                target_table: targetNode?.data?.table_name
              }
            };
            
            if (onEdgeUpdated) {
              onEdgeUpdated(updatedEdge);
            }
            
            return updatedEdge;
          }
          return e;
        })
      );
    } else if (pendingEdge) {
      // Crea nuovo edge
      const newEdge = {
        id: `edge_${Date.now()}`,
        source: pendingEdge.source,
        target: pendingEdge.target,
        ...edgeStyle,
        data: {
          ...joinConfig,
          source_table: sourceNode?.data?.table_name,
          target_table: targetNode?.data?.table_name
        }
      };
      
      setEdges((eds) => addEdge(newEdge, eds));
      
      if (onEdgeCreated) {
        onEdgeCreated(newEdge);
      }
    }
    
    // Chiudi modal
    setShowJoinModal(false);
    setPendingEdge(null);
    setEditingEdge(null);
  }, [nodes, pendingEdge, editingEdge, setEdges, getEdgeStyle, onEdgeCreated, onEdgeUpdated]);

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
      setEdges((eds) => eds.filter((e) => e.id !== editingEdge.id));
      setShowJoinModal(false);
      setEditingEdge(null);
    }
  }, [editingEdge, setEdges]);

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
