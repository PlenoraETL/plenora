/**
 * EditorShell.jsx — Layout/guard scaffold condiviso da EstraiEditor /
 * ManipolaEditor / ArricchisciEditor.
 *
 * Cosa GESTISCE direttamente:
 *   - Security guard `isValidProjectId(projectId)` con redirect a '/' se KO.
 *   - 3 branch di early return: loading (LoadingOverlay), loadError (banner
 *     "Errore caricamento" con bottoni Riprova / Torna alla lista),
 *     !project (banner "Progetto non trovato" con bottone Torna).
 *   - Wrapper canvas: CanvasErrorBoundary + div `editor-shell__canvas
 *     ${prefix}-canvas` (con classe canvas-readonly opt-in) + ReactFlow +
 *     Background + Controls + MiniMap.
 *   - Layout root: `editor-shell` > topbar > beforeCanvas > main >
 *     (sidebar | canvas) > afterCanvas (dentro canvas) > modals.
 *   - Impaginazione: classi STATICHE del guscio (`editor-shell`,
 *     `__main`, `__canvas`, `__hex`, `__error`, `__error-message`,
 *     `__error-btn`) con le regole comuni in EditorShell.css: il layout non
 *     dipende da nomi composti col prefisso. Accanto, tre classi col
 *     prefisso per le varianti del singolo editor nel suo CSS (aspetto di
 *     12e33b9d): `${prefix}-editor` (radice: Estrai 13px), `${prefix}-canvas`
 *     (archi, controlli) e `${prefix}-error` (schermata d'errore).
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
 *                  reload, onBack, hexCanvasRef. Il grafo NON passa dalle
 *                  props (UI-2, V4): lo legge FlowCanvas dallo store, cosi'
 *                  un trascinamento ridisegna solo il canvas, non l'editor
 *                  ne' il guscio.
 *
 *   `canvasConfig` (oggetto) — configurazione/stato del canvas, statica
 *   per render. Contiene: nodeTypes, edgeTypes, defaultEdgeOptions,
 *   fitView, fitViewOptions, minZoom, maxZoom, backgroundProps,
 *   showMiniMap, miniMapProps, isReadOnly, readOnlyProps, palette,
 *   branchActions, edgeInspector (archi configurabili nel pannello
 *   laterale e nella vista Passi: in Estrai i join, vedi NodeInspector),
 *   presentEdges(edges, nodes) (aspetto degli archi dal contenuto: in
 *   Estrai lo stato dei join; ricalcolato solo quando il contenuto cambia).
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

import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ReactFlow, Background, Controls, ControlButton, MiniMap, useReactFlow } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';

import { isValidProjectId } from '../../utils/validateRouteParam';
import { CanvasErrorBoundary } from '../common/ErrorBoundary';
import { LoadingOverlay } from '../common/LoadingOverlay';
import NodeInspector from '../canvas/NodeInspector';
import { SidePanelFrame, PanelResizer } from './EditorPanels';
import { compactNodeTypes } from '../canvas/compactNodeTypes';
import NodePalette from '../canvas/NodePalette';
import StepPreview from '../canvas/StepPreview';
import { nodeTitle } from '../canvas/nodeDescriptors';
import StepsView from '../canvas/StepsView';
import EditorToolbar from './EditorToolbar';
import PipelineRunner from './PipelineRunner';
import SaveConflictNotice from './SaveConflictNotice';
import useLocalDraft, { DRAFT_ORIGIN_LABEL } from '../../hooks/useLocalDraft';
import { useColumnSync } from '../../hooks/useColumns';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';
import { useOptionalConfirm } from '../../context/ConfirmContext';
import { CanvasGraphContext } from '../canvas/CanvasGraphContext';
import { analyzeCanvas, structureKey } from '../../utils/canvasGraph';
import { withFlowEdgeLabels } from '../../utils/glossary';
import { isShortcutBlocked, isTypingTarget } from '../../utils/dialogStack';
import { matchesShortcut } from '../../utils/shortcuts';
import { requestCanvasDeletion } from '../../utils/canvasDeletion';
import { formatDateTime } from '../../utils/format';
import './EditorShell.css';

const EMPTY_OBJ = {};
const NO_EDGES = [];

/**
 * FlowCanvas — l'UNICO componente iscritto a `nodes`/`edges` interi (UI-2,
 * V4; test tests/shared/canvasRender.contract): disegna React Flow e si
 * ridisegna a ogni fotogramma di un trascinamento, da solo. Gli archi
 * mostrati (etichette del glossario, aspetto dei join) si ricalcolano solo
 * quando cambiano gli archi o il contenuto, non quando si sposta un nodo.
 */
const FlowCanvas = memo(function FlowCanvas({ presentEdges, flowProps, children }) {
  const nodes = useCanvasStore(s => s.nodes);
  const edges = useCanvasStore(s => s.edges);
  const contentRev = useCanvasStore(s => s.contentRev);
  const shownEdges = useMemo(() => {
    const presented = presentEdges ? presentEdges(edges || NO_EDGES, useCanvasStore.getState().nodes) : edges;
    return withFlowEdgeLabels(presented);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edges, contentRev, presentEdges]);
  return (
    <ReactFlow nodes={nodes} edges={shownEdges} {...flowProps}>
      {children}
    </ReactFlow>
  );
});

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
    <div className={`editor-shell__error ${prefix}-error`} role="alert" aria-live="assertive">
      <p className="editor-shell__error-message">{message}</p>
      {enhanced}
    </div>
  );
}

function EditorShell({
projectId,
  // Utente dell'editor: le opzioni dello staff nella finestra «Prima di avviare».
  user = null,
  prefix,
  loading = false,
  loadError = null,
  project,
  reload,
  onBack,
  hexCanvasRef,
canvasConfig = EMPTY_OBJ,
  canvasHandlers = EMPTY_OBJ,
  slots = EMPTY_OBJ,
}) {
  const navigate = useNavigate();
  // Fonte unica delle colonne (Blocco A, regola 4): il guscio monta l'UNICA
  // sottoscrizione che ricalcola la mappa dell'editor sul canvas dell'editor.
  useColumnSync(isValidProjectId(projectId) ? projectId : null);

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
    presentEdges = null,
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
  // Callback stabili per React Flow (UI-2, V8): l'ultima onConnect via ref.
  const onConnectRef = useRef(onConnect);
  onConnectRef.current = onConnect;
  const handleConnect = useCallback((conn) => { takeSnapshot(); onConnectRef.current?.(conn); }, [takeSnapshot]);
  const handleNodeDragStart = useCallback(() => takeSnapshot(), [takeSnapshot]);

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
      if (!matchesShortcut('add_step', e)) return;
      const t = e.target;
      const onCanvas = t === document.body || t === document.documentElement || Boolean(t?.closest?.('.react-flow'));
      if (!onCanvas || isTypingTarget(t)) return;
      // Regola unica delle scorciatoie: niente sotto una finestra aperta.
      if (isShortcutBlocked(e)) return;
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
  // Tasto destro su un arco = elimina, con la conferma di ogni cancellazione
  // dal canvas (utils/canvasDeletion, R1.7); poi resta annullabile.
  const handleEdgeContextMenu = useCallback(async (event, edge) => {
    event.preventDefault();
    if (!onEdgeContextMenu) return;
    await requestCanvasDeletion({ confirm, edges: [edge], perform: () => onEdgeContextMenu(event, edge) });
  }, [onEdgeContextMenu, confirm]);
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
      if (!matchesShortcut('save', e)) return;
      const blocked = isShortcutBlocked(e);
      e.preventDefault();
      if (blocked) return;
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
  // dagre si scarica solo al primo «Riordina» (UI-4 V2: fuori dal chunk
  // dell'editor); un chunk mancante dopo un deploy lo gestisce chunkReload.
  const handleAutoLayout = useCallback(() => {
    if (isReadOnly) return;
    import('../canvas/autoLayout').then(({ autoLayout }) => {
      editNodes(current => autoLayout(current, useCanvasStore.getState().edges));
      requestAnimationFrame(() => reactFlow.fitView({ padding: 0.15, duration: 300 }));
    }).catch(() => { /* chunk non caricato: utils/chunkReload */ });
  }, [isReadOnly, editNodes, reactFlow]);

  // Titolo del passo da cui si apre la palette (ricalcolato col contenuto).
  const paletteContext = useCanvasDerived((s) => {
    const n = paletteAfter ? (s.nodes || []).find(x => x.id === paletteAfter) : null;
    return n ? nodeTitle(n.type, n.data) : null;
  }, { deps: [paletteAfter] });

  // Fase 4, M6: vista Passi e rami scollegati (utils/canvasGraph).
  const canvasView = useCanvasStore(s => s.canvasView);
  const setCanvasView = useCanvasStore(s => s.setCanvasView);
  const focusNodeId = useCanvasStore(s => s.focusNodeId);
  // Analisi del flusso solo quando cambia la struttura (non a ogni drag):
  // la firma si rilegge solo quando sale il contatore del contenuto
  // (hooks/useCanvasDerived), l'analisi solo se la firma cambia.
  // Le etichette degli archi verso Output e caricamenti (glossario, regola
  // 10 del Blocco A) le applica FlowCanvas.
  const graphKey = useCanvasDerived(s => structureKey(s.nodes, s.edges));
  const graph = useMemo(() => {
    const st = useCanvasStore.getState();
    return { key: graphKey, ...analyzeCanvas(st.nodes, st.edges) };
  }, [graphKey]);
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
          <button type="button" className="editor-shell__error-btn" onClick={reload}>
            Riprova
          </button>
        )}
        {onBack && (
          <button type="button" className="editor-shell__error-btn" onClick={onBack}>
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
          <button type="button" className="editor-shell__error-btn" onClick={onBack}>
            Torna alla lista
          </button>
        )}
      </ErrorBanner>
    );
  }

  const canvasClassName = `editor-shell__canvas ${prefix}-canvas${isReadOnly ? ' canvas-readonly' : ''}`;
  const flowProps = {
    onNodesChange,
    onEdgesChange,
    onConnect: handleConnect,
    onNodeDragStart: handleNodeDragStart,
    onEdgeClick,
    onEdgeContextMenu: onEdgeContextMenu ? handleEdgeContextMenu : undefined,
    onInit,
    nodeTypes: compactNodeTypes(nodeTypes),
    edgeTypes,
    defaultEdgeOptions,
    fitView,
    fitViewOptions,
    minZoom,
    maxZoom,
    // Cancellazione SOLO dal nostro useCanvasKeyboard (fuoco sul canvas,
    // annullabile) e da "Elimina passo" (con conferma): la scorciatoia
    // nativa di React Flow cancellava anche con il fuoco su un pulsante del
    // pannello (giro di conferma Blocco A F1).
    deleteKeyCode: null,
    ...(readOnlyProps || {}),
  };

  return (
    <div className={`editor-shell ${prefix}-editor`}>
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
                  {DRAFT_ORIGIN_LABEL[copy.origin]}, {formatDateTime(copy.savedAt)}.
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
      {/* Salvataggio superato da un'altra scheda (Blocco A, regola 8). */}
      {toolbar && <SaveConflictNotice />}
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
      <div className="editor-shell__main">
        {/* UI-4 (U26): barra richiudibile, pannello del passo ridimensionabile. */}
        {sidebar && <SidePanelFrame>{sidebar}</SidePanelFrame>}
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
                className="editor-shell__hex"
              />
            )}
            <FlowCanvas presentEdges={presentEdges} flowProps={flowProps}>
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
            </FlowCanvas>
            {canvasView === 'steps' && <StepsView onAddAfter={palette && !isReadOnly ? openPalette : null} edgeInspector={edgeInspector} />}
            {afterCanvas}
            {children}
            <StepPreview projectId={projectId} />
          </div>
        </CanvasErrorBoundary>
        {/* Nuova interfaccia (Fase 4, M1): nodi compatti sul canvas, form
            completo del nodo selezionato nel pannello a destra. */}
        <PanelResizer />
        <NodeInspector fullNodeTypes={nodeTypes} prefix={prefix} branchActions={branchActions} edgeInspector={edgeInspector} />
      </div>
      </CanvasGraphContext.Provider>
      {/* Valida / Anteprima / Esegui: percorso unico, sempre montato (M7). */}
      {toolbar && <PipelineRunner user={user} />}
      {modals}
      {palette && (
        <NodePalette
          open={Boolean(paletteState)}
          context={paletteContext}
          groups={palette.groups}
          notice={palette.notice || null}
          onPick={(item) => palette.onPick(item, paletteAfter)}
          onClose={() => setPaletteState(null)}
        />
      )}
    </div>
  );
}

// UI-2 (V4): il guscio non dipende dal grafo; si ridisegna con l'editor o
// con le sue iscrizioni (struttura, vista), mai per una posizione.
export default memo(EditorShell);
