/**
 * useCanvasCore.js — Hook wrapper su useCanvasStore
 *
 * Interfaccia identica a prima per gli editor (nessuna modifica agli editor).
 * Internamente legge e scrive su Zustand invece di useState locale.
 *
 * Responsabilità:
 * - Inizializza lo store con projectId e mode
 * - Mantiene nodesRef/edgesRef aggiornati (per stale closure)
 * - Registra keyboard shortcuts (Delete/Backspace)
 * - Espone dataNodes e getViewportCenter come helpers
 */

import { useRef, useEffect, useCallback, useMemo } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { useCanvasKeyboard } from './useCanvasKeyboard';
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';

/**
 * @param {string} projectId   - ID del progetto corrente
 * @param {Object} options
 * @param {string} options.mode - 'estrai' | 'manipola' | 'arricchisci'
 */
export function useCanvasCore(projectId, { mode = 'manipola' } = {}) {

  const nodes          = useCanvasStore(s => s.nodes);
  const edges          = useCanvasStore(s => s.edges);
  const setNodes       = useCanvasStore(s => s.setNodes);
  const setEdges       = useCanvasStore(s => s.setEdges);
  const onNodesChange  = useCanvasStore(s => s.onNodesChange);
  const onEdgesChange  = useCanvasStore(s => s.onEdgesChange);
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode     = useCanvasStore(s => s.deleteNode);
  const handleSaveQuiet = useCanvasStore(s => s.handleSaveQuiet);
  const init           = useCanvasStore(s => s.init);
  const setReadOnly    = useCanvasStore(s => s.setReadOnly);
  // Undo/redo (#24)
  const undo           = useCanvasStore(s => s.undo);
  const redo           = useCanvasStore(s => s.redo);
  const takeSnapshot   = useCanvasStore(s => s.takeSnapshot);
  const canUndo        = useCanvasStore(s => s.past.length > 0);
  const canRedo        = useCanvasStore(s => s.future.length > 0);

  // Per i viewer (sola lettura) tutte le mutazioni del canvas (deleteNode,
  // updateNodeData, toggle*, *CalculatedColumn, updateColumnAlias,
  // handleSaveQuiet, drag/keyboard delete) sono già no-op grazie al flag
  // isReadOnly nello store. Qui ci limitiamo a settare il flag.
  const { user } = useAuth();
  const isReadOnly = !can.editCanvas(user);
  useEffect(() => { setReadOnly(isReadOnly); }, [isReadOnly, setReadOnly]);

  // Props ReactFlow per la modalità sola lettura. Da spreddare sul componente
  // <ReactFlow> di ogni editor: {...readOnlyProps}. Disabilita drag, connessione
  // di nuovi edge, drop di nuovi nodi e shortcut di cancellazione, ma lascia
  // selezione/pan/zoom/click attivi così il viewer può comunque ispezionare il
  // canvas e cliccare sui nodi per vederne i dettagli.
  const readOnlyProps = useMemo(() => (
    isReadOnly
      ? {
          nodesDraggable: false,
          nodesConnectable: false,
          edgesFocusable: false,
          elementsSelectable: true,
          deleteKeyCode: null,
          // Sovrascrive i callback di mutazione del canvas con no-op
          // così anche se React Flow li chiama internamente non succede nulla.
          onNodesChange: () => {},
          onEdgesChange: () => {},
          onConnect: () => {},
          onDrop: (e) => { e.preventDefault(); },
          onDragOver: (e) => { e.preventDefault(); },
        }
      : {}
  ), [isReadOnly]);

  const reset = useCanvasStore(s => s.reset);

  useEffect(() => {
    reset();                    // pulisce nodi/edges del progetto precedente
    init(projectId, mode);
  }, [projectId, mode, init, reset]);

  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // Disabilitati per i viewer: non devono poter cancellare nulla.
  useCanvasKeyboard({
    nodes,
    edges,
    onDeleteNode: isReadOnly ? () => {} : deleteNode,
    setEdges: isReadOnly ? () => {} : setEdges,
    onUndo: isReadOnly ? () => {} : undo,
    onRedo: isReadOnly ? () => {} : redo,
    onBeforeDelete: isReadOnly ? () => {} : takeSnapshot,
  });


  /** Nodi escludendo annotazioni — per validation, query preview, column calc.
   *
   * Pre-fix (P1 frontend-hooks): `nodes.filter(...)` inline senza useMemo
   * produceva una nuova array identity ad ogni render. EstraiEditor lo usa
   * come dep di un useEffect (`calculateOutputColumnsRecursive`); senza
   * memoization l'effect si rieseguiva ad ogni drag/hover/selection del
   * canvas, chiamando setNodes inutile su ogni interazione utente.
   * Post-fix: useMemo + dep `nodes` → recompute solo quando `nodes`
   * cambia veramente (length o item identity).
   */
  const dataNodes = useMemo(
    () => nodes.filter(n => n.type !== 'comment'),
    [nodes],
  );

  /**
   * Centro del viewport in coordinate canvas.
   * Richiede reactFlowInstance oppure oggetto { screenToFlowPosition }.
   */
  const getViewportCenter = useCallback((reactFlowInstance) => {
    try {
      const fn = reactFlowInstance?.screenToFlowPosition;
      if (!fn) return { x: 400, y: 300 };
      const center = fn({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
      return {
        x: center.x + (Math.random() - 0.5) * 50,
        y: center.y + (Math.random() - 0.5) * 50,
      };
    } catch {
      return { x: 400, y: 300 };
    }
  }, []);

  return {
    // Stato ReactFlow
    nodes,
    edges,
    setNodes,
    setEdges,
    onNodesChange,
    onEdgesChange,

    // Refs
    nodesRef,
    edgesRef,

    // Operazioni nodi
    updateNodeData,
    deleteNode,

    // Undo/redo (#24)
    undo,
    redo,
    canUndo,
    canRedo,
    takeSnapshot,

    // Salvataggio
    handleSaveQuiet,

    // Sola lettura (viewer)
    isReadOnly,
    readOnlyProps,

    // Helpers
    dataNodes,
    getViewportCenter,
  };
}

export default useCanvasCore;
