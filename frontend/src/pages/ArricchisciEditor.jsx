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
 *              useEnrichmentValidation, useExecution, usePreview,
 *              useMultiModal, useToast
 *
 * Route: /arricchisci/:projectId
 */
import React, { useState, useCallback, useEffect, useMemo, useRef, lazy, Suspense } from 'react';
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { ConfirmProvider } from '../context/ConfirmContext';
import { executeEnrichmentAsync } from '../api';
import { useAuth } from '../context/AuthContext';
import { useEditorData } from '../hooks/useEditorData';
import { beforePreview, saveForRun, saveNow } from '../utils/serverActions';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import useCanvasStore from '../store/useCanvasStore';
import useRuntimeInputDefinitions from '../hooks/useRuntimeInputDefinitions';
import { executionNeedsConfig } from '../utils/executionConfig';
import { withAvailableColumns } from '../utils/columnPlans';
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
import { nodeTitle } from '../components/canvas/nodeDescriptors';

// Modali lazy-loaded
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ConnectionSelectorModal = lazy(() => import('../components/arricchisci/ConnectionSelectorModal'));
const EnrichmentConfirmModal = lazy(() => import('../components/modals/EnrichmentConfirmModal'));
const CanvasVersionsModal = lazy(() => import('../components/modals/CanvasVersionsModal'));
import { usePreview, useExecution, useCanvasCore, useMultiModal, useEnrichmentValidation, useCanvasSave, useUnsavedChangesGuard, useValidationHighlights } from '../hooks';
import { useToast } from '../hooks/useToast';
import { useTheme } from '../hooks/useTheme';
import '../styles/ArricchisciEditor.css';
import '../components/arricchisci/ConnectionSelectorModal.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/modals/EnrichmentConfirmModal.css';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';
import { addFirstMessage, flowEdgeLabel, menuEntry, onlyOneMessage, paletteEntry, term, termName } from '../utils/glossary';
import useEditorActions from '../components/editor/useEditorActions';

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
    nodes, edges, onNodesChange, onEdgesChange,
    loadGraph, addNodes, addEdges, editEdges, insertNode, applyDerivedNodes,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'arricchisci' });

  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: ARRICCHISCI_SOURCE_TYPES,
  });

  const [exportFormat, setExportFormat]         = useState('csv');
  const [showConfirmModal, setShowConfirmModal] = useState(false);

  const modals = useMultiModal(['template', 'openapi', 'manual', 'datasource']);
  const [showVersions, setShowVersions] = useState(false);

  const enrichmentValidation = useEnrichmentValidation();
  const preview   = usePreview(projectId, beforePreview);
  const { saving, handleSaveCanvas } = useCanvasSave(saveNow, {
    logPrefix: '[ArricchisciEditor]',
  });
  // Input runtime chiesti all'esecuzione, come in Estrai (audit R9.1).
  const runtimeInputDefinitions = useRuntimeInputDefinitions(nodes);
  // Letto al clic su Esegui (le azioni registrate non vedono i render nuovi).
  const needsRunConfigRef = useRef(false);
  needsRunConfigRef.current = executionNeedsConfig(runtimeInputDefinitions, user);
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
    loadGraph(loadedNodes, [...sanitizedEdges, ...hydrateParameterBindingEdges(loadedNodes)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/deserializeNodes omessi: load programmatico una sola
    // volta per project, non ad ogni re-render dei setter store.
  }, [project, loading]);


  // Aggiorna availableColumns in ResultNode e CaricanNode
  // Fingerprint stabile dei campi che devono triggerare il refresh di
  // `availableColumns` sui result/carica nodes. Pre-fix (P0 frontend-pages
  // #1): deps = [addNodes] significava firing ONLY al mount; il cambio
  // di `output_mapping` su un nodo api_enrich (utente che configura il
  // mapping output) NON ri-firrava l'effect → ResultNode esponeva colonne
  // stale finche' l'utente non triggerava manualmente un re-render.
  // Post-fix: dep su un fingerprint JSON-derivato che cambia quando
  // cambia uno dei mapping rilevanti (sources cols + api_enrich
  // output_mapping). Il loop nodes->effect e' evitato perche' il
  // fingerprint NON include `availableColumns` (campo che l'effect
  // scrive), solo gli input.
  const availableColumnsFingerprint = useMemo(() => {
    const relevant = nodes.map(n => {
      if (
        n.type === 'project_source'
        || n.type === 'file_source'
        || n.type === 'datasourceNode'
        || n.type === 'datasource_node'
      ) {
        return { id: n.id, type: n.type, cols: n.data?.columns };
      }
      if (n.type === 'api_enrich') {
        return { id: n.id, type: n.type, output_mapping: n.data?.output_mapping };
      }
      if (n.type === 'result') {
        // Selezione e GROUP BY decidono le colonne dei Carica (resultOutputColumns).
        const d = n.data || {};
        return { id: n.id, type: n.type, out: [d.output_columns, d.group_by_enabled, d.group_by_columns, d.aggregations] };
      }
      return null;
    }).filter(Boolean);
    return JSON.stringify(relevant);
  }, [nodes]);

  useEffect(() => {
    // NOTA: 'nodes' rimosso dalle dipendenze intenzionalmente per evitare loop React.
    // Tutto il calcolo avviene dentro l'updater (snapshot aggiornato).
    // Dato DERIVATO: non marca dirty.
    // La dep `availableColumnsFingerprint` triggera il refresh quando
    // input rilevanti cambiano (source cols / api_enrich output_mapping),
    // senza catturare modifiche a campi scritti dall'effect stesso.
    applyDerivedNodes(nds => {
      const resultNode = nds.find(n => n.type === 'result');
      if (!resultNode) return nds;

      // Solo i nodi a monte dell'Output (un ramo scollegato non arriva nel
      // file), con le colonne come escono davvero: nascoste tolte, alias
      // applicato (backend: filter_visible_columns). Nomi senza doppioni.
      const flow = dataFlowEdges(edgesRef.current);
      const upstream = new Set();
      const visit = (nodeId) => {
        if (upstream.has(nodeId)) return;
        upstream.add(nodeId);
        flow.filter(e => e.target === nodeId).forEach(e => visit(e.source));
      };
      visit(resultNode.id);
      const availableColumns = [];
      const push = (col) => { if (col.name && !availableColumns.some(c => c.name === col.name)) availableColumns.push(col); };
      for (const node of nds) {
        if (!upstream.has(node.id)) continue;
        const cols = node.data?.columns;
        if (!cols) continue;
        const source = node.data?.source_project_id || node.data?.file_source_id || node.type;

        if (
          node.type === 'project_source'
          || node.type === 'file_source'
          || node.type === 'datasourceNode'
          || node.type === 'datasource_node'
        ) {
          cols.forEach(col => {
            if (typeof col === 'object' && col !== null) {
              if (col.visible === false) return;
              push({ name: col.alias || col.name || 'unknown', type: col.type || col.dtype || 'str', source });
            } else if (typeof col === 'string') push({ name: col, type: 'str', source });
          });
        } else if (node.type === 'api_enrich' && node.data?.output_mapping) {
          node.data.output_mapping.forEach(mapping =>
            push({ name: mapping.column || mapping.target_column || mapping.name, type: mapping.type || 'str', source: 'api_enrich' })
          );
        }
      }

      // Carica: colonne dopo "Colonne in uscita" dell'Output (utils/columnPlans).
      return withAvailableColumns(nds, availableColumns);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyDerivedNodes, availableColumnsFingerprint, edges]);

  // NOTA: le colonne dei nodi source vengono aggiornate direttamente
  // dai nodi stessi tramite useCanvasStore.getState().updateNodeData
  // Non servono handler passati come prop.

  const getEnrichmentConfig = useCallback(() => {
    const currentNodes  = nodesRef.current;
    const apiNode       = currentNodes.find(n => n.type === 'api_enrich');
    if (!apiNode) return null;
    const staticValuesNodes = currentNodes.filter(n => n.type === 'staticValues');
    const staticColumns = staticValuesNodes
      .flatMap(node => (node.data?.fields || []).map(f => f.name));
    // B-FE-68: includi le colonne prodotte da enricher upstream (fromApi:true) nelle
    // staticColumns così il backend le skippa nel check missing_columns durante la validazione.
    const upstreamApiColumns = currentNodes
      .filter(n => n.type === 'api_enrich')
      .flatMap(n => (n.data?.columns || []).filter(c => c.fromApi && c.name).map(c => c.name));
    const allStaticColumns = [...new Set([...staticColumns, ...upstreamApiColumns])];
    // staticParameters: mappa {param_name: value} dai nodi StaticValues.
    // Pre-fix il FE non inviava questo campo al BE → validate produceva
    // falsi `missing_columns` per parametri soddisfatti da static values
    // (drift contracts-schemas P1 plenora-bug-contracts-schemas).
    const staticParameters = {};
    staticValuesNodes.forEach(node => {
      (node.data?.fields || []).forEach(f => {
        if (f.name && f.value !== undefined && f.value !== null && f.value !== '') {
          staticParameters[f.name] = f.value;
        }
      });
    });
    // B-FE-41: leggi file_source_id dal nodo file_source nel canvas
    const fileSourceNode    = currentNodes.find(n => n.type === 'file_source');
    const fileSourceId      = fileSourceNode?.data?.file_source_id || null;
    // B-FE-52: leggi source_project_id dal nodo project_source per validate/estimate
    const projectSourceNode = currentNodes.find(n => n.type === 'project_source');
    const sourceProjectId   = projectSourceNode?.data?.source_project_id || null;
    return {
      apiId: apiNode.data?.api_id,
      paramMapping: apiNode.data?.param_mapping || {},
      staticColumns: allStaticColumns,
      staticParameters,
      fileSourceId,
      sourceProjectId,
    };
  }, []);

  // Persistiamo le options ricevute da ResultNode (es. force_recalculate) tra
  // l'apertura del modal di conferma e il click su "Conferma" — altrimenti
  // la flag dal radio "Ricalcola tutto" andava persa nel passaggio
  // handleShowConfirmModal -> setShowConfirmModal -> handleConfirmExecution.
  const [pendingExecuteOptions, setPendingExecuteOptions] = useState({});

  const handleShowConfirmModal = useCallback(async (_id, options = {}) => {
    setPendingExecuteOptions(options);
    const currentNodes = nodesRef.current;
    // Risorse non accessibili e salvataggio: gia' fatti dal percorso unico di
    // ResultNode (Fase 4, M7) prima di arrivare qui.
    // I nodi file-processor (rest_file) lavorano
    // file->file, non riga-per-riga su un DataFrame. Il validate/estimate
    // dell'enrichment classico (che carica il file come DataFrame e controlla
    // le colonne mappate) non si applica: skippa il modal di conferma e
    // esegui direttamente.
    const hasFileProcessor = currentNodes.filter(n => n.type === 'api_enrich').some(n =>
      n.data?.connection_type === 'rest_file'
    );
    if (hasFileProcessor) {
      const resultNode = currentNodes.find(n => n.type === 'result');
      const format = resultNode?.data?.format || exportFormat || 'csv';
      execution.prepareExecute({ format, ...options }, { needsConfig: needsRunConfigRef.current });
      setPendingExecuteOptions({});
      return;
    }
    setShowConfirmModal(true);
    const config = getEnrichmentConfig();
    if (config?.apiId) enrichmentValidation.validateAndEstimate(
      config.sourceProjectId || projectId,
      config.apiId,
      config.paramMapping,
      config.fileSourceId,
      config.staticColumns,
      config.staticParameters,
    );
  }, [getEnrichmentConfig, projectId, enrichmentValidation, execution, exportFormat]);

  const handleConfirmExecution  = useCallback(() => {
    setShowConfirmModal(false);
    // Leggi il formato dal nodo Result al momento dell'esecuzione
    const resultNode = nodesRef.current.find(n => n.type === 'result');
    const format = resultNode?.data?.format || exportFormat || 'csv';
    execution.prepareExecute({ format, ...pendingExecuteOptions }, { needsConfig: needsRunConfigRef.current });
    setPendingExecuteOptions({});
  }, [execution, exportFormat, pendingExecuteOptions]);
  const handleCloseConfirmModal = useCallback(() => { setShowConfirmModal(false); enrichmentValidation.reset(); }, [enrichmentValidation]);

  // Registra azioni editor nello store — dopo tutti gli hook e useCallback
  // Azioni dell'editor: registrate una volta, chiamano sempre l'ultima
  // versione (components/editor/useEditorActions, regola 9 Blocco A).
  useEditorActions({
    onPreview:        () => preview.handlePreview(),
    onExecute:        handleShowConfirmModal,
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
      // allineato al pattern degli altri `handleAdd*Node` di questo file
      // (vedi `handleAddResultNode` etc.) che usano `getPos()`. Pre-fix
      // `Math.random()` produceva posizioni non riproducibili e potenzialmente
      // fuori dal viewport visibile su zoom alto.
      // Pre-fix (P2 frontend-pages): `getPos` veniva letta dalla
      // closure ma `[addNodes]` era l'unica dep → se
      // `screenToFlowPosition` cambiava (cambio viewport), la
      // versione catturata era stale. Post-fix: invochiamo
      // `getViewportCenter({ screenToFlowPosition })` direttamente
      // qui dentro, e `screenToFlowPosition` e' in deps.
      position: getViewportCenter({ screenToFlowPosition }),
      data: { api_id: connection.id, api_name: connection.name, param_mapping: {}, columns: [],
        enrich_type: category, api_type: connection.api_type, endpoint: connection.endpoint,
        auth: connection.auth, parameters: connection.parameters, output_mapping: connection.output_mapping,
        static_parameters: connection.static_parameters || {},
        ...(category === 'rest_file'  && { connection_type: 'rest_file',  request: connection.request, response: connection.response }),
        ...(category === 'datasource' && { source_type: 'datasource', datasource_config: connection.datasource }),
        ...(category === 'openapi' && { openapi_spec: connection.openapi_spec }) } });
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

  const handleAddStaticValuesNode = useCallback(() => {
    addNodes({ id: `static_${Date.now()}`, type: 'staticValues',
      position: { x: 100, y: 200 },
      data: { label: termName('static_values'), fields: [] } });
  }, [addNodes]);

  const handleAddProjectInputNode = useCallback(() => {
    const ordinal = nodesRef.current.filter(node => node.type === 'project_input').length + 1;
    addNodes({ id: `project_input_${Date.now()}`, type: 'project_input',
      position: { x: 100, y: 100 + ordinal * 80 },
      data: { input_key: `input_${ordinal}`, label: `Input ${ordinal}`, input_kind: 'file', required: true, expected_columns: [], validation: {}, bindings: [], order: ordinal - 1 } });
  }, [addNodes, nodesRef]);

  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'result')) { toast.warning(onlyOneMessage('output')); return; }
    addNodes({ id: `result_${Date.now()}`, type: 'result',
      position: { x: 600, y: 200 },
      data: { isArricchisci: true, fileName: 'output', format: 'csv', availableColumns: [],
        order_by_enabled: false, order_by_columns: [],
        having_enabled: false, having_conditions: [] } });
  }, [addNodes]);

  const handleAddCaricanNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'result')) { toast.warning(addFirstMessage('output')); return; }
    if (currentNodes.some(n => n.type === 'carica'))  { toast.warning(onlyOneMessage('load_db')); return; }
    const resultNode = currentNodes.find(n => n.type === 'result');
    const newNode = { id: `carica_${Date.now()}`, type: 'carica',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y },
      data: { connection_id: '', schema_name: '', table_name: '', write_mode: 'create',
        column_mappings: [], match_columns: [], batch_size: 1000,
        transaction_mode: 'all_or_nothing', create_spatial_index: true, conflict_policy: 'abort',
        availableColumns: resultNode.data?.availableColumns || [] } };
    addNodes(newNode);
    addEdges({ id: `edge_result_carica_${Date.now()}`, source: resultNode.id, target: newNode.id,
      type: 'smoothstep', animated: true, style: { stroke: '#7c3aed', strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#7c3aed' }, data: {} });
  }, [addNodes, addEdges]);

  const handleAddCaricanArcGISNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'result')) { toast.warning(addFirstMessage('output')); return; }
    if (currentNodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis')) { toast.warning(onlyOneMessage('load_arcgis')); return; }
    const resultNode = currentNodes.find(n => n.type === 'result');
    const newNode = {
      id: `carica_arcgis_${Date.now()}`, type: 'carica_arcgis',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y + 200 },
      data: { arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '',
        arcgis_operation: 'add', arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
        column_mappings: [], availableColumns: resultNode.data?.availableColumns || [] }
    };
    addNodes(newNode);
    addEdges({ id: `edge_result_carica_arcgis_${Date.now()}`, source: resultNode.id, target: newNode.id,
      label: flowEdgeLabel('carica_arcgis'), animated: true, style: { stroke: '#2DD4BF', strokeWidth: 2 }, data: { join_type: 'carica_arcgis' } });
  }, [addNodes, addEdges]);

  const palette = useMemo(() => ({
    groups: [
      { label: 'Arricchisci', items: [
        // Nomi, icone e descrizioni dal glossario; `id` e' la finestra da aprire.
        paletteEntry('connector_manual', { id: 'manual', kind: 'connector' }),
        paletteEntry('connector_openapi', { id: 'openapi', kind: 'connector' }),
        paletteEntry('connector_rest_file', { id: 'rest_file', kind: 'connector' }),
      ] },
      { label: 'Uscite', items: [
        paletteEntry('output', { id: 'result', kind: 'result' }),
        paletteEntry('load_db', { id: 'carica', kind: 'carica' }),
        paletteEntry('load_arcgis', { id: 'carica_arcgis', kind: 'carica_arcgis' }),
      ] },
    ],
    onPick: (item, afterId) => {
      if (item.kind === 'connector') { paletteAfterRef.current = afterId; modals.open(item.id); return; }
      if (item.kind === 'carica') { handleAddCaricanNode(); return; }
      if (item.kind === 'carica_arcgis') { handleAddCaricanArcGISNode(); return; }
      if (nodesRef.current.some(n => n.type === 'result')) { toast.warning(onlyOneMessage('output')); return; }
      placeAfter({ id: `result_${Date.now()}`, type: 'result',
        position: { x: 600, y: 200 },
        data: { isArricchisci: true, fileName: 'output', format: 'csv', availableColumns: [],
          order_by_enabled: false, order_by_columns: [],
          having_enabled: false, having_conditions: [] } }, afterId, false);
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [modals, handleAddCaricanNode, handleAddCaricanArcGISNode, placeAfter, toast]);

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
  const onDragStart        = (event, nodeType, nodeData = {}) => { event.dataTransfer.setData('application/reactflow', nodeType); event.dataTransfer.setData('nodeData', JSON.stringify(nodeData)); event.dataTransfer.effectAllowed = 'move'; };
  const onDragOver         = useCallback((event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }, []);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const nodeType    = event.dataTransfer.getData('application/reactflow');
    const nodeDataStr = event.dataTransfer.getData('nodeData');
    if (!nodeType) return;
    const nodeData = nodeDataStr ? JSON.parse(nodeDataStr) : {};
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const newNodeId = `${nodeType}_${Date.now()}`;
    let newNode;

    if (nodeType === 'project_source') newNode = { id: newNodeId, type: 'project_source', position,
      data: { source_project_id: nodeData.source_project_id || nodeData.id, columns: [] } };
    else if (nodeType === 'file_source') newNode = { id: newNodeId, type: 'file_source', position,
      data: { file_source_id: nodeData.file_source_id || nodeData.id, columns: [] } };
    else if (nodeType === 'api_enrich') newNode = { id: newNodeId, type: 'api_enrich', position,
      data: { api_id: '', param_mapping: {}, columns: [] } };

    if (newNode) addNodes(newNode);
  }, [screenToFlowPosition, addNodes]);

  const { confirmNavigation } = useUnsavedChangesGuard();
  const handleBack     = () => { if (confirmNavigation()) navigate('/'); };
  const hasResultNode  = nodes.some(n => n.type === 'result');
  const hasCaricanNode = nodes.some(n => n.type === 'carica');
  const hasCaricanArcGISNode = nodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis');

  // beforeCanvas slot
  const accessDeniedBanner = <AccessDeniedBanner nodes={nodes} />;

  // topbar slot
  // Barra unica (Fase 4, M7): le fonti di arricchimento e le altre azioni
  // nel menu "⋯", un solo "▶ Esegui".
  const toolbar = {
    mode: 'arricchisci',
    title: project?.metadata?.name,
    onBack: handleBack,
    onSave: handleSaveCanvas,
    saving,
    executing: execution.isExecuting,
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
      menuEntry('runtime_input', { onClick: handleAddProjectInputNode }),
      menuEntry('static_values', { onClick: handleAddStaticValuesNode }),
      { key: 'div2', divider: true },
      menuEntry('output', { onClick: handleAddResultNode, disabled: hasResultNode }),
      menuEntry('load_db', { onClick: handleAddCaricanNode, disabled: !hasResultNode || hasCaricanNode }),
      menuEntry('load_arcgis', { onClick: handleAddCaricanArcGISNode, disabled: !hasResultNode || hasCaricanArcGISNode }),
      { key: 'div3', divider: true },
      menuEntry('versions', { onClick: () => setShowVersions(true) }),
    ],
  };

  // sidebar slot
  const sidebar = !isReadOnly ? (
    <EditorSidebar
      variant="arricchisci" projects={availableSources} files={availableFiles} onDragStart={onDragStart}
      projectsConfig={{ title: '📊 Sorgenti Progetto', subtitle: 'Trascina un progetto nel canvas',
        emptyMessage: 'Nessun progetto disponibile.', emptyHint: 'Crea prima un progetto Estrai, Manipola o Arricchisci.',
        filterTypes: ['estrai', 'manipola', 'arricchisci'], showSearch: true }}
      filesConfig={{ title: '📁 File Esterni', subtitle: 'Trascina un file nel canvas',
        emptyMessage: 'Nessun file disponibile.', uploadLink: '/files', uploadLinkText: 'Carica file esterni' }}
    />
  ) : null;

  // modals slot — `modals` (var name) e' gia' occupato da useMultiModal:
  // usiamo `modalsSlot` per evitare collisione col chiamante .isOpen/.close.
  const modalsSlot = (
    <ModalErrorBoundary>
      <Suspense fallback={null}>
      {showVersions && (
        <CanvasVersionsModal isOpen onClose={() => setShowVersions(false)} projectId={projectId} />
      )}
      <ExecutionModal isOpen={execution.showModal} onClose={() => execution.closeModal()} isExecuting={execution.isExecuting} result={execution.result} onDownload={execution.downloadTaskResult} title={`${term('run').icon} Esecuzione dell'arricchimento`} projectType="arricchisci" progress={execution.progress} progressMessage={execution.progressMessage} taskStatus={execution.taskStatus} onCancel={execution.cancelTask} caricaTasks={execution.caricaTasks} caricaPolling={execution.caricaPolling} awaitingLaunch={execution.awaitingLaunch} onLaunch={execution.launch} user={user} inputDefinitions={runtimeInputDefinitions} />
      <PreviewModal isOpen={preview.showModal} onClose={preview.closeModal} onRefresh={limit => preview.handlePreview(limit)}
        data={(() => {
          if (!preview.data?.columns) return preview.data;
          const resultNode = nodes.find(n => n.type === 'result');
          const availableCols = resultNode?.data?.availableColumns || [];
          const nodeLabelMap  = buildNodeLabelMap(nodes);
          // Mappa name → label sorgente dalle availableColumns del resultNode.
          // Per api_enrich la source è già 'api_enrich', per gli altri è un nodeId o un UUID.
          const sourceMap = {};
          availableCols.forEach(col => {
            if (col.source) {
              // Prova a risolvere via mappa (nodeId), altrimenti usa il valore grezzo
              sourceMap[col.name] = nodeLabelMap[col.source] || col.source;
            }
          });
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
        })()}
        loading={preview.loading} error={preview.error}
        permissionRevoked={preview.permissionRevoked}
        title={`${term('preview').name} dell'arricchimento`} projectType="arricchisci" />
      <ConnectionSelectorModal isOpen={modals.isOpen('template')} onClose={() => modals.close('template')} onSelect={conn => handleSelectConnection(conn, 'template')} category="template" title="Seleziona un modello di API" icon={term('connector_template').icon} emptyMessage="Nessun modello di API configurato" emptyHint="Crea una connessione da un modello nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('manual')} onClose={() => modals.close('manual')} onSelect={conn => handleSelectConnection(conn, 'manual')} category="manual" title="Seleziona un'API manuale" icon={term('connector_manual').icon} emptyMessage="Nessuna API manuale configurata" emptyHint="Crea una nuova connessione API nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('openapi')} onClose={() => modals.close('openapi')} onSelect={conn => handleSelectConnection(conn, 'openapi')} category="openapi" title="Seleziona un'API OpenAPI" icon={term('connector_openapi').icon} emptyMessage="Nessuna API OpenAPI importata" emptyHint="Importa uno schema OpenAPI nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('rest_file')} onClose={() => modals.close('rest_file')} onSelect={conn => handleSelectConnection(conn, 'rest_file')} category="rest_file" title="Seleziona un processore file" icon={term('connector_rest_file').icon} emptyMessage="Nessun processore file configurato" emptyHint="Configura un servizio REST che riceve e restituisce file nella pagina API Esterne" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('datasource')} onClose={() => modals.close('datasource')} onSelect={conn => handleSelectConnection(conn, 'datasource')} category="datasource" title="Seleziona un Data Source" icon={term('datasource').icon} emptyMessage="Nessun Data Source configurato" emptyHint="Configura un Data Source nella pagina Data Source" createLink="/datasources" />
      <EnrichmentConfirmModal isOpen={showConfirmModal} onClose={handleCloseConfirmModal} onConfirm={handleConfirmExecution} validation={enrichmentValidation.validation} estimation={enrichmentValidation.estimation} isValidating={enrichmentValidation.isValidating} isEstimating={enrichmentValidation.isEstimating} formatTime={enrichmentValidation.formatTime} />
      </Suspense>
    </ModalErrorBoundary>
  );

  return (
    <EditorShell
      projectId={projectId}
      prefix="arricchisci"
      loading={loading}
      loadError={loadError}
      project={project}
      reload={reloadData}
      onBack={handleBack}
      nodes={nodes}
      edges={edges}
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

const ArricchisciEditor = () => (
  <ReactFlowProvider>
    <ConfirmProvider>
      <ArricchisciEditorContent />
    </ConfirmProvider>
  </ReactFlowProvider>
);

export default ArricchisciEditor;
