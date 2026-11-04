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
 *   - JSX dei modal (ExecutionModal, PreviewModal, ...).
 *
 * API semantica:
 *
 *   Top-level (8): projectId, prefix, loading, loadError, project,
 *                  reload, onBack, hexCanvasRef, nodes, edges
 *
 *   `canvasConfig` (oggetto) — configurazione/stato del canvas, statica
 *   per render. Contiene: nodeTypes, edgeTypes, defaultEdgeOptions,
 *   fitView, fitViewOptions, minZoom, maxZoom, backgroundProps,
 *   showMiniMap, miniMapProps, isReadOnly, readOnlyProps, palette,
 *   branchActions, edgeInspector (archi configurabili nel pannello
 *   laterale e nella vista Passi: in Estrai i join, vedi NodeInspector).
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
import EditorToolbar from './EditorToolbar';
import PipelineRunner from './PipelineRunner';
import useLocalDraft, { DRAFT_ORIGIN_LABEL } from '../../hooks/useLocalDraft';
import { useOptionalConfirm } from '../../context/ConfirmContext';
import { CanvasGraphContext } from '../canvas/CanvasGraphContext';
import { analyzeCanvas, structureKey } from '../../utils/canvasGraph';
import { withFlowEdgeLabels } from '../../utils/glossary';

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
    // Blocco A, raccolta b: { kind, icon, matches, entry, Panel } degli archi
    // che si configurano nel pannello laterale (Estrai: i join).
    edgeInspector = null,
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
    // Fase 4, M7: configurazione della barra unica (EditorToolbar). Se c'e',
    // sostituisce `topbar`.
    toolbar,
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

  const confirm = useOptionalConfirm();
  // Copia di sicurezza delle modifiche non salvate (Fase 4, M7; Blocco A
  // regola 8: per utente, progetto e scheda).
  const draft = useLocalDraft(toolbar ? projectId : null, loading || Boolean(loadError) || !project, {
    serverUpdatedAt: project?.metadata?.updated_at || null,
    confirm,
  });

  // Letture dal server (Valida, Anteprima, ...) con modifiche non salvate:
  // l'editor chiede (utils/serverActions.saveBeforeServerRead).
  useEffect(() => {
    if (!confirm) return undefined;
    setEditorActions({
      confirmSaveBeforeRead: (action) => confirm({
        title: 'Modifiche non salvate',
        message: `${action} usa la versione salvata del progetto. Salvo prima le modifiche?`,
        confirmText: 'Salva e continua',
        cancelText: 'Annulla',
      }),
    });
    return () => setEditorActions({ confirmSaveBeforeRead: null });
  }, [confirm, setEditorActions]);

  // Ctrl+S (Fase 4, M7): salva come il pulsante della barra, solo con
  // l'editor pronto e nessuna finestra aperta (i campi di una finestra non
  // sono ancora nel canvas: salvarli a meta' confonderebbe).
  const onSaveRef = useRef(null);
  onSaveRef.current = toolbar && !loading && !loadError && project ? (toolbar.onSave || null) : null;
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 's') return;
      e.preventDefault();
      if (document.querySelector('[role="dialog"], [aria-modal="true"]')) return;
      if (useCanvasStore.getState().isReadOnly || !onSaveRef.current) return;
      // Un campo che conferma all'uscita (titolo, valori) va confermato
      // prima: si esce dal campo e si salva al giro successivo.
      const el = document.activeElement;
      if (el && el !== document.body && typeof el.blur === 'function') el.blur();
      setTimeout(() => onSaveRef.current?.(), 0);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

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
  // Etichette degli archi verso Output e caricamenti dal glossario, anche
  // per gli archi salvati con un nome vecchio (regola 10 del Blocco A).
  const shownEdges = useMemo(() => withFlowEdgeLabels(edges), [edges]);
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
      {toolbar
        ? <EditorToolbar {...toolbar} onAddStep={palette && !isReadOnly ? openPaletteFromSelection : null} />
        : topbar}
      {draft.offer && !isReadOnly && (
        <div className="draft-offer" role="alert">
          <span>Ci sono modifiche non salvate rimaste in questo browser:</span>
          <ul className="draft-offer__list">
            {draft.offer.map(copy => (
              <li key={copy.id} className="draft-offer__item">
                <span>
                  {DRAFT_ORIGIN_LABEL[copy.origin]}, {new Date(copy.savedAt).toLocaleString('it-IT')}.
                  {copy.stale && (
                    <strong> Il progetto è stato salvato dopo: ripristinandole e salvando sostituiresti quella versione.</strong>
                  )}
                </span>
                <button type="button" className="etb-btn etb-btn--primary" onClick={() => draft.restore(copy.id)}>Ripristina</button>
                <button type="button" className="etb-btn" onClick={() => draft.discard(copy.id)}>
                  {copy.origin === 'open' ? 'Nascondi' : 'Scarta'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {draft.notices.map(notice => (
        <div key={notice.id} className="draft-offer draft-offer--notice" role="status">
          <span>{notice.text}</span>
          {notice.id !== 'deleted' && (
            <button type="button" className="etb-btn" onClick={() => draft.dismissNotice(notice.id)}>Chiudi</button>
          )}
        </div>
      ))}
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
            {hexCanvasRef && (
              <canvas
                ref={hexCanvasRef}
                className={`${prefix}-canvas__hex`}
              />
            )}
            <ReactFlow
              nodes={nodes}
              edges={shownEdges}
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
              // Cancellazione SOLO dal nostro useCanvasKeyboard (fuoco sul
              // canvas, annullabile) e da "Elimina passo" (con conferma): la
              // scorciatoia nativa di React Flow cancellava anche con il fuoco
              // su un pulsante del pannello (giro di conferma Blocco A F1).
              deleteKeyCode={null}
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
            {canvasView === 'steps' && <StepsView onAddAfter={palette && !isReadOnly ? openPalette : null} edgeInspector={edgeInspector} />}
            {afterCanvas}
            {children}
            <StepPreview projectId={projectId} />
          </div>
        </CanvasErrorBoundary>
        {/* Nuova interfaccia (Fase 4, M1): nodi compatti sul canvas, form
            completo del nodo selezionato nel pannello a destra. */}
        <NodeInspector fullNodeTypes={nodeTypes} prefix={prefix} branchActions={branchActions} edgeInspector={edgeInspector} />
      </div>
      </CanvasGraphContext.Provider>
      {/* Valida / Anteprima / Esegui: percorso unico, sempre montato (M7). */}
      {toolbar && <PipelineRunner />}
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
