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
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from 'reactflow';
import 'reactflow/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { ConfirmProvider } from '../context/ConfirmContext';
import { executeEnrichmentAsync } from '../api';
import { useAuth } from '../context/AuthContext';
import { useEditorData } from '../hooks/useEditorData';
import { describeAccessDeniedNodes } from '../utils/canvasAccessControl';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import useCanvasStore from '../store/useCanvasStore';
import ValidationStatusBadge from '../components/editor/ValidationStatusBadge';
import { deserializeNodes } from '../utils/nodeFactories';
import EditorSidebar from '../components/panels/EditorSidebar';
import ProjectSourceNode from '../components/nodes/ProjectSourceNode';
import FileSourceNode from '../components/nodes/FileSourceNode';
import ApiEnrichNode from '../components/panels/ApiEnrichNode';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';
import ImdfPublisherNode from '../components/nodes/ImdfPublisherNode';
import PlenoraLogo from '../components/common/PlenoraLogo';
import StaticValuesNode from '../components/nodes/StaticValuesNode';

// Modali lazy-loaded
const CaricaricaModal = lazy(() => import('../components/modals/CaricaricaModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ConnectionSelectorModal = lazy(() => import('../components/arricchisci/ConnectionSelectorModal'));
const EnrichmentConfirmModal = lazy(() => import('../components/modals/EnrichmentConfirmModal'));
import { usePreview, useExecution, useCanvasCore, useMultiModal, useEnrichmentValidation, useCaricarica, useCanvasSave, useUnsavedChangesGuard, useValidationHighlights } from '../hooks';
import { useToast } from '../hooks/useToast';
import { useTheme } from '../hooks/useTheme';
import '../styles/ArricchisciEditor.css';
import '../components/arricchisci/ConnectionSelectorModal.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/modals/EnrichmentConfirmModal.css';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';

/**
 * Costruisce una mappa nodeId → label leggibile per il raggruppamento colonne in PreviewModal.
 */
const buildNodeLabelMap = (nodes) => {
  const map = {};
  nodes.forEach(node => {
    const d = node.data || {};
    let label;
    switch (node.type) {
      case 'project_source':  label = d.project_name || d.source_project_id?.slice(0, 6) || 'Progetto'; break;
      case 'file_source':     label = d.file_name    || d.file_source_id?.slice(0, 6)    || 'File';     break;
      case 'api_enrich':      label = d.api_name     || 'API';    break;
      case 'featureLayerNode':label = d.layer_name   || 'Layer';  break;
      default:                label = d.label        || node.type; break;
    }
    map[node.id] = label;
  });
  return map;
};

// Costante stabile — evita array literal nelle deps di useEditorData
const ARRICCHISCI_SOURCE_TYPES = ['estrai', 'manipola', 'arricchisci'];

// Costanti stabili fuori dal componente — evita re-render loop in ReactFlow StoreUpdater
// Qualsiasi oggetto/funzione inline nelle props di ReactFlow causa Maximum update depth
const NODE_COLORS = { project_source: '#4CAF50', file_source: '#3F51B5', api_enrich: '#FF6B35', result: '#2196F3', staticValues: '#667eea', carica: '#7c3aed', imdf_publisher: '#7C3AED' };
const nodeColorFn = (node) => NODE_COLORS[node.type] || '#999';
const FIT_VIEW_OPTIONS = { padding: 0.2 };

const nodeTypes = {
  project_source: ProjectSourceNode, file_source: FileSourceNode,
  api_enrich: ApiEnrichNode, result: ResultNode,
  staticValues: StaticValuesNode, carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode,
  imdf_publisher: ImdfPublisherNode
};

const defaultEdgeOptions = {
  type: 'smoothstep', animated: true,
  style: { stroke: '#FF6B35', strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#FF6B35' }
};

const ArricchisciEditorContent = () => {
  const { projectId } = useParams();

  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { screenToFlowPosition } = useReactFlow();

  const { theme, setTheme } = useTheme();
  const hexCanvasRef = useRef(null);
  useHexCanvas(hexCanvasRef);

  const toast = useToast();

  const {
    nodes, edges, setNodes, setEdges, onNodesChange, onEdgesChange,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    handleSaveQuiet, getViewportCenter,
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
  const [showEnrichDropdown, setShowEnrichDropdown] = useState(false);

  const modals = useMultiModal(['template', 'openapi', 'manual', 'datasource']);

  const enrichmentValidation = useEnrichmentValidation();
  const preview   = usePreview(projectId, handleSaveQuiet);
  const { saving, handleSaveCanvas } = useCanvasSave(handleSaveQuiet, {
    logPrefix: '[ArricchisciEditor]',
  });
  const execution = useExecution(projectId, {
    executeFunction: executeEnrichmentAsync,
    // Tutti i guard (CANVAS_EMPTY, ARRICCHISCI_NO_SOURCES, ARRICCHISCI_NO_ENRICHER)
    // sono coperti da `validateArricchisciCanvas` (chiamato da `ResultNode.
    // handleExecuteWithValidation` prima di `onExecute`). onBeforeExecute
    // raggiunto solo a validation-passed.
    onBeforeExecute: () => handleSaveQuiet(),
  });
  const caricarica = useCaricarica(projectId, {
    onBeforeExecute: handleSaveQuiet,
    getCanvas: () => ({ nodes: nodesRef.current, edges: edgesRef.current })
  });

  useEffect(() => {
    if (!project?.canvas?.nodes || loading) return;

    const loadedNodes = deserializeNodes(project.canvas.nodes, {
      flags: { isArricchisci: true },
    });

    setNodes(loadedNodes);
    // Corregge edges con targetHandle/sourceHandle "undefined" (salvati in canvas precedenti)
    // NON si elimina: si corregge al valore standard del nodo destinatario
    const sanitizedEdges = (project.canvas.edges || []).map(e => {
      const clean = { ...e };
      if (!clean.targetHandle || clean.targetHandle === 'undefined') clean.targetHandle = 'input';
      if (!clean.sourceHandle || clean.sourceHandle === 'undefined') clean.sourceHandle = 'output';
      return clean;
    });
    setEdges(sanitizedEdges);
    // Reset dirty: setNodes/setEdges hanno marcato dirty perché inseriscono
    // nuovi ID, ma è un load programmatico, non una modifica utente.
    useCanvasStore.getState().markClean();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // setNodes/setEdges/deserializeNodes omessi: load programmatico una sola
    // volta per project, non ad ogni re-render dei setter store.
  }, [project, loading]);


  // Aggiorna availableColumns in ResultNode e CaricanNode
  // Fingerprint stabile dei campi che devono triggerare il refresh di
  // `availableColumns` sui result/carica nodes. Pre-fix (P0 frontend-pages
  // #1): deps = [setNodes] significava firing ONLY al mount; il cambio
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
      if (n.type === 'project_source' || n.type === 'file_source') {
        return { id: n.id, type: n.type, cols: n.data?.columns };
      }
      if (n.type === 'api_enrich') {
        return { id: n.id, type: n.type, output_mapping: n.data?.output_mapping };
      }
      return null;
    }).filter(Boolean);
    return JSON.stringify(relevant);
  }, [nodes]);

  useEffect(() => {
    // NOTA: 'nodes' rimosso dalle dipendenze intenzionalmente per evitare loop React.
    // Tutto il calcolo avviene dentro la callback di setNodes (snapshot aggiornato).
    // La dep `availableColumnsFingerprint` triggera il refresh quando
    // input rilevanti cambiano (source cols / api_enrich output_mapping),
    // senza catturare modifiche a campi scritti dall'effect stesso.
    setNodes(nds => {
      const resultNode = nds.find(n => n.type === 'result');
      if (!resultNode) return nds;

      const availableColumns = [];
      for (const node of nds) {
        const cols = node.data?.columns;
        if (!cols) continue;
        const source = node.data?.source_project_id || node.data?.file_source_id || node.type;

        if (node.type === 'project_source' || node.type === 'file_source') {
          cols.forEach(col => {
            if (typeof col === 'object' && col !== null)
              availableColumns.push({ name: col.name || col.alias || 'unknown', type: col.type || col.dtype || 'str', source });
            else if (typeof col === 'string')
              availableColumns.push({ name: col, type: 'str', source });
          });
        } else if (node.type === 'api_enrich' && node.data?.output_mapping) {
          node.data.output_mapping.forEach(mapping =>
            availableColumns.push({ name: mapping.column || mapping.target_column || mapping.name, type: mapping.type || 'str', source: 'api_enrich' })
          );
        }
      }

      if (JSON.stringify(availableColumns) === JSON.stringify(resultNode.data?.availableColumns || [])) return nds;
      return nds.map(node => {
        if (node.type === 'result' || node.type === 'carica' || node.type === 'carica_arcgis' || node.type === 'caricaArcgis') return { ...node, data: { ...node.data, availableColumns } };
        return node;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setNodes, availableColumnsFingerprint]);

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
    // Pre-flight access_denied: backend (RB.4.B/RB.7.B) marca i nodi che
    // riferiscono risorse non visibili al gruppo del progetto. Blocca il
    // run prima di chiamare il backend (che fallirebbe a runtime con
    // _assert_file_source_group_access — rete di sicurezza preservata).
    // NB: access_denied NON e' nel validator — resta come unico guard pre-confirm.
    const denied = describeAccessDeniedNodes(currentNodes);
    if (denied) {
      toast.error('Esecuzione bloccata: rimuovi o sostituisci i nodi con risorse non accessibili.');
      console.warn('[run-blocked] access_denied nodes:\n' + denied.summary);
      return;
    }
    await handleSaveQuiet();
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
      execution.prepareExecute({ format, ...options });
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
  }, [handleSaveQuiet, getEnrichmentConfig, projectId, enrichmentValidation, execution, exportFormat]);

  const handleConfirmExecution  = useCallback(() => {
    setShowConfirmModal(false);
    // Leggi il formato dal nodo Result al momento dell'esecuzione
    const resultNode = nodesRef.current.find(n => n.type === 'result');
    const format = resultNode?.data?.format || exportFormat || 'csv';
    execution.prepareExecute({ format, ...pendingExecuteOptions });
    setPendingExecuteOptions({});
  }, [execution, exportFormat, pendingExecuteOptions]);
  const handleCloseConfirmModal = useCallback(() => { setShowConfirmModal(false); enrichmentValidation.reset(); }, [enrichmentValidation]);

  // Registra azioni editor nello store — dopo tutti gli hook e useCallback
  useEffect(() => {
    useCanvasStore.getState().setEditorActions({
      onPreview:        () => preview.handlePreview(),
      onExecute:        handleShowConfirmModal,
      onCaricanExecute: (nodeId, cfg) => caricarica.handleExecute(nodeId, cfg),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // re-bind solo al cambio degli handler hook delle azioni canvas; setStore
    // letto via getState() volutamente non in deps.
  }, [preview.handlePreview, handleShowConfirmModal, caricarica.handleExecute]);

  // Sincronizza connections nello store
  useEffect(() => {
    useCanvasStore.getState().setConnections(connections);
  }, [connections]);

  // Outline amber sui nodi con issue di validazione (popolati dallo store
  // da ResultNode.runValidation). Hook condiviso con ManipolaEditor.
  useValidationHighlights(setNodes);

  const getPos = () => getViewportCenter({ screenToFlowPosition });

  const handleSelectConnection = useCallback((connection, category) => {
    const newNodeId = `api_enrich_${Date.now()}`;
    setNodes(nds => nds.concat({ id: newNodeId,
      type: category === 'datasource' ? 'project_source' : 'api_enrich',
      // Posizionamento deterministico al centro del viewport corrente,
      // allineato al pattern degli altri `handleAdd*Node` di questo file
      // (vedi `handleAddResultNode` etc.) che usano `getPos()`. Pre-fix
      // `Math.random()` produceva posizioni non riproducibili e potenzialmente
      // fuori dal viewport visibile su zoom alto.
      position: getPos(),
      data: { api_id: connection.id, api_name: connection.name, param_mapping: {}, columns: [],
        enrich_type: category, api_type: connection.api_type, endpoint: connection.endpoint,
        auth: connection.auth, parameters: connection.parameters, output_mapping: connection.output_mapping,
        static_parameters: connection.static_parameters || {},
        ...(category === 'rest_file'  && { connection_type: 'rest_file',  request: connection.request, response: connection.response }),
        ...(category === 'datasource' && { source_type: 'datasource', datasource_config: connection.datasource }),
        ...(category === 'openapi' && { openapi_spec: connection.openapi_spec }) } }));
    if (category === 'template')   modals.close('template');
    if (category === 'manual')     modals.close('manual');
    if (category === 'openapi')    modals.close('openapi');
    if (category === 'rest_file')  modals.close('rest_file');
    if (category === 'datasource') modals.close('datasource');
  }, [setNodes]);

  const handleAddStaticValuesNode = useCallback(() => {
    setNodes(nds => nds.concat({ id: `static_${Date.now()}`, type: 'staticValues',
      position: { x: 100, y: 200 },
      data: { label: 'Valori Statici', fields: [] } }));
  }, [setNodes]);

  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'result')) { toast.warning('Puoi avere solo un nodo risultato per progetto'); return; }
    setNodes(nds => nds.concat({ id: `result_${Date.now()}`, type: 'result',
      position: { x: 600, y: 200 },
      data: { isArricchisci: true, fileName: 'output', format: 'csv', availableColumns: [],
        order_by_enabled: false, order_by_columns: [],
        having_enabled: false, having_conditions: [] } }));
  }, [setNodes]);

  const handleAddCaricanNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'result')) { toast.warning('Aggiungi prima un nodo Output'); return; }
    if (currentNodes.some(n => n.type === 'carica'))  { toast.warning('Puoi avere solo un nodo CARICA per progetto'); return; }
    const resultNode = currentNodes.find(n => n.type === 'result');
    const newNode = { id: `carica_${Date.now()}`, type: 'carica',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y },
      data: { connection_id: '', schema_name: '', table_name: '', write_mode: 'create',
        column_mappings: [], match_columns: [], batch_size: 1000,
        transaction_mode: 'all_or_nothing', create_spatial_index: true, conflict_policy: 'abort',
        availableColumns: resultNode.data?.availableColumns || [] } };
    setNodes(nds => nds.concat(newNode));
    setEdges(eds => eds.concat({ id: `edge_result_carica_${Date.now()}`, source: resultNode.id, target: newNode.id,
      type: 'smoothstep', animated: true, style: { stroke: '#7c3aed', strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#7c3aed' }, data: {} }));
  }, [setNodes, setEdges]);

  const handleAddCaricanArcGISNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'result')) { toast.warning('Aggiungi prima un nodo Output'); return; }
    if (currentNodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis')) { toast.warning('Puoi avere solo un nodo CARICA ArcGIS per progetto'); return; }
    const resultNode = currentNodes.find(n => n.type === 'result');
    const newNode = {
      id: `carica_arcgis_${Date.now()}`, type: 'carica_arcgis',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y + 200 },
      data: { arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '',
        arcgis_operation: 'add', arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
        column_mappings: [], availableColumns: resultNode.data?.availableColumns || [] }
    };
    setNodes(nds => nds.concat(newNode));
    setEdges(eds => eds.concat({ id: `edge_result_carica_arcgis_${Date.now()}`, source: resultNode.id, target: newNode.id,
      label: 'Carica ArcGIS', animated: true, style: { stroke: '#2DD4BF', strokeWidth: 2 }, data: { join_type: 'carica_arcgis' } }));
  }, [setNodes, setEdges]);

  const handleAddImdfPublisherNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    // Richiede un api_enrich con output_kind='imdf' come sorgente
    const imdfEnrich = currentNodes.find(n =>
      n.type === 'api_enrich' && (n.data?.output_kind === 'imdf')
    );
    if (!imdfEnrich) {
      toast.warning('Serve prima un nodo Processore File con output IMDF');
      return;
    }
    if (currentNodes.some(n => n.type === 'imdf_publisher')) {
      toast.warning('Puoi avere solo un nodo IMDF Publisher per progetto');
      return;
    }
    const newNode = {
      id: `imdf_publisher_${Date.now()}`, type: 'imdf_publisher',
      position: { x: imdfEnrich.position.x + 400, y: imdfEnrich.position.y + 220 },
      data: {
        imdf_arcgis_connection_id: '',
        imdf_arcgis_folder:        '',
        imdf_fs_name_template:     '',
        imdf_apply_renderers:      true,
      }
    };
    setNodes(nds => nds.concat(newNode));
    setEdges(eds => eds.concat({
      id: `edge_imdf_pub_${Date.now()}`,
      source: imdfEnrich.id, target: newNode.id,
      label: 'IMDF', animated: true,
      style: { stroke: '#7C3AED', strokeWidth: 2 },
      data: { join_type: 'imdf_publisher' }
    }));
  }, [setNodes, setEdges, toast]);

  const onConnect = useCallback((params) => {
    // Normalizza handles: undefined → 'input'/'output'
    const cleanParams = { ...params };
    if (!cleanParams.targetHandle || cleanParams.targetHandle === 'undefined') cleanParams.targetHandle = 'input';
    if (!cleanParams.sourceHandle || cleanParams.sourceHandle === 'undefined') cleanParams.sourceHandle = 'output';
    setEdges(eds => addEdge({ ...cleanParams, ...defaultEdgeOptions }, eds));
  }, [setEdges]);
  const onEdgeContextMenu  = useCallback((event, edge) => { event.preventDefault(); setEdges(eds => eds.filter(e => e.id !== edge.id)); }, [setEdges]);
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

    if (newNode) setNodes(nds => nds.concat(newNode));
  }, [screenToFlowPosition, setNodes]);

  const { confirmNavigation } = useUnsavedChangesGuard();
  const handleBack     = () => { if (confirmNavigation()) navigate('/'); };
  const hasResultNode  = nodes.some(n => n.type === 'result');
  const hasCaricanNode = nodes.some(n => n.type === 'carica');
  const hasCaricanArcGISNode = nodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis');
  const hasImdfEnrich        = nodes.some(n => n.type === 'api_enrich' && n.data?.output_kind === 'imdf');
  const hasImdfPublisherNode = nodes.some(n => n.type === 'imdf_publisher');

  // beforeCanvas slot
  const accessDeniedBanner = <AccessDeniedBanner nodes={nodes} />;

  // topbar slot
  const topbar = (
    <div className="arricchisci-toolbar">
        <div className="arricchisci-toolbar__left">
          <div className="arricchisci-toolbar__logo">
            <PlenoraLogo gridLines="left" plenClassName="arricchisci-toolbar__logo-text" />
          </div>
          <div className="arricchisci-toolbar__sep" />
          <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--back" onClick={handleBack}>← Indietro</button>
        </div>
        <div className="arricchisci-toolbar__center">
          <h2 className="arricchisci-toolbar__title">{project?.metadata?.name}</h2>
          <span className="arricchisci-toolbar__badge">ARRICCHISCI</span>
        </div>
        <div className="arricchisci-toolbar__right">
          {!isReadOnly && (
            <>
              <div className="arricchisci-toolbar__dropdown" onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setShowEnrichDropdown(false); }}>
                <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--api" onClick={() => setShowEnrichDropdown(v => !v)}>🔗 + Enrichment ▼</button>
                {showEnrichDropdown && (
                <div className="arricchisci-toolbar__dropdown-content">
                  <button type="button" onClick={() => { modals.open('template');   setShowEnrichDropdown(false); }}>🍪 Da Template</button>
                  <button type="button" onClick={() => { modals.open('manual');     setShowEnrichDropdown(false); }}>✏️ API Manuale</button>
                  <button type="button" onClick={() => { modals.open('openapi');    setShowEnrichDropdown(false); }}>📋 OpenAPI</button>
                  <button type="button" onClick={() => { modals.open('rest_file');  setShowEnrichDropdown(false); }}>📦 Processore File</button>
                  <button type="button" onClick={() => { modals.open('datasource'); setShowEnrichDropdown(false); }}>📁 Data Source</button>
                </div>
                )}
              </div>
              <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--static" onClick={handleAddStaticValuesNode} title="Aggiungi valori statici con variabili template">📝 Valori Statici</button>
              <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--output" onClick={handleAddResultNode}>📁 Output</button>
              <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--carica" onClick={handleAddCaricanNode} disabled={!hasResultNode || hasCaricanNode} title="Aggiungi nodo per caricare su database">💾 Carica DB</button>
              <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--carica" onClick={handleAddCaricanArcGISNode} disabled={!hasResultNode || hasCaricanArcGISNode} title="Aggiungi nodo Carica ArcGIS" style={{ borderColor: 'rgba(45,212,191,0.4)', color: '#2DD4BF' }}>🌐 Carica ArcGIS</button>
              <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--carica" onClick={handleAddImdfPublisherNode} disabled={!hasImdfEnrich || hasImdfPublisherNode} title="Pubblica il bundle IMDF su un Feature Service ArcGIS (richiede upstream IMDF)" style={{ borderColor: 'rgba(124,58,237,0.4)', color: '#7C3AED' }}>🗺️ Pubblica IMDF</button>
            </>
          )}
          <ValidationStatusBadge />
          <div className="arricchisci-theme-toggle">
            <div className={`arricchisci-theme-opt${theme==='dark'?' arricchisci-theme-opt--on':''}`} onClick={() => setTheme('dark')}>🌙 Scuro</div>
            <div className={`arricchisci-theme-opt${theme==='light'?' arricchisci-theme-opt--on':''}`} onClick={() => setTheme('light')}>☀️ Chiaro</div>
          </div>
          {!isReadOnly && (
            <button type="button" className="arricchisci-toolbar__btn arricchisci-toolbar__btn--save" onClick={handleSaveCanvas} disabled={saving}>{saving ? 'Salvataggio...' : '💾 Salva'}</button>
          )}
        </div>
      </div>
  );

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
      <ExecutionModal isOpen={execution.showModal} onClose={() => execution.closeModal()} isExecuting={execution.isExecuting} result={execution.result} onDownload={execution.downloadTaskResult} title="🔗 Esegui Arricchimento" projectType="arricchisci" progress={execution.progress} progressMessage={execution.progressMessage} taskStatus={execution.taskStatus} onCancel={execution.cancelTask} caricaTasks={execution.caricaTasks} caricaPolling={execution.caricaPolling} awaitingLaunch={execution.awaitingLaunch} onLaunch={execution.launch} user={user} />
      <CaricaricaModal isOpen={caricarica.showModal} onClose={() => caricarica.closeModal()} isExecuting={caricarica.isExecuting} result={caricarica.result} progress={caricarica.progress} progressMessage={caricarica.progressMessage} taskStatus={caricarica.taskStatus} caricaricaInfo={caricarica.caricaricaInfo} onCancel={caricarica.cancelTask} />
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
        title="Anteprima Arricchimento" projectType="arricchisci" />
      <ConnectionSelectorModal isOpen={modals.isOpen('template')} onClose={() => modals.close('template')} onSelect={conn => handleSelectConnection(conn, 'template')} category="template" title="Seleziona Template API" icon="🍪" emptyMessage="Nessun template API configurato" emptyHint="Crea una connessione da template in Gestione API" createLink="/external-connectors" />
      <ConnectionSelectorModal isOpen={modals.isOpen('manual')} onClose={() => modals.close('manual')} onSelect={conn => handleSelectConnection(conn, 'manual')} category="manual" title="Seleziona API Manuale" icon="✏️" emptyMessage="Nessuna API manuale configurata" emptyHint="Crea una nuova connessione API in Gestione API" createLink="/api-connections" />
      <ConnectionSelectorModal isOpen={modals.isOpen('openapi')} onClose={() => modals.close('openapi')} onSelect={conn => handleSelectConnection(conn, 'openapi')} category="openapi" title="Seleziona API OpenAPI" icon="📋" emptyMessage="Nessuna API OpenAPI importata" emptyHint="Importa uno schema OpenAPI in Gestione API" createLink="/api-connections" />
      <ConnectionSelectorModal isOpen={modals.isOpen('rest_file')} onClose={() => modals.close('rest_file')} onSelect={conn => handleSelectConnection(conn, 'rest_file')} category="rest_file" title="Seleziona Processore File" icon="📦" emptyMessage="Nessun Processore File configurato" emptyHint="Configura un connettore REST file-in/file-out in Gestione API" createLink="/api-connections" />
      <ConnectionSelectorModal isOpen={modals.isOpen('datasource')} onClose={() => modals.close('datasource')} onSelect={conn => handleSelectConnection(conn, 'datasource')} category="datasource" title="Seleziona Data Source" icon="📁" emptyMessage="Nessun Data Source configurato" emptyHint="Configura una connessione dati in Gestione API" createLink="/api-connections" />
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
        minZoom: 0.5,
        maxZoom: 2,
        backgroundProps: { color: '#ddd', gap: 20 },
        miniMapProps: { className: 'arricchisci-minimap', nodeColor: nodeColorFn, zoomable: true, pannable: true },
        isReadOnly,
        readOnlyProps,
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
        topbar,
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
