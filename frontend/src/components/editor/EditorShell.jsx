/**
 * EditorShell.jsx — Layout/guard scaffold condiviso da EstraiEditor /
 * ManipolaEditor / ArricchisciEditor.
 *
 * Cosa GESTISCE direttamente:
 *   - Security guard `isValidProjectId(projectId)` con redirect a '/' se KO.
 *   - 3 branch di early return: loading (LoadingOverlay), loadError (banner
 *     "Errore caricamento" con bottoni Riprova / Torna alla lista),
 *     !project (banner "Progetto non trovato" con bottone Torna).
 *   - Wrapper canvas: CanvasErrorBoundary + div `${prefix}-canvas` (con
 *     classe canvas-readonly opt-in) + ReactFlow + Background + Controls
 *     + MiniMap.
 *   - Layout root: `${prefix}-editor` > topbar > beforeCanvas > main >
 *     (sidebar | canvas) > afterCanvas (dentro canvas) > modals.
 *
 * Cosa NON gestisce (responsabilita' del consumer):
 *   - Hook applicativi (useEditorData / useCanvasCore / usePreview / ...).
 *   - JSX della topbar (logo, back, connection badge, save, theme, ...).
 *   - JSX della sidebar (search, catalog tree, ...).
 *   - JSX dei modal (JoinModal, ExecutionModal, ...).
 *
 * API semantica:
 *
 *   Top-level (8): projectId, prefix, loading, loadError, project,
 *                  reload, onBack, hexCanvasRef, nodes, edges
 *
 *   `canvasConfig` (oggetto) — configurazione/stato del canvas, statica
 *   per render. Contiene: nodeTypes, edgeTypes, defaultEdgeOptions,
 *   fitView, fitViewOptions, minZoom, maxZoom, backgroundProps,
 *   showMiniMap, miniMapProps, isReadOnly, readOnlyProps.
 *
 *   `canvasHandlers` (oggetto) — callback canvas, bindabili runtime.
 *   Contiene: onNodesChange, onEdgesChange, onConnect, onEdgeClick,
 *   onEdgeContextMenu, onInit, onDrop, onDragOver.
 *
 *   `slots` (oggetto) — render slots posizionali. Contiene: topbar,
 *   sidebar, beforeCanvas, afterCanvas, modals, children.
 *
 *   Note su `slots.children`: e' un punto di estensione esplicito,
 *   renderizzato DENTRO il `CanvasErrorBoundary` e nel canvas div
 *   subito dopo `afterCanvas`. Serve per overlay che devono vivere
 *   nel layer canvas (es. tooltip globali, layer geo overlay) e non
 *   sono esprimibili tramite gli altri slot. Coperto da contract
 *   test (`renders all slots ...inside the canvas error boundary`).
 *
 *   Note su `canvasConfig.edgeTypes`: e' forwarded direttamente al
 *   componente `<ReactFlow edgeTypes={...}>`. Estensione standard
 *   ReactFlow per renderer custom di edge; coperto da contract test
 *   (`forwards canvasConfig.edgeTypes to ReactFlow`).
 *
 *   Banner di errore (loadError, !project): `role="alert"` +
 *   `aria-live="assertive"` + focus iniziale sul primo bottone
 *   azione. Vedi `ErrorBanner` interno.
 *
 * Behavior preservato byte-per-byte rispetto al pattern legacy.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ReactFlow, Background, Controls, ControlButton, MiniMap, useReactFlow } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';

import { isValidProjectId } from '../../utils/validateRouteParam';
import { CanvasErrorBoundary } from '../common/ErrorBoundary';
import { LoadingOverlay } from '../common/LoadingOverlay';
import NodeInspector from '../canvas/NodeInspector';
import { compactNodeTypes } from '../canvas/compactNodeTypes';
import NodePalette from '../canvas/NodePalette';
import StepPreview from '../canvas/StepPreview';
import { autoLayout } from '../canvas/autoLayout';
import { nodeTitle } from '../canvas/nodeDescriptors';
import StepsView from '../canvas/StepsView';
import { CanvasGraphContext } from '../canvas/CanvasGraphContext';
import { analyzeCanvas, structureKey } from '../../utils/canvasGraph';

const EMPTY_OBJ = {};

/**
 * Banner errore con semantica accessibile: `role="alert"` + `aria-live`
 * "assertive" cosi' gli screen reader annunciano subito il messaggio,
 * e focus iniziale sul primo bottone azione per dare un punto di
 * tastiera immediato.
 */
function ErrorBanner({ prefix, message, children }) {
  const firstBtnRef = useRef(null);
  useEffect(() => {
    firstBtnRef.current?.focus();
  }, []);
  // Cloniamo solo il primo bottone per agganciare la ref senza imporre
  // a chi consuma di passare ref dall'esterno.
  let attachedFirst = false;
  const enhanced = React.Children.map(children, (child) => {
    if (!attachedFirst && React.isValidElement(child) && child.type === 'button') {
      attachedFirst = true;
      return React.cloneElement(child, { ref: firstBtnRef });
    }
    return child;
  });
  return (
    <div className={`${prefix}-error`} role="alert" aria-live="assertive">
      <p className={`${prefix}-error__message`}>{message}</p>
      {enhanced}
    </div>
  );
}

function EditorShell({
projectId,
  prefix,
  loading = false,
  loadError = null,
  project,
  reload,
  onBack,
nodes = [],
  edges = [],
  hexCanvasRef,
canvasConfig = EMPTY_OBJ,
  canvasHandlers = EMPTY_OBJ,
  slots = EMPTY_OBJ,
}) {
  const navigate = useNavigate();

  const {
    nodeTypes,
    edgeTypes,
    defaultEdgeOptions,
    fitView = false,
    fitViewOptions,
    minZoom,
    maxZoom,
    backgroundProps = { color: 'transparent', gap: 16 },
    showMiniMap = true,
    miniMapProps = { zoomable: true, pannable: true },
    isReadOnly = false,
    readOnlyProps,
    // Nuova interfaccia (Fase 4, M2): { groups, onPick(item, afterNodeId) }
    palette,
    // Fase 4, M6: { connectBranch?(endId, targetId, mode), connectModes?,
    // duplicateBranch?(sourceId, otherSourceId) } dall'editor.
    branchActions,
  } = canvasConfig;

  const {
    onNodesChange,
    onEdgesChange,
    onConnect,
    onEdgeClick,
    onEdgeContextMenu,
    onInit,
    onDrop,
    onDragOver,
  } = canvasHandlers;

  const {
    topbar,
    sidebar,
    beforeCanvas,
    afterCanvas,
    modals,
    children,
  } = slots;

  // Undo/redo (#24): snapshot pre-interazione su drag-start e connessione,
  // così spostamenti e nuovi edge sono annullabili con Ctrl+Z. takeSnapshot
  // è no-op in sola lettura e dedup-safe.
  const takeSnapshot = useCanvasStore(s => s.takeSnapshot);
  const handleConnect = (conn) => { takeSnapshot(); onConnect?.(conn); };

  // ── Nuova interfaccia (Fase 4, M2) ────────────────────────────────────
  const editNodes = useCanvasStore(s => s.editNodes);
  const setEditorActions = useCanvasStore(s => s.setEditorActions);
  const reactFlow = useReactFlow();
  // Palette: { afterId } quando aperta (afterId null = canvas vuoto o
  // nessun nodo selezionato), null quando chiusa.
  const [paletteState, setPaletteState] = useState(null);
  const paletteAfter = paletteState?.afterId || null;

  // "＋" sul nodo compatto: apre la palette per aggiungere il passo successivo.
  const openPalette = useCallback((afterNodeId = null) => {
    if (!palette || isReadOnly) return;
    setPaletteState({ afterId: afterNodeId });
  }, [palette, isReadOnly]);

  // Dal pulsante dei controlli o da tastiera ("/" o "A"): dopo il nodo
  // selezionato se ce n'e' uno solo, altrimenti un passo libero.
  const openPaletteFromSelection = useCallback(() => {
    const selected = useCanvasStore.getState().nodes.filter(n => n.selected && n.type !== 'comment');
    openPalette(selected.length === 1 ? selected[0].id : null);
  }, [openPalette]);
  useEffect(() => {
    if (!palette || isReadOnly) return undefined;
    // Solo con il fuoco sul canvas (o su nessun controllo): un tasto premuto
    // nel pannello, in una modale o su un pulsante non apre la palette.
    const onKey = (e) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key !== '/' && e.key !== 'a' && e.key !== 'A') return;
      const t = e.target;
      const onCanvas = t === document.body || t === document.documentElement || Boolean(t?.closest?.('.react-flow'));
      if (!onCanvas || t?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t?.tagName || '')) return;
      if (document.querySelector('[role="dialog"]')) return;
      e.preventDefault();
      openPaletteFromSelection();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [palette, isReadOnly, openPaletteFromSelection]);
  useEffect(() => {
    setEditorActions({ openPalette: palette && !isReadOnly ? openPalette : null });
  }, [setEditorActions, openPalette, palette, isReadOnly]);

  // Riordina: layout automatico come azione utente (annullabile, salvata).
  const handleAutoLayout = useCallback(() => {
    if (isReadOnly) return;
    editNodes(current => autoLayout(current, edges));
    requestAnimationFrame(() => reactFlow.fitView({ padding: 0.15, duration: 300 }));
  }, [isReadOnly, editNodes, edges, reactFlow]);

  const paletteAfterNode = paletteAfter ? nodes.find(n => n.id === paletteAfter) : null;

  // Fase 4, M6: vista Passi e rami scollegati (utils/canvasGraph).
  const canvasView = useCanvasStore(s => s.canvasView);
  const setCanvasView = useCanvasStore(s => s.setCanvasView);
  const focusNodeId = useCanvasStore(s => s.focusNodeId);
  // Analisi del flusso solo quando cambia la struttura (non a ogni drag).
  const graphKey = useCanvasStore(s => structureKey(s.nodes, s.edges));
  const graph = useMemo(() => {
    const st = useCanvasStore.getState();
    return { key: graphKey, ...analyzeCanvas(st.nodes, st.edges) };
  }, [graphKey]);
  const selectedId = useCanvasStore(s => {
    const sel = s.nodes.filter(n => n.selected);
    return sel.length === 1 ? sel[0].id : null;
  });
  // "Vedi nel canvas": centra il passo scelto (con margine, sopra
  // l'anteprima); l'evidenziazione si spegne da sola.
  useEffect(() => {
    if (!focusNodeId) return undefined;
    if (canvasView === 'canvas') {
      requestAnimationFrame(() => reactFlow.fitView?.({ nodes: [{ id: focusNodeId }], maxZoom: 1.2, padding: 0.6, duration: 300 }));
    }
    const t = setTimeout(() => useCanvasStore.getState().setCanvasView(useCanvasStore.getState().canvasView, null), 1600);
    return () => clearTimeout(t);
  }, [canvasView, focusNodeId, reactFlow]);

  // Security guard: redirect a '/' se projectId non valido.
  useEffect(() => {
    if (!isValidProjectId(projectId)) {
      console.error('[Security] projectId non valido, redirect a home:', projectId);
      navigate('/', { replace: true });
    }
  }, [projectId, navigate]);

  if (!isValidProjectId(projectId)) return null;

  if (loading) {
    return <LoadingOverlay message="Caricamento progetto..." />;
  }

  if (loadError) {
    return (
      <ErrorBanner prefix={prefix} message={`Errore caricamento: ${loadError}`}>
        {reload && (
          <button type="button" className={`${prefix}-error__btn`} onClick={reload}>
            Riprova
          </button>
        )}
        {onBack && (
          <button type="button" className={`${prefix}-error__btn`} onClick={onBack}>
            Torna alla lista
          </button>
        )}
      </ErrorBanner>
    );
  }

  if (!project) {
    return (
      <ErrorBanner prefix={prefix} message="Progetto non trovato">
        {onBack && (
          <button type="button" className={`${prefix}-error__btn`} onClick={onBack}>
            Torna alla lista
          </button>
        )}
      </ErrorBanner>
    );
  }

  const canvasClassName = `${prefix}-canvas${isReadOnly ? ' canvas-readonly' : ''}`;

  return (
    <div className={`${prefix}-editor`}>
      {topbar}
      {beforeCanvas}
      <CanvasGraphContext.Provider value={graph}>
      <div className={`${prefix}-main`}>
        {sidebar}
        <CanvasErrorBoundary>
          {/* Drop handler sul canvas div (gli eventi bubble da ReactFlow). */}
          <div
            className={canvasClassName}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
            <div className="canvas-view-toggle" role="group" aria-label="Vista">
              {/* Cambiando vista resta in primo piano il passo selezionato. */}
              <button type="button" aria-pressed={canvasView === 'canvas'} onClick={() => setCanvasView('canvas', selectedId)}>
                <span aria-hidden="true">◇ </span>Canvas
              </button>
              <button type="button" aria-pressed={canvasView === 'steps'} onClick={() => setCanvasView('steps', selectedId)}>
                <span aria-hidden="true">☰ </span>Passi
              </button>
            </div>
            {hexCanvasRef && (
              <canvas
                ref={hexCanvasRef}
                className={`${prefix}-canvas__hex`}
              />
            )}
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={handleConnect}
              onNodeDragStart={takeSnapshot}
              onEdgeClick={onEdgeClick}
              onEdgeContextMenu={onEdgeContextMenu}
              onInit={onInit}
              nodeTypes={compactNodeTypes(nodeTypes)}
              edgeTypes={edgeTypes}
              defaultEdgeOptions={defaultEdgeOptions}
              fitView={fitView}
              fitViewOptions={fitViewOptions}
              minZoom={minZoom}
              maxZoom={maxZoom}
              {...(readOnlyProps || {})}
            >
              <Background {...backgroundProps} />
              <Controls>
                {palette && !isReadOnly && (
                  <ControlButton onClick={openPaletteFromSelection} title="Aggiungi un passo (/ o A)" aria-label="Aggiungi un passo">
                    ＋
                  </ControlButton>
                )}
                {!isReadOnly && (
                  <ControlButton onClick={handleAutoLayout} title="Riordina i nodi automaticamente" aria-label="Riordina i nodi">
                    ⤢
                  </ControlButton>
                )}
              </Controls>
              {showMiniMap && <MiniMap {...miniMapProps} />}
            </ReactFlow>
            {canvasView === 'steps' && <StepsView onAddAfter={palette && !isReadOnly ? openPalette : null} />}
            {afterCanvas}
            {children}
            <StepPreview projectId={projectId} />
          </div>
        </CanvasErrorBoundary>
        {/* Nuova interfaccia (Fase 4, M1): nodi compatti sul canvas, form
            completo del nodo selezionato nel pannello a destra. */}
        <NodeInspector fullNodeTypes={nodeTypes} prefix={prefix} branchActions={branchActions} />
      </div>
      </CanvasGraphContext.Provider>
      {modals}
      {palette && (
        <NodePalette
          open={Boolean(paletteState)}
          context={paletteAfterNode ? nodeTitle(paletteAfterNode.type, paletteAfterNode.data) : null}
          groups={palette.groups}
          onPick={(item) => palette.onPick(item, paletteAfter)}
          onClose={() => setPaletteState(null)}
        />
      )}
    </div>
  );
}

export default EditorShell;
