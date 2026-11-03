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
  // Canali di mutazione espliciti (audit R1): load / utente / derivato.
  const loadGraph         = useCanvasStore(s => s.loadGraph);
  const addNodes          = useCanvasStore(s => s.addNodes);
  const addEdges          = useCanvasStore(s => s.addEdges);
  const removeEdges       = useCanvasStore(s => s.removeEdges);
  const editNodes         = useCanvasStore(s => s.editNodes);
  const editEdges         = useCanvasStore(s => s.editEdges);
  const updateEdge        = useCanvasStore(s => s.updateEdge);
  const patchNodeConfig   = useCanvasStore(s => s.patchNodeConfig);
  const applyDerivedNodes = useCanvasStore(s => s.applyDerivedNodes);
  const applyDerivedEdges = useCanvasStore(s => s.applyDerivedEdges);
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
    onDeleteEdges: isReadOnly ? () => {} : removeEdges,
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
   * canvas, rieseguendo il calcolo colonne su ogni interazione utente.
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
  const getViewportCenter = useCallback(() => {
    // Calcolo DOM-only del centro della vista corrente in coordinate flow:
    // leggiamo la transform CSS applicata da React Flow a `.react-flow__viewport`
    // (matrix(zoom,0,0,zoom,tx,ty)) + le dimensioni del pannello `.react-flow`.
    // Non dipende da screenToFlowPosition/instance → funziona in tutti gli editor.
    const HALF_W = 120;
    const HALF_H = 55;
    const jitter = () => (Math.random() - 0.5) * 40;
    try {
      const pane = document.querySelector('.react-flow');
      const vpEl = document.querySelector('.react-flow__viewport');
      if (!pane || !vpEl) return { x: 400, y: 300 };
      const rect = pane.getBoundingClientRect();
      const m = new DOMMatrixReadOnly(getComputedStyle(vpEl).transform);
      const zoom = m.a || 1;     // scaleX
      const tx = m.e;            // translateX (px)
      const ty = m.f;            // translateY (px)
      // Centro visivo del pannello (coord schermo relative al pannello) → flow.
      const cx = (rect.width / 2 - tx) / zoom;
      const cy = (rect.height / 2 - ty) / zoom;
      // React Flow posiziona il nodo dal suo angolo TOP-LEFT: sottraiamo metà
      // di un nodo "tipico" così il CORPO risulta centrato, non lo spigolo.
      return { x: cx - HALF_W + jitter(), y: cy - HALF_H + jitter() };
    } catch {
      return { x: 400, y: 300 };
    }
  }, []);

  return {
    // Stato ReactFlow
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,

    // Refs
    nodesRef,
    edgesRef,

    // Mutazioni (vedi canali in useCanvasStore)
    loadGraph,
    addNodes,
    addEdges,
    removeEdges,
    editNodes,
    editEdges,
    updateEdge,
    patchNodeConfig,
    applyDerivedNodes,
    applyDerivedEdges,
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
