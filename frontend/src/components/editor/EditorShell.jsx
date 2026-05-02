/**
 * EditorShell.jsx — Layout/guard scaffold condiviso da EstraiEditor /
 * ManipolaEditor / ArricchisciEditor (FE-SPLIT-004).
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
 * API semantica (FE-SPLIT-004 cleanup):
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

import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactFlow, { Background, Controls, MiniMap } from 'reactflow';

import { isValidProjectId } from '../../utils/validateRouteParam';
import { CanvasErrorBoundary } from '../common/ErrorBoundary';
import { LoadingOverlay } from '../common/LoadingOverlay';

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
  // ── Guards ─────────────────────────────────────────────────────────
  projectId,
  prefix,
  loading = false,
  loadError = null,
  project,
  reload,
  onBack,

  // ── ReactFlow data + visual ref ────────────────────────────────────
  nodes = [],
  edges = [],
  hexCanvasRef,

  // ── Grouped API ────────────────────────────────────────────────────
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
      <div className={`${prefix}-main`}>
        {sidebar}
        <CanvasErrorBoundary>
          {/* Drop handler sul canvas div (gli eventi bubble da ReactFlow). */}
          <div
            className={canvasClassName}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
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
              onConnect={onConnect}
              onEdgeClick={onEdgeClick}
              onEdgeContextMenu={onEdgeContextMenu}
              onInit={onInit}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              defaultEdgeOptions={defaultEdgeOptions}
              fitView={fitView}
              fitViewOptions={fitViewOptions}
              minZoom={minZoom}
              maxZoom={maxZoom}
              {...(readOnlyProps || {})}
            >
              <Background {...backgroundProps} />
              <Controls />
              {showMiniMap && <MiniMap {...miniMapProps} />}
            </ReactFlow>
            {afterCanvas}
            {children}
          </div>
        </CanvasErrorBoundary>
      </div>
      {modals}
    </div>
  );
}

export default EditorShell;
