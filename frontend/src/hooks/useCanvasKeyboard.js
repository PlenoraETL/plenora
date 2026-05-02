/**
 * useCanvasKeyboard.js — Shortcuts tastiera per il canvas
 *
 * useCanvasKeyboard:    Delete/Backspace per eliminare nodi/edges selezionati
 * useCanvasSaveShortcut: Ctrl+S / Cmd+S per salvataggio canvas
 *
 * Tutti gli hook accettano { enabled, onDelete, onSave }.
 */
import { useEffect, useCallback } from 'react';

/**
 * Hook per gestire gli eventi tastiera sul canvas React Flow
 * Gestisce Delete/Backspace per eliminare nodi e edges selezionati
 * 
 * @param {Object} options - Opzioni di configurazione
 * @param {Array} options.nodes - Array dei nodi React Flow
 * @param {Array} options.edges - Array degli edges React Flow
 * @param {Function} options.onDeleteNode - Callback per eliminare un nodo
 * @param {Function} options.setEdges - Setter degli edges da React Flow
 * @param {boolean} options.enabled - Se false, disabilita gli handler (default: true)
 * 
 * Uso:
 * ```jsx
 * useCanvasKeyboard({
 *   nodes,
 *   edges,
 *   onDeleteNode: handleDeleteNode,
 *   setEdges
 * });
 * ```
 */
export function useCanvasKeyboard(options) {
  const { 
    nodes, 
    edges, 
    onDeleteNode, 
    setEdges, 
    enabled = true 
  } = options;

  const handleKeyDown = useCallback((event) => {
    // Ignora se siamo in un input/textarea
    const target = event.target;
    if (
      target.tagName === 'INPUT' || 
      target.tagName === 'TEXTAREA' || 
      target.isContentEditable
    ) {
      return;
    }
    
    // Gestisci Delete/Backspace
    if (event.key === 'Delete' || event.key === 'Backspace') {
      // Elimina nodi selezionati
      const selectedNodes = nodes.filter(n => n.selected);
      if (selectedNodes.length > 0 && onDeleteNode) {
        selectedNodes.forEach(node => {
          onDeleteNode(node.id);
        });
      }
      
      // Elimina edges selezionati
      const selectedEdges = edges.filter(e => e.selected);
      if (selectedEdges.length > 0 && setEdges) {
        setEdges((eds) => 
          eds.filter(edge => !selectedEdges.find(e => e.id === edge.id))
        );
      }
    }
  }, [nodes, edges, onDeleteNode, setEdges]);

  useEffect(() => {
    if (!enabled) return;

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown, enabled]);
}

/**
 * Hook per gestire Ctrl+S per salvare il canvas
 * 
 * @param {Object} options - Opzioni di configurazione
 * @param {Function} options.onSave - Callback per salvare
 * @param {boolean} options.enabled - Se false, disabilita l'handler (default: true)
 * 
 * Uso:
 * ```jsx
 * useCanvasSaveShortcut({
 *   onSave: handleSaveCanvas
 * });
 * ```
 */
export function useCanvasSaveShortcut(options) {
  const { onSave, enabled = true } = options;

  const handleKeyDown = useCallback((event) => {
    // Ctrl+S o Cmd+S
    if ((event.ctrlKey || event.metaKey) && event.key === 's') {
      event.preventDefault();
      if (onSave) {
        onSave();
      }
    }
  }, [onSave]);

  useEffect(() => {
    if (!enabled) return;

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown, enabled]);
}

export default useCanvasKeyboard;
