/**
 * ManipolaEditor.jsx — Editor visuale per il modulo MANIPOLA
 *
 * Permette di costruire pipeline di trasformazione dati collegando:
 *  - Nodi sorgente (progetti Estrai, file)
 *  - Nodi Transform (33+ operazioni: join, filter, pivot, reproject, dissolve...)
 *  - Nodo Multi-Join (N sorgenti)
 *  - Nodo Output
 *  - Nodo Carica DB
 *
 * Hooks usati: useEditorData, useCanvasCore, useEdgeHandlers,
 *              useExecution, usePreview, useToast. Le colonne vengono dalla
 *              fonte unica (utils/columnSource, montata da EditorShell).
 *
 * Route: /editor/manipola/:id
 */
import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { useCanvasDerived } from '../hooks/useCanvasDerived';
import { sinkFlags } from '../utils/canvasGraph';
import { ConfirmProvider } from '../context/ConfirmContext';
import ProjectSourceNode from '../components/nodes/ProjectSourceNode';
import FileSourceNode from '../components/nodes/FileSourceNode';
import ProjectInputNode from '../components/nodes/ProjectInputNode';
import TransformNode from '../components/transforms/TransformNode';
import MultiJoinNode from '../components/transforms/MultiJoinNode';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';
import EditorSidebar from '../components/panels/EditorSidebar';

// Modali lazy-loaded
const PreviewModal = lazyWindow(() => import('../components/modals/PreviewModal'), term('preview').name);
const ExecutionModal = lazyWindow(() => import('../components/modals/ExecutionModal'), 'Esecuzione');
const CanvasVersionsModal = lazyWindow(() => import('../components/modals/CanvasVersionsModal'), 'Versioni');
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import DataSourceNode from '../components/nodes/DataSourceNode';
import { executionModalProps, executionToolbarProps } from '../hooks/useExecution';
import { usePreview, useExecution, useCanvasCore, useCanvasSave, useUnsavedChangesGuard, useValidationHighlights } from '../hooks';
import { useEditorData } from '../hooks/useEditorData';
import useCanvasStore from '../store/useCanvasStore';
import { serverVersionOf } from '../utils/canvasVersion';
import { analyzeCanvas, branchSteps, nodeRole } from '../utils/canvasGraph';
import { connectStepData, cloneStepData } from '../utils/branchNodes';
import { nodeTitle } from '../components/canvas/nodeDescriptors';
import { joinKeysPatch } from '../components/transforms/panels/panelConfig';
import {
  createParameterBindingEdge,
  dataFlowEdges,
  hydrateParameterBindingEdges,
  isParameterBindingEdge,
  isScalarProjectInputNode,
} from '../utils/runtimeBindingEdges';
import ValidationStatusBadge from '../components/editor/ValidationStatusBadge';
import { useToast } from '../hooks/useToast';
import { useTheme } from '../hooks/useTheme';
import { getColumnsOf } from '../hooks/useColumns';
import { deserializeNodes } from '../utils/nodeFactories';
import { beforePreview, saveForRun, saveNow } from '../utils/serverActions';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import '../styles/ManipolaEditor.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/nodes/AnnotationNode.css';
import '../components/nodes/DataSourceNode.css';
import { useAuth } from '../context/AuthContext';
import { lazyWindow } from '../components/common/LazyWindow';
import { useHexCanvas } from '../utils/hexCanvas';
import { menuEntry, term } from '../utils/glossary';
import { buildManipolaPaletteGroups } from '../components/canvas/manipolaPalette';
import { isMultiInputOp } from '../utils/transformRegistry';
import { useTransformRegistry } from '../hooks/useTransformRegistry';
import useEditorActions from '../components/editor/useEditorActions';
import useCatalogAdd, { useResolvedSourceNames } from '../components/editor/useCatalogAdd';
import { catalogConstraintNodes, catalogMenuItems, isCanvasEmpty, readSourceDrop, sourcePaletteGroups } from '../components/editor/editorNodeCatalog';
import EditorFileUploadModal from '../components/editor/EditorFileUploadModal';
import { useEditorFileUpload } from '../components/editor/editorUpload';
import { newJoinType } from '../utils/joinTypes';

// Costante stabile — evita array literal nelle deps di useEditorData
const MANIPOLA_SOURCE_TYPES = ['estrai', 'manipola', 'arricchisci'];

const nodeTypes = {
  project_source: ProjectSourceNode, file_source: FileSourceNode,
  project_input: ProjectInputNode,
  transform: TransformNode, multi_join: MultiJoinNode,
  result: ResultNode, staticValues: StaticValuesNode,
  carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode, comment: AnnotationNode,
  // React Flow `nodeTypes`: la chiave e' il tipo di rendering del registro
  // (nodeTypeRegistry.renderNodeType), lo stesso del deserializer e dei nodi
  // appena aggiunti (Fase 5, T6, R2.6: prima i nuovi erano `datasource_node`).
  datasourceNode: DataSourceNode,
};

const defaultEdgeOptions = {
  type: 'smoothstep', animated: false,
  style: { stroke: '#9C27B0', strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#9C27B0' }
};

const ManipolaEditorContent = () => {
  const { projectId } = useParams();
  const [showVersions, setShowVersions] = useState(false);

  const navigate = useNavigate();
  const { user } = useAuth();
  const { screenToFlowPosition } = useReactFlow();
  const toast = useToast();

  const { theme, setTheme } = useTheme();
  const hexCanvasRef = useRef(null);
  useHexCanvas(hexCanvasRef);

  const {
    onNodesChange, onEdgesChange,
    loadGraph, editEdges, insertNode, applyDerivedNodes,
    insertBetween, addSubgraph,
    nodesRef, edgesRef,
    getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'manipola' });

  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles, sourcesError, sourcesLoading, reloadSources,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: MANIPOLA_SOURCE_TYPES,
  });

  const preview   = usePreview(projectId, beforePreview);
  const { saving, handleSaveCanvas } = useCanvasSave(saveNow, {
    logPrefix: '[ManipolaEditor]',
  });
  // I 3 throws fail-fast (canvas vuoto / Output mancante / sorgente mancante)
  // erano duplicati delle regole CANVAS_EMPTY / RESULT_NODE_MISSING /
  // RESULT_NO_SOURCE, ora coperte dalla validazione backend. Il flow
  // `ResultNode.handleExecuteWithValidation` valida (POST /validate) PRIMA di
  // chiamare `onExecute`: se fallisce apre il modal e ritorna; quindi questo
  // `onBeforeExecute` viene raggiunto solo a validation-passed.
  const execution = useExecution(projectId, {
    onBeforeExecute: saveForRun,
    tracksRunState: true,
  });

  useEffect(() => {
    if (!project?.canvas?.nodes || loading) return;

    const loadedNodes = deserializeNodes(project.canvas.nodes, {
      flags: { isManipulate: true },
    });

    const rawEdges = (project.canvas.edges || []).map(edge => {
      if (edge.targetHandle) return edge;
      const match = edge.id?.match(/(input-\d+)$/);
      return match ? { ...edge, targetHandle: match[1] } : edge;
    });

    const loadedEdges = [...rawEdges, ...hydrateParameterBindingEdges(loadedNodes)];
    // loadGraph: grafo dal backend, stato pulito e cronologia azzerata. Le
    // colonne le ricalcola la sottoscrizione unica (hooks/useColumns).
    loadGraph(loadedNodes, loadedEdges, serverVersionOf(project));
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/deserializeNodes omessi: load programmatico una sola volta
    // per project, non ad ogni re-bind degli handler.
  }, [project, loading]);

  // Registra azioni editor nello store — DEVE stare dopo tutti gli hook e useCallback
  // Azioni dell'editor: registrate una volta, chiamano sempre l'ultima
  // versione (components/editor/useEditorActions, regola 9 Blocco A).
  useEditorActions({
    onPreview:                    () => preview.handlePreview(),
    // Input e conferme: gia' nella finestra unica «Prima di avviare»
    // (editor/PipelineRunner, Blocco A regola 5).
    onExecute:                    (nodeId, opts) => execution.handleExecute(opts),
  });

  // Sincronizza connections nello store
  useEffect(() => {
    useCanvasStore.getState().setConnections(connections);
  }, [connections]);

  // Outline amber sui nodi con issue di validazione (popolati dallo store
  // da ResultNode.runValidation). Hook condiviso con ArricchisciEditor.
  useValidationHighlights(applyDerivedNodes);

  const onConnect = useCallback((params) => {
    // Input runtime su un parametro singolo che una lista sostituisce
    // (Filtra con condizioni, Formula con piu' colonne): la lista torna al
    // formato singolo, oppure il collegamento e' rifiutato con il motivo.
    const bindingSource = nodesRef.current.find(node => node.id === params.source);
    if (isScalarProjectInputNode(bindingSource)) {
      const paramId = String(params.targetHandle || '').match(/^parameter:(.+)$/)?.[1];
      const check = paramId ? useCanvasStore.getState().prepareParameterBinding(params.target, paramId) : { ok: true };
      if (!check.ok) { toast.warning(check.message); return; }
    }
    editEdges(eds => {
      const sourceNode = nodesRef.current.find(node => node.id === params.source);
      const edge = isScalarProjectInputNode(sourceNode)
        ? createParameterBindingEdge(params)
        : { ...params, ...defaultEdgeOptions };
      return addEdge(edge, eds);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editEdges]);


  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    editEdges(eds => eds.filter(e => e.id !== edge.id));
  }, [editEdges]);

  // Ogni nodo nuovo nasce al centro della vista visibile (UI-3, P30).
  const getPos = useCallback(() => getViewportCenter({ screenToFlowPosition }), [getViewportCenter, screenToFlowPosition]);

  // Un passo che entra nel flusso dopo il nodo di partenza della palette:
  // se quel nodo ha un solo passo successivo e il nuovo lascia passare i
  // dati, entra nella catena (store.insertNode).
  const makeChainEdge = useCallback((params) => ({
    ...params, ...defaultEdgeOptions,
    id: `xy-edge__${params.source}${params.sourceHandle || ''}-${params.target}${params.targetHandle || ''}`,
  }), []);
  const insertAfter = useCallback((node, afterId) => {
    const multi = node.type === 'multi_join' || (node.type === 'transform' && isMultiInputOp(node.data?.transform_op));
    insertNode({
      node, afterId,
      passThrough: node.type !== 'result',
      inHandle: multi ? 'input-0' : (node.type === 'result' ? 'input' : null),
      makeEdge: makeChainEdge,
    });
  }, [insertNode, makeChainEdge]);

  // Catalogo unico dei nodi (UI-3, P30): menu, palette e canvas vuoto.
  const { addNode, addSource } = useCatalogAdd('manipola', getPos, { insertAfter });
  const [showUpload, setShowUpload] = useState(false);
  // U30: caricare un file dall'editor (regola unica: components/editor/editorUpload).
  const { canUpload, onUploaded } = useEditorFileUpload({ user, project, isReadOnly, reloadSources, addSource, toast });

  // U9: i nodi sorgente salvati col solo id prendono il nome dal catalogo
  // delle sorgenti (campo transitorio: il canvas non risulta modificato).
  useResolvedSourceNames(applyDerivedNodes, availableSources, availableFiles);

  // ── Palette "Cosa vuoi fare?" (Fase 4, M2) ────────────────────────────
  // Catalogo condiviso con errore e «Riprova» (hooks/useTransformRegistry,
  // audit N13): la palette lo dice invece di restare senza operazioni.
  const registryResource = useTransformRegistry();
  const transformRegistry = registryResource.data || null;

  // Il nuovo passo nasce dopo il nodo di partenza, gia' selezionato
  // (pannello aperto). Sorgenti e voci del catalogo passano dal catalogo.
  const handlePalettePick = useCallback((item, afterId) => {
    if (item.kind === 'source') { addSource(item.sourceKind, item.source); return; }
    if (item.kind === 'catalog') { addNode(item.catalogKey, { afterId }); return; }
    const newNode = { id: `transform_${Date.now()}`, type: 'transform', position: getPos(),
      // U21: un Join nuovo nasce con il tipo predefinito del catalogo unico.
      data: { transform_op: item.id, config: item.id === 'join' ? { how: newJoinType('manipola') } : {} } };
    insertAfter(newNode, afterId);
  }, [addSource, addNode, getPos, insertAfter]);

  const registryFailed = registryResource.status === 'error';
  const retryRegistry = registryResource.reload;
  // U11: le sorgenti sono nella palette; a canvas vuoto vengono per prime.
  const canvasEmpty = useCanvasDerived(isCanvasEmpty, { on: 'structure' });
  const palette = useMemo(() => {
    const ops = buildManipolaPaletteGroups(transformRegistry);
    const sources = sourcePaletteGroups({ projects: availableSources, files: availableFiles });
    return {
      groups: canvasEmpty ? [...sources, ...ops] : [...ops, ...sources],
      onPick: handlePalettePick,
      notice: registryFailed ? { message: 'Le operazioni non sono state caricate.', onRetry: retryRegistry } : null,
    };
  }, [transformRegistry, availableSources, availableFiles, canvasEmpty, handlePalettePick, registryFailed, retryRegistry]);

  // ── Rami (Fase 4, M6) ─────────────────────────────────────────────────
  const branchEdge = useCallback((params) => ({
    ...params, ...defaultEdgeOptions,
    id: `xy-edge__${params.source}${params.sourceHandle || ''}-${params.target}${params.targetHandle || ''}`,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), []);

  // "Collega": un Join (o Concatena) subito dopo il passo scelto, che ne
  // riceve TUTTE le uscite; il ramo scollegato entra nel secondo ingresso.
  // Un'unica azione (store.insertBetween). Chiave del Join proposta: una
  // colonna visibile con lo stesso nome nei due rami (stesse regole del
  // pannello Join: nomi e id).
  const connectBranch = useCallback((endId, targetId, mode) => {
    const current = nodesRef.current;
    const end = current.find(n => n.id === endId);
    const target = current.find(n => n.id === targetId);
    if (!end || !target) return;
    const op = mode === 'concat' ? 'concat' : 'join';
    // Colonne in uscita dei due rami dalla fonte unica (con gli id).
    const leftCols = getColumnsOf(targetId).columns;
    const rightCols = getColumnsOf(endId).columns;
    const shared = leftCols.find(c => rightCols.some(r => r.name === c.name))?.name;
    const config = op === 'join'
      ? { how: newJoinType('manipola'), name_conflict: 'keep_left', ...joinKeysPatch(shared ? [[shared, shared]] : [], leftCols, rightCols) }
      : {};
    // Il ramo si chiama come la sua corsia (vista Passi e pannello).
    const headId = analyzeCanvas(current, edgesRef.current).laneOf.get(endId)?.headId;
    const head = current.find(n => n.id === headId) || end;
    const branchName = nodeTitle(head.type, head.data);
    const newNode = {
      id: `transform_${Date.now()}`, type: 'transform', position: getPos(),
      // Titolo derivato (operazione · ramo): niente data.title (regola 7).
      data: connectStepData(op, config, branchName),
    };
    const done = insertBetween({
      node: newNode, targetId, inHandle: 'input-0', makeEdge: branchEdge,
      extraEdges: [branchEdge({ source: endId, sourceHandle: null, target: newNode.id, targetHandle: 'input-1' })],
    });
    if (!done) return;
    if (op === 'join' && !shared) toast.warning(`Ramo collegato: scegli le chiavi del Join nel pannello (nessuna colonna con lo stesso nome).`);
    else toast.success(`Ramo "${branchName}" collegato dopo "${nodeTitle(target.type, target.data)}".`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insertBetween, branchEdge, toast]);

  // "Duplica ramo": i passi alimentati solo da questa sorgente, con gli
  // STESSI collegamenti (maniglie e input runtime compresi), attaccati a
  // un'altra sorgente come un blocco (store.addSubgraph, un solo Ctrl+Z).
  // L'ultimo passo copiato resta selezionato: da li' si collega il ramo.
  const duplicateBranch = useCallback((sourceId, otherId) => {
    const current = nodesRef.current;
    const source = current.find(n => n.id === sourceId);
    const other = current.find(n => n.id === otherId);
    const steps = branchSteps(current, edgesRef.current, sourceId);
    if (!source || !other || steps.length === 0) return;
    const stamp = Date.now();
    const dx = other.position.x - source.position.x;
    const dy = other.position.y - source.position.y;
    const idMap = new Map([[sourceId, otherId], ...steps.map((n, i) => [n.id, `${n.type}_${stamp}_${i}`])]);
    const otherName = nodeTitle(other.type, other.data);
    // Titolo derivato col suffisso del ramo (utils/branchNodes): sempre
    // distinto dall'originale, senza titoli congelati.
    const clones = steps.map((n) => ({
      id: idMap.get(n.id), type: n.type,
      position: { x: n.position.x + dx, y: n.position.y + dy },
      data: cloneStepData(n, otherName),
    }));
    const stepIds = new Set(steps.map(n => n.id));
    const copied = edgesRef.current
      // Dal ramo, dagli input runtime (binding) e dai parametri (Valori statici).
      .filter(e => stepIds.has(e.target) && (idMap.has(e.source) || isParameterBindingEdge(e)
        || nodeRole(current.find(x => x.id === e.source)) === 'param'))
      .map(e => ({
        ...e,
        id: `${e.id}__copy_${stamp}`,
        source: idMap.get(e.source) || e.source, // i binding restano sullo stesso input runtime
        target: idMap.get(e.target),
        selected: false,
      }));
    const last = clones[clones.length - 1].id;
    const done = addSubgraph({ nodes: clones, edges: copied, selectId: last });
    if (!done) return;
    toast.success(`Ramo duplicato su "${otherName}": ${clones.length} ${clones.length === 1 ? 'passo copiato' : 'passi copiati'}. Collegalo dal pannello a destra.`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addSubgraph, toast]);

  const branchActions = useMemo(() => ({
    connectBranch,
    connectModes: [['join', 'Unisci con (Join)'], ['concat', 'Accoda a (Concatena)']],
    duplicateBranch,
  }), [connectBranch, duplicateBranch]);


  const onDragOver = useCallback((event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }, []);

  // Sorgente trascinata dalla barra laterale: stesso costruttore del clic
  // (editorNodeCatalog, con il nome), alla posizione del rilascio.
  const onDrop = useCallback((event) => {
    event.preventDefault();
    const dropped = readSourceDrop(event);
    if (!dropped) return;
    addSource(dropped.kind, dropped.item, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }, [screenToFlowPosition, addSource]);

  // U30: file caricato dall'editor -> nell'elenco delle sorgenti e nel canvas.
  // Uscite: chiede il guard unico (NavigationGuard + useBlocker).
  useUnsavedChangesGuard();
  const handleBack = () => navigate('/');

  // beforeCanvas slot: banner accessibilita' nodi cross-tenant.
  const accessDeniedBanner = <AccessDeniedBanner />;

  // topbar slot
  // Barra unica (Fase 4, M7): le azioni dell'editor nel menu "⋯", i passi
  // dalla palette ("＋ Passo"), un solo "▶ Esegui".
  // Vincoli del catalogo (Output e caricamenti) dal grafo, senza iscriversi
  // ai nodi (UI-2, hooks/useCanvasDerived).
  const catalogNodes = useCanvasDerived(catalogConstraintNodes);
  const toolbar = {
    mode: 'manipola',
    title: project?.metadata?.name,
    onBack: handleBack,
    onSave: handleSaveCanvas,
    saving,
    ...executionToolbarProps(execution),
    theme,
    setTheme,
    // Lo stato delle colonne e' nella barra comune (EditorToolbar, ColumnSyncBadge).
    statusExtra: <ValidationStatusBadge />,
    // Sola lettura: nessuna azione che modifica (Versioni crea e ripristina).
    // Voci dal catalogo unico (UI-3, P30): stessi strumenti dei tre editor,
    // voci spente con il motivo scritto (E3).
    menuItems: isReadOnly ? [] : [
      ...catalogMenuItems('manipola', catalogNodes, addNode),
      { key: 'div-file', divider: true },
      ...(canUpload ? [menuEntry('upload_file', { onClick: () => setShowUpload(true) })] : []),
      menuEntry('versions', { onClick: () => setShowVersions(true) }),
    ],
  };

  // sidebar slot
  const sidebar = !isReadOnly ? (
    <EditorSidebar
      variant="manipola" projects={availableSources} files={availableFiles}
      onPick={(kind, item) => addSource(kind, item)} onUpload={canUpload ? () => setShowUpload(true) : null}
      sourcesLoading={sourcesLoading} sourcesError={sourcesError} onRetrySources={reloadSources}
      projectsConfig={{ title: '📊 Sorgenti Progetto', subtitle: 'Clic o trascina un progetto nel canvas: ne legge il risultato',
        emptyMessage: 'Nessun progetto disponibile.', emptyHint: 'Crea prima un progetto Estrai, Manipola o Arricchisci.',
        filterTypes: ['estrai', 'manipola', 'arricchisci'], showSearch: true }}
      filesConfig={{ title: '📁 File Esterni', subtitle: 'Clic o trascina un file nel canvas',
        emptyMessage: 'Nessun file disponibile.', uploadLink: '/files', uploadLinkText: 'Carica file esterni' }}
    />
  ) : null;

  // modals slot
  const modals = (
    <>
        {showVersions && (
          <CanvasVersionsModal isOpen onClose={() => setShowVersions(false)} projectId={projectId} />
        )}
        <EditorFileUploadModal isOpen={showUpload} onClose={() => setShowUpload(false)} onUploaded={onUploaded} variant="manipola" />
        <ExecutionModal {...executionModalProps(execution)} projectId={projectId} projectName={project?.metadata?.name} title={`${term('run').icon} Esecuzione del progetto Manipola`} projectType="manipola" />
        {preview.showModal && (
<PreviewModal isOpen={preview.showModal} onClose={preview.closeModal} onRefresh={limit => preview.handlePreview(limit)}
          data={preview.data} loading={preview.loading} error={preview.error}
          permissionRevoked={preview.permissionRevoked}
          title={`${term('preview').name} del progetto Manipola`} projectType="manipola" disableGrouping />
)}
      </>
  );

  return (
    <EditorShell
      projectId={projectId}
      user={user}
      prefix="manipola"
      loading={loading}
      loadError={loadError}
      project={project}
      reload={reloadData}
      onBack={handleBack}
      hexCanvasRef={hexCanvasRef}
      canvasConfig={{
        nodeTypes,
        defaultEdgeOptions,
        fitView: true,
        fitViewOptions: { padding: 0.2, maxZoom: 0.8 },
        minZoom: 0.3,
        maxZoom: 2,
        backgroundProps: { color: '#aaa', gap: 20 },
        showMiniMap: true,
        isReadOnly,
        readOnlyProps,
        palette,
        branchActions,
      }}
      canvasHandlers={{
        onNodesChange,
        onEdgesChange,
        onConnect,
        onEdgeContextMenu,
        onDrop,
        onDragOver,
      }}
      slots={{
        toolbar,
        sidebar,
        beforeCanvas: accessDeniedBanner,
        modals,
      }}
    />
  );
};

// Fase 5 T2 (R4.6): un progetto = un editor (rimontato al cambio di progetto).
const ManipolaEditor = () => {
  const { projectId } = useParams();
  return (
    <ReactFlowProvider key={projectId}>
      <ConfirmProvider>
        <ManipolaEditorContent />
      </ConfirmProvider>
    </ReactFlowProvider>
  );
};

export default ManipolaEditor;
