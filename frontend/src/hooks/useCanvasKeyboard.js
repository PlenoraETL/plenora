/**
 * useCanvasKeyboard.js — Shortcuts tastiera per il canvas
 *
 * useCanvasKeyboard: Delete/Backspace per eliminare nodi/edges selezionati
 *
 * L'hook accetta { enabled, onDeleteNode, onDeleteEdges, nodes, edges }.
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
 * @param {Function} options.onDeleteEdges - Rimuove gli edge per id (azione utente dello store)
 * @param {boolean} options.enabled - Se false, disabilita gli handler (default: true)
 * 
 * Uso:
 * ```jsx
 * useCanvasKeyboard({
 *   nodes,
 *   edges,
 *   onDeleteNode: handleDeleteNode,
 *   onDeleteEdges
 * });
 * ```
 */
export function useCanvasKeyboard(options) {
  const {
    nodes,
    edges,
    onDeleteNode,
    onDeleteEdges,
    enabled = true,
    onUndo,          // #24: Ctrl/Cmd+Z
    onRedo,          // #24: Ctrl/Cmd+Shift+Z o Ctrl/Cmd+Y
    onBeforeDelete,  // #24: snapshot pre-delete (per rendere annullabile la cancellazione)
  } = options;

  // Pre-fix (P2 frontend-hooks): `handleKeyDown` aveva `[nodes,
  // edges, ...]` nelle deps. `nodes`/`edges` sono gli array ReactFlow
  // completi: ogni cambio (drag, selezione, dati) invalidava la
  // callback → l'`useEffect` faceva removeEventListener +
  // addEventListener su `document` ad alta frequenza durante un
  // canvas attivo. Post-fix: refs aggiornati via effect separato;
  // `handleKeyDown` legge lazy `nodesRef.current` / `edgesRef.current`
  // ed e' referenzialmente stabile per tutto il ciclo di vita
  // (deps = [onDeleteNode, onDeleteEdges]).
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
      target.tagName === 'SELECT' ||
      target.isContentEditable
    ) {
      return;
    }

    // Undo/Redo (#24). Cmd/Ctrl+Z = undo; +Shift o Cmd/Ctrl+Y = redo.
    if (event.metaKey || event.ctrlKey) {
      const k = event.key.toLowerCase();
      if (k === 'z') {
        event.preventDefault();
        if (event.shiftKey) onRedo?.(); else onUndo?.();
        return;
      }
      if (k === 'y') {
        event.preventDefault();
        onRedo?.();
        return;
      }
    }

    // Gestisci Delete/Backspace: solo con il fuoco sul canvas (o su nessun
    // controllo). Un tasto premuto su un pulsante del pannello o della vista
    // Passi non deve cancellare il nodo selezionato (Fase 4, M6).
    const onCanvas = target === document.body || target === document.documentElement
      || Boolean(target.closest?.('.react-flow'));
    if ((event.key === 'Delete' || event.key === 'Backspace') && onCanvas) {
      const selectedNodes = nodesRef.current.filter(n => n.selected);
      const selectedEdges0 = edgesRef.current.filter(e => e.selected);
      // Snapshot PRE-delete una sola volta se c'è qualcosa da eliminare, così
      // la cancellazione (nodo o edge) è annullabile con Ctrl+Z.
      if ((selectedNodes.length > 0 || selectedEdges0.length > 0) && onBeforeDelete) {
        onBeforeDelete();
      }
      // Elimina nodi selezionati
      if (selectedNodes.length > 0 && onDeleteNode) {
        selectedNodes.forEach(node => {
          onDeleteNode(node.id);
        });
      }

      // Elimina edges selezionati
      const selectedEdges = edgesRef.current.filter(e => e.selected);
      if (selectedEdges.length > 0 && onDeleteEdges) {
        onDeleteEdges(selectedEdges.map(e => e.id));
      }
    }
  }, [onDeleteNode, onDeleteEdges, onUndo, onRedo, onBeforeDelete]);

  useEffect(() => {
    if (!enabled) return;

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown, enabled]);
}

export default useCanvasKeyboard;
