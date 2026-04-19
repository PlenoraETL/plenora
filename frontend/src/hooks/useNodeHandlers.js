/**
 * useNodeHandlers - Hook condiviso per gestione nodi ReactFlow
 * 
 * Usato da: EstraiEditor, ManipolaEditor, ArricchisciEditor
 * 
 * Fornisce:
 * - updateNodeData: aggiorna data di un nodo
 * - deleteNode: elimina nodo e edges collegati
 * - updateNodePosition: aggiorna posizione nodo
 * - getNode: trova nodo per ID
 */

import { useCallback } from 'react';

/**
 * Hook per operazioni comuni sui nodi del canvas
 * 
 * @param {Function} setNodes - setState per i nodi (da useNodesState)
 * @param {Function} setEdges - setState per gli edges (da useEdgesState)
 * @returns {Object} handlers per manipolazione nodi
 * 
 * @example
 * const { updateNodeData, deleteNode } = useNodeHandlers(setNodes, setEdges);
 * 
 * // Aggiorna un campo
 * updateNodeData(nodeId, { fileName: 'output.csv' });
 * 
 * // Aggiorna più campi
 * updateNodeData(nodeId, { 
 *   format: 'geojson',
 *   geomColumn: 'geometry'
 * });
 * 
 * // Elimina nodo
 * deleteNode(nodeId);
 */
export function useNodeHandlers(setNodes, setEdges) {
  
  /**
   * Aggiorna i data di un nodo specifico
   * @param {string} nodeId - ID del nodo da aggiornare
   * @param {Object} updates - Oggetto con le proprietà da aggiornare/aggiungere
   */
  const updateNodeData = useCallback((nodeId, updates) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, data: { ...node.data, ...updates } }
        : node
      )
    );
  }, [setNodes]);

  /**
   * Elimina un nodo e tutti gli edges collegati
   * @param {string} nodeId - ID del nodo da eliminare
   */
  const deleteNode = useCallback((nodeId) => {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => 
      edge.source !== nodeId && edge.target !== nodeId
    ));
  }, [setNodes, setEdges]);

  /**
   * Aggiorna la posizione di un nodo
   * @param {string} nodeId - ID del nodo
   * @param {Object} position - { x, y }
   */
  const updateNodePosition = useCallback((nodeId, position) => {
    setNodes((nds) =>
      nds.map((node) => node.id === nodeId 
        ? { ...node, position }
        : node
      )
    );
  }, [setNodes]);

  /**
   * Trova un nodo per ID (utility sincrona, richiede nodes come param)
   * @param {Array} nodes - Array dei nodi
   * @param {string} nodeId - ID del nodo da trovare
   * @returns {Object|undefined} Il nodo trovato o undefined
   */
  const getNode = useCallback((nodes, nodeId) => {
    return nodes.find((node) => node.id === nodeId);
  }, []);

  /**
   * Aggiorna un campo specifico nei data del nodo
   * Shorthand per updateNodeData con singolo campo
   * @param {string} nodeId - ID del nodo
   * @param {string} field - Nome del campo
   * @param {any} value - Valore da impostare
   */
  const updateNodeField = useCallback((nodeId, field, value) => {
    updateNodeData(nodeId, { [field]: value });
  }, [updateNodeData]);

  /**
   * Aggiorna le colonne di un nodo sorgente
   * @param {string} nodeId - ID del nodo
   * @param {Array} columns - Array delle colonne
   */
  const updateNodeColumns = useCallback((nodeId, columns) => {
    updateNodeData(nodeId, { columns });
  }, [updateNodeData]);

  return {
    updateNodeData,
    deleteNode,
    updateNodePosition,
    getNode,
    updateNodeField,
    updateNodeColumns
  };
}

export default useNodeHandlers;
