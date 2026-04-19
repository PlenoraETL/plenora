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

  // ─── Store ───
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

  // ─── Init store ───
  useEffect(() => {
    reset();                    // pulisce nodi/edges del progetto precedente
    init(projectId, mode);
  }, [projectId, mode, init, reset]);

  // ─── Refs aggiornati (evitano stale closure nelle callback async) ───
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // ─── Keyboard shortcuts (Delete/Backspace) ───
  // Disabilitati per i viewer: non devono poter cancellare nulla.
  useCanvasKeyboard({
    nodes,
    edges,
    onDeleteNode: isReadOnly ? () => {} : deleteNode,
    setEdges: isReadOnly ? () => {} : setEdges,
  });

  // ─── Helpers ───

  /** Nodi escludendo annotazioni — per validation, query preview, column calc */
  const dataNodes = nodes.filter(n => n.type !== 'comment');

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