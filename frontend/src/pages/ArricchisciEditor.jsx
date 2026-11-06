/**
 * ArricchisciEditor.jsx — Editor visuale per il modulo ARRICCHISCI
 *
 * Permette di costruire pipeline di arricchimento dati collegando:
 *  - Nodi sorgente (progetti Estrai/Manipola, file, datasource)
 *  - Nodi ApiEnrich (API esterne, eseguibili, datasource)
 *  - Nodo valori statici con template variabili ({{NOW}}, {{TODAY}}, ...)
 *  - Nodo Output con mapping risposta JSON
 *
 * Hooks usati: useEditorData, useCanvasCore, useEdgeHandlers,
 *              useExecution, usePreview,
 *              useMultiModal, useToast
 *
 * Route: /arricchisci/:projectId
 */
import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { useCanvasDerived } from '../hooks/useCanvasDerived';
import { sinkFlags } from '../utils/canvasGraph';
import { ConfirmProvider } from '../context/ConfirmContext';
import { executeEnrichmentAsync } from '../api';
import { useAuth } from '../context/AuthContext';
import { useEditorData } from '../hooks/useEditorData';
import { beforePreview, saveForRun, saveNow } from '../utils/serverActions';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import useCanvasStore from '../store/useCanvasStore';
import { serverVersionOf } from '../utils/canvasVersion';
import { columnSnapshot } from '../hooks/useColumns';
import { columnOrigin, columnsOf } from '../utils/columnSource';
import {
  createParameterBindingEdge,
  dataFlowEdges,
  hydrateParameterBindingEdges,
  isScalarProjectInputNode,
} from '../utils/runtimeBindingEdges';
import ValidationStatusBadge from '../components/editor/ValidationStatusBadge';
import { deserializeNodes } from '../utils/nodeFactories';
import EditorSidebar from '../components/panels/EditorSidebar';
import ProjectSourceNode from '../components/nodes/ProjectSourceNode';
import FileSourceNode from '../components/nodes/FileSourceNode';
import ProjectInputNode from '../components/nodes/ProjectInputNode';
import ApiEnrichNode from '../components/panels/ApiEnrichNode';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import '../components/nodes/AnnotationNode.css';
import { nodeTitle } from '../components/canvas/nodeDescriptors';

// Modali lazy-loaded
const ExecutionModal = lazyWindow(() => import('../components/modals/ExecutionModal'), 'Esecuzione');
const PreviewModal = lazyWindow(() => import('../components/modals/PreviewModal'), term('preview').name);
const ConnectionSelectorModal = lazyWindow(() => import('../components/arricchisci/ConnectionSelectorModal'), 'Scelta della connessione');
const CanvasVersionsModal = lazyWindow(() => import('../components/modals/CanvasVersionsModal'), 'Versioni');
import { executionModalProps, executionToolbarProps } from '../hooks/useExecution';
import { usePreview, useExecution, useCanvasCore, useMultiModal, useCanvasSave, useUnsavedChangesGuard, useValidationHighlights } from '../hooks';
import { useToast } from '../hooks/useToast';
import { useTheme } from '../hooks/useTheme';
import '../styles/ArricchisciEditor.css';
import '../components/arricchisci/ConnectionSelectorModal.css';
import '../components/nodes/StaticValuesNode.css';
import { lazyWindow } from '../components/common/LazyWindow';
import { useHexCanvas } from '../utils/hexCanvas';
import { menuEntry, paletteEntry, term } from '../utils/glossary';
import useEditorActions from '../components/editor/useEditorActions';
import useCatalogAdd, { useResolvedSourceNames } from '../components/editor/useCatalogAdd';
import { catalogConstraintNodes, catalogMenuItems, isCanvasEmpty, catalogPaletteItems, readSourceDrop, sourcePaletteGroups } from '../components/editor/editorNodeCatalog';
import EditorFileUploadModal from '../components/editor/EditorFileUploadModal';
import { useEditorFileUpload } from '../components/editor/editorUpload';

/**
 * Costruisce una mappa nodeId → titolo per il raggruppamento colonne in
 * PreviewModal: lo stesso titolo del canvas e del pannello (nodeDescriptors,
 * titolo dell'utente compreso), non un secondo calcolo (Blocco A, d.9).
 */
const buildNodeLabelMap = (nodes) => Object.fromEntries(
  nodes.map(node => [node.id, nodeTitle(node.type, node.data)]),
);

// Costante stabile — evita array literal nelle deps di useEditorData
const ARRICCHISCI_SOURCE_TYPES = ['estrai', 'manipola', 'arricchisci'];

// Costanti stabili fuori dal componente — evita re-render loop in ReactFlow StoreUpdater
// Qualsiasi oggetto/funzione inline nelle props di ReactFlow causa Maximum update depth
const NODE_COLORS = { project_source: '#4CAF50', file_source: '#3F51B5', api_enrich: '#FF6B35', result: '#2196F3', staticValues: '#667eea', carica: '#7c3aed' };
const nodeColorFn = (node) => NODE_COLORS[node.type] || '#999';
const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 0.8 };

const nodeTypes = {
  project_source: ProjectSourceNode, file_source: FileSourceNode,
  project_input: ProjectInputNode,
  api_enrich: ApiEnrichNode, result: ResultNode,
  staticValues: StaticValuesNode, carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode,
  // UI-3 (P30): la Nota c'e' anche in Arricchisci, come negli altri editor.
  comment: AnnotationNode,
};

const defaultEdgeOptions = {
  type: 'smoothstep', animated: true,
  style: { stroke: '#FF6B35', strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#FF6B35' }
};

const ArricchisciEditorContent = () => {
  const { projectId } = useParams();

  const navigate = useNavigate();
  const { user } = useAuth();
  const { screenToFlowPosition } = useReactFlow();

  const { theme, setTheme } = useTheme();
  const hexCanvasRef = useRef(null);
  useHexCanvas(hexCanvasRef);

  const toast = useToast();

  const {
    onNodesChange, onEdgesChange,
    loadGraph, addNodes, editEdges, insertNode, applyDerivedNodes,
    nodesRef, edgesRef, updateNodeData,
    getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'arricchisci' });

  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles, sourcesError, sourcesLoading, reloadSources,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: ARRICCHISCI_SOURCE_TYPES,
  });


  const modals = useMultiModal(['template', 'openapi', 'manual', 'datasource']);
  const [showVersions, setShowVersions] = useState(false);

  const preview   = usePreview(projectId, beforePreview);
  const { saving, handleSaveCanvas } = useCanvasSave(saveNow, {
    logPrefix: '[ArricchisciEditor]',
  });
  const execution = useExecution(projectId, {
    executeFunction: executeEnrichmentAsync,
    // Tutti i guard (CANVAS_EMPTY, ARRICCHISCI_NO_SOURCES, ARRICCHISCI_NO_ENRICHER)
    // sono coperti dalla validazione backend (POST /validate), chiamata da
    // `ResultNode.handleExecuteWithValidation` prima di `onExecute`.
    // onBeforeExecute raggiunto solo a validation-passed.
    onBeforeExecute: saveForRun,
    tracksRunState: true,
  });

  useEffect(() => {
    if (!project?.canvas?.nodes || loading) return;

    const loadedNodes = deserializeNodes(project.canvas.nodes, {
      flags: { isArricchisci: true },
    });

    // Corregge edges con targetHandle/sourceHandle "undefined" (salvati in canvas precedenti)
    // NON si elimina: si corregge al valore standard del nodo destinatario
    const sanitizedEdges = (project.canvas.edges || []).map(e => {
      const clean = { ...e };
      if (!clean.targetHandle || clean.targetHandle === 'undefined') clean.targetHandle = 'input';
      if (!clean.sourceHandle || clean.sourceHandle === 'undefined') clean.sourceHandle = 'output';
      return clean;
    });
    // loadGraph: grafo dal backend, stato pulito e cronologia azzerata.
    loadGraph(loadedNodes, [...sanitizedEdges, ...hydrateParameterBindingEdges(loadedNodes)], serverVersionOf(project));
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/deserializeNodes omessi: load programmatico una sola
    // volta per project, non ad ogni re-render dei setter store.
  }, [project, loading]);


  // Colonne dell'Output, dei Carica e dell'arricchimento: dalla fonte unica
  // (Blocco A, regola 4), calcolate dal server sul canvas dell'editor con lo
  // stesso mapping dell'esecuzione. Nessun calcolo e nessuna scrittura qui.

  // NOTA: le colonne dei nodi source vengono aggiornate direttamente
  // dai nodi stessi tramite useCanvasStore.getState().updateNodeData
  // Non servono handler passati come prop.

  // Avvio: input, stima e conferma sono gia' passati dalla finestra unica
  // «Prima di avviare» del percorso unico (editor/PipelineRunner, Blocco A
  // regola 5); qui si aggiunge solo il formato dell'Output.
  const handleExecute = useCallback((_id, options = {}) => {
    const resultNode = nodesRef.current.find(n => n.type === 'result');
    return execution.handleExecute({ format: resultNode?.data?.format || 'csv', ...options });
  }, [execution, nodesRef]);

  // Registra azioni editor nello store — dopo tutti gli hook e useCallback
  // Azioni dell'editor: registrate una volta, chiamano sempre l'ultima
  // versione (components/editor/useEditorActions, regola 9 Blocco A).
  useEditorActions({
    onPreview:        () => preview.handlePreview(),
    onExecute:        handleExecute,
  });

  // Sincronizza connections nello store
  useEffect(() => {
    useCanvasStore.getState().setConnections(connections);
  }, [connections]);

  // Outline amber sui nodi con issue di validazione (popolati dallo store
  // da ResultNode.runValidation). Hook condiviso con ManipolaEditor.
  useValidationHighlights(applyDerivedNodes);

  const getPos = () => getViewportCenter({ screenToFlowPosition });

  // ── Palette "Cosa vuoi fare?" (Fase 4, M2) ────────────────────────────
  // Il nodo API nasce dopo la scelta della connessione nel selettore: il
  // nodo di partenza resta in sospeso qui e il nodo entra nel flusso a quel
  // punto (store.insertNode: catena se c'e' un solo passo successivo).
  const paletteAfterRef = useRef(null);
  // Stessa normalizzazione degli handle di onConnect ('output' -> 'input').
  const makeEdge = useCallback((p) => {
    const params = { ...p, sourceHandle: p.sourceHandle || 'output', targetHandle: p.targetHandle || 'input' };
    return { ...params, ...defaultEdgeOptions, id: `edge_${params.source}_${params.target}_${params.targetHandle}` };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const placeAfter = useCallback((node, afterId, passThrough) => {
    if (afterId) insertNode({ node, afterId, passThrough, inHandle: 'input', outHandle: 'output', makeEdge });
    else addNodes(node);
  }, [insertNode, addNodes, makeEdge]);

  const handleSelectConnection = useCallback((connection, category) => {
    const newNodeId = `api_enrich_${Date.now()}`;
    const newNode = ({ id: newNodeId,
      type: category === 'datasource' ? 'project_source' : 'api_enrich',
      // Posizionamento deterministico al centro del viewport corrente,
      // allineato agli altri nodi nuovi dell'editor (catalogo unico,
      // components/editor/useCatalogAdd) che nascono al centro della vista. Pre-fix
      // `Math.random()` produceva posizioni non riproducibili e potenzialmente
      // fuori dal viewport visibile su zoom alto.
      // Pre-fix (P2 frontend-pages): `getPos` veniva letta dalla
      // closure ma `[addNodes]` era l'unica dep → se
      // `screenToFlowPosition` cambiava (cambio viewport), la
      // versione catturata era stale. Post-fix: invochiamo
      // `getViewportCenter({ screenToFlowPosition })` direttamente
      // qui dentro, e `screenToFlowPosition` e' in deps.
      position: getViewportCenter({ screenToFlowPosition }),
      // Il nodo porta il RIFERIMENTO alla connessione (Fase 5, T6, R2.10,
      // N11): niente endpoint, auth mascherata ne' openapi_spec copiati nel
      // canvas; il pannello li legge dalla connessione. `parameters` e
      // `static_parameters` sono l'istantanea per la validazione,
      // riallineata ogni volta che la connessione si rilegge.
      data: { api_id: connection.id, api_name: connection.name, param_mapping: {},
        enrich_type: category, api_type: connection.api_type,
        parameters: connection.parameters, output_mapping: connection.output_mapping,
        static_parameters: connection.static_parameters || {},
        ...(category === 'rest_file'  && { connection_type: 'rest_file',  request: connection.request, response: connection.response }),
        ...(category === 'datasource' && { source_type: 'datasource', datasource_config: connection.datasource }) } });
    // Un progetto sorgente non ha ingresso: dopo la palette nasce solo vicino.
    const afterId = paletteAfterRef.current;
    paletteAfterRef.current = null;
    placeAfter(newNode, category === 'datasource' ? null : afterId, true);
    if (category === 'template')   modals.close('template');
    if (category === 'manual')     modals.close('manual');
    if (category === 'openapi')    modals.close('openapi');
    if (category === 'rest_file')  modals.close('rest_file');
    if (category === 'datasource') modals.close('datasource');
  }, [placeAfter, screenToFlowPosition, modals]);

  // Catalogo unico dei nodi (UI-3, P30): Valori statici, Input runtime,
  // Nota, Output e caricamenti nascono nella vista visibile (prima a
  // posizioni fisse, spesso fuori vista); l'Output dalla palette entra nel
  // flusso dopo il nodo di partenza.
  const { addNode, addSource } = useCatalogAdd('arricchisci', getPos, {
    insertAfter: (node, afterId) => placeAfter(node, afterId, false),
  });
  const [showUpload, setShowUpload] = useState(false);
  // U30: caricare un file dall'editor (regola unica: components/editor/editorUpload).
  const { canUpload, onUploaded } = useEditorFileUpload({ user, project, isReadOnly, reloadSources, addSource, toast });
  // U9: nome delle sorgenti salvate col solo id (campo transitorio).
  useResolvedSourceNames(applyDerivedNodes, availableSources, availableFiles);

  const canvasEmpty = useCanvasDerived(isCanvasEmpty, { on: 'structure' });
  const palette = useMemo(() => {
    const sources = sourcePaletteGroups({ projects: availableSources, files: availableFiles });
    const own = [
      { label: 'Arricchisci', items: [
        // Nomi, icone e descrizioni dal glossario; `id` e' la finestra da aprire.
        paletteEntry('connector_manual', { id: 'manual', kind: 'connector' }),
        paletteEntry('connector_openapi', { id: 'openapi', kind: 'connector' }),
        paletteEntry('connector_rest_file', { id: 'rest_file', kind: 'connector' }),
      ] },
      { label: 'Uscite', items: catalogPaletteItems('arricchisci', ['output', 'load_db', 'load_arcgis']) },
      { label: 'Strumenti', items: catalogPaletteItems('arricchisci', ['runtime_input', 'static_values', 'note']) },
    ];
    return {
      // U11: sorgenti nella palette, per prime a canvas vuoto.
      groups: canvasEmpty ? [...sources, ...own] : [...own, ...sources],
      onPick: (item, afterId) => {
        if (item.kind === 'connector') { paletteAfterRef.current = afterId; modals.open(item.id); return; }
        if (item.kind === 'source') { addSource(item.sourceKind, item.source); return; }
        addNode(item.catalogKey, { afterId });
      },
    };
  }, [modals, addNode, addSource, availableSources, availableFiles, canvasEmpty]);

  const onConnect = useCallback((params) => {
    // Normalizza handles: undefined → 'input'/'output'
    const cleanParams = { ...params };
    const sourceNode = nodesRef.current.find(node => node.id === cleanParams.source);
    const scalarInput = isScalarProjectInputNode(sourceNode);
    if (!scalarInput) {
      if (!cleanParams.targetHandle || cleanParams.targetHandle === 'undefined') cleanParams.targetHandle = 'input';
      if (!cleanParams.sourceHandle || cleanParams.sourceHandle === 'undefined') cleanParams.sourceHandle = 'output';
    }
    const edge = scalarInput
      ? createParameterBindingEdge(cleanParams)
      : { ...cleanParams, ...defaultEdgeOptions };
    editEdges(eds => addEdge(edge, eds));
  }, [editEdges]);
  const onEdgeContextMenu  = useCallback((event, edge) => { event.preventDefault(); editEdges(eds => eds.filter(e => e.id !== edge.id)); }, [editEdges]);
  const onDragOver         = useCallback((event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }, []);

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
  const handleBack     = () => navigate('/');

  // beforeCanvas slot
  const accessDeniedBanner = <AccessDeniedBanner />;

  // topbar slot
  // Barra unica (Fase 4, M7): le fonti di arricchimento e le altre azioni
  // nel menu "⋯", un solo "▶ Esegui".
  // Vincoli del catalogo (Output e caricamenti) dal grafo, senza iscriversi
  // ai nodi (UI-2, hooks/useCanvasDerived).
  const catalogNodes = useCanvasDerived(catalogConstraintNodes);
  const toolbar = {
    mode: 'arricchisci',
    title: project?.metadata?.name,
    onBack: handleBack,
    onSave: handleSaveCanvas,
    saving,
    ...executionToolbarProps(execution),
    theme,
    setTheme,
    statusExtra: <ValidationStatusBadge />,
    // Sola lettura: nessuna azione che modifica (Versioni crea e ripristina).
    menuItems: isReadOnly ? [] : [
      // Nome, icona e descrizione di ogni voce dal glossario (regola 10).
      menuEntry('connector_template', { onClick: () => modals.open('template') }),
      menuEntry('connector_manual', { onClick: () => modals.open('manual') }),
      menuEntry('connector_openapi', { onClick: () => modals.open('openapi') }),
      menuEntry('connector_rest_file', { onClick: () => modals.open('rest_file') }),
      menuEntry('datasource', { onClick: () => modals.open('datasource') }),
      { key: 'div1', divider: true },
      // Voci dal catalogo unico (UI-3, P30), spente con il motivo (E3).
      ...catalogMenuItems('arricchisci', catalogNodes, addNode),
      { key: 'div-file', divider: true },
      ...(canUpload ? [menuEntry('upload_file', { onClick: () => setShowUpload(true) })] : []),
      menuEntry('versions', { onClick: () => setShowVersions(true) }),
    ],
  };

  // sidebar slot
  const sidebar = !isReadOnly ? (
    <EditorSidebar
      variant="arricchisci" projects={availableSources} files={availableFiles}
      onPick={(kind, item) => addSource(kind, item)} onUpload={canUpload ? () => setShowUpload(true) : null}
      sourcesLoading={sourcesLoading} sourcesError={sourcesError} onRetrySources={reloadSources}
      projectsConfig={{ title: '📊 Sorgenti Progetto', subtitle: 'Clic o trascina un progetto nel canvas',
        emptyMessage: 'Nessun progetto disponibile.', emptyHint: 'Crea prima un progetto Estrai, Manipola o Arricchisci.',
        filterTypes: ['estrai', 'manipola', 'arricchisci'], showSearch: true }}
      filesConfig={{ title: '📁 File Esterni', subtitle: 'Clic o trascina un file nel canvas',
        emptyMessage: 'Nessun file disponibile.', uploadLink: '/files', uploadLinkText: 'Carica file esterni' }}
    />
  ) : null;

  // modals slot — `modals` (var name) e' gia' occupato da useMultiModal:
  // usiamo `modalsSlot` per evitare collisione col chiamante .isOpen/.close.
  // P5 (Fase 5 T7): colonne arricchite UNA volta per risposta, non a ogni
  // render (un oggetto nuovo azzerava pagina, ordinamento, ricerca e scheda).
  // Le colonne del canvas si leggono quando arriva la risposta.
  const previewData = useMemo(() => {
          if (!preview.data?.columns) return preview.data;
          const { nodes } = useCanvasStore.getState();
          const resultNode = nodes.find(n => n.type === 'result');
          const nodeLabelMap  = buildNodeLabelMap(nodes);
          // Mappa name → nodo da cui la colonna entra nel flusso (sorgente o
          // arricchimento), risalendo per id dall'ingresso dell'Output (fonte unica).
          const snapshot = columnSnapshot();
          const sourceMap = {};
          if (resultNode) {
            columnsOf(snapshot, resultNode.id, { side: 'input' }).columns.forEach(col => {
              const origin = columnOrigin(snapshot, col.input_node, col.id) || col.input_node;
              if (origin) sourceMap[col.name] = nodeLabelMap[origin] || origin;
            });
          }
          const enrichedColumns = preview.data.columns.map(col => ({
            ...col,
            source: sourceMap[col.name] || col.source || null,
          }));
          // Ordina per sorgente mantenendo l'ordine di prima apparizione (segue sequenza canvas)
          const sourceOrder = [];
          enrichedColumns.forEach(col => {
            const s = col.source || '__none__';
            if (!sourceOrder.includes(s)) sourceOrder.push(s);
          });
          enrichedColumns.sort((a, b) =>
            sourceOrder.indexOf(a.source || '__none__') - sourceOrder.indexOf(b.source || '__none__')
          );
          return { ...preview.data, columns: enrichedColumns };
        }, [preview.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const modalsSlot = (
    <>
      {showVersions && (
        <CanvasVersionsModal isOpen onClose={() => setShowVersions(false)} projectId={projectId} />
      )}
      <EditorFileUploadModal isOpen={showUpload} onClose={() => setShowUpload(false)} onUploaded={onUploaded} variant="arricchisci" />
      <ExecutionModal {...executionModalProps(execution)} projectId={projectId} projectName={project?.metadata?.name} title={`${term('run').icon} Esecuzione dell'arricchimento`} projectType="arricchisci" />
      {preview.showModal && (
      <PreviewModal isOpen={preview.showModal} onClose={preview.closeModal} onRefresh={limit => preview.handlePreview(limit)}
        data={previewData}
        loading={preview.loading} error={preview.error}
        permissionRevoked={preview.permissionRevoked}
        title={`${term('preview').name} dell'arricchimento`} projectType="arricchisci" />
      )}
      <ConnectionSelectorModal isOpen={modals.isOpen('template')} onClose={() => modals.close('template')} onSelect={conn => handleSelectConnection(conn, 'template')} category="template" title="Seleziona un modello di API" icon={term('connector_template').icon} emptyMessage="Nessun modello di API configurato" emptyHint="Crea una connessione da un modello nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('manual')} onClose={() => modals.close('manual')} onSelect={conn => handleSelectConnection(conn, 'manual')} category="manual" title="Seleziona un'API manuale" icon={term('connector_manual').icon} emptyMessage="Nessuna API manuale configurata" emptyHint="Crea una nuova connessione API nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('openapi')} onClose={() => modals.close('openapi')} onSelect={conn => handleSelectConnection(conn, 'openapi')} category="openapi" title="Seleziona un'API OpenAPI" icon={term('connector_openapi').icon} emptyMessage="Nessuna API OpenAPI importata" emptyHint="Importa uno schema OpenAPI nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('rest_file')} onClose={() => modals.close('rest_file')} onSelect={conn => handleSelectConnection(conn, 'rest_file')} category="rest_file" title="Seleziona un processore file" icon={term('connector_rest_file').icon} emptyMessage="Nessun processore file configurato" emptyHint="Configura un servizio REST che riceve e restituisce file nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('datasource')} onClose={() => modals.close('datasource')} onSelect={conn => handleSelectConnection(conn, 'datasource')} category="datasource" title="Seleziona un Data Source" icon={term('datasource').icon} emptyMessage="Nessun Data Source configurato" emptyHint="Configura un Data Source nella pagina Data Source" createLink="/datasources" />
      </>
  );

  return (
    <EditorShell
      projectId={projectId}
      user={user}
      prefix="arricchisci"
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
        fitViewOptions: FIT_VIEW_OPTIONS,
        minZoom: 0.3,
        maxZoom: 2,
        backgroundProps: { color: '#ddd', gap: 20 },
        miniMapProps: { className: 'arricchisci-minimap', nodeColor: nodeColorFn, zoomable: true, pannable: true },
        isReadOnly,
        readOnlyProps,
        palette,
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
        modals: modalsSlot,
      }}
    />
  );
};

// Fase 5 T2 (R4.6): un progetto = un editor (rimontato al cambio di progetto).
const ArricchisciEditor = () => {
  const { projectId } = useParams();
  return (
    <ReactFlowProvider key={projectId}>
      <ConfirmProvider>
        <ArricchisciEditorContent />
      </ConfirmProvider>
    </ReactFlowProvider>
  );
};

export default ArricchisciEditor;
