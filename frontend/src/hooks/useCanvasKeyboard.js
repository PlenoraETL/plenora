/**
 * useCanvasKeyboard.js — Shortcuts tastiera per il canvas
 *
 * useCanvasKeyboard: Delete/Backspace per eliminare nodi/edges selezionati
 *
 * L'hook accetta { enabled, onDeleteNode, setEdges, nodes, edges }.
 */
import { useEffect, useCallback, useRef } from 'react';

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

  // Pre-fix (P2 frontend-hooks): `handleKeyDown` aveva `[nodes,
  // edges, ...]` nelle deps. `nodes`/`edges` sono gli array ReactFlow
  // completi: ogni cambio (drag, selezione, dati) invalidava la
  // callback → l'`useEffect` faceva removeEventListener +
  // addEventListener su `document` ad alta frequenza durante un
  // canvas attivo. Post-fix: refs aggiornati via effect separato;
  // `handleKeyDown` legge lazy `nodesRef.current` / `edgesRef.current`
  // ed e' referenzialmente stabile per tutto il ciclo di vita
  // (deps = [onDeleteNode, setEdges]).
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

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
      const selectedNodes = nodesRef.current.filter(n => n.selected);
      if (selectedNodes.length > 0 && onDeleteNode) {
        selectedNodes.forEach(node => {
          onDeleteNode(node.id);
        });
      }

      // Elimina edges selezionati
      const selectedEdges = edgesRef.current.filter(e => e.selected);
      if (selectedEdges.length > 0 && setEdges) {
        setEdges((eds) =>
          eds.filter(edge => !selectedEdges.find(e => e.id === edge.id))
        );
      }
    }
  }, [onDeleteNode, setEdges]);

  useEffect(() => {
    if (!enabled) return;

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown, enabled]);
}

export default useCanvasKeyboard;
