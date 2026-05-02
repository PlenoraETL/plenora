/**
 * ArricchisciEditor.jsx — Editor visuale per il modulo ARRICCHISCI
 *
 * Permette di costruire pipeline di arricchimento dati collegando:
 *  - Nodi sorgente (progetti Estrai/Manipola, file, datasource)
 *  - Nodi ApiEnrich (API esterne, eseguibili, datasource)
 *  - Nodo valori statici con template variabili ({{NOW}}, {{TODAY}}, ...)
 *  - Nodo Output con mapping risposta JSON
 *
 * Hooks usati: useEditorData, useCanvasCore, useNodeHandlers,
 *              useEdgeHandlers, useEnrichmentValidation, useExecution,
 *              usePreview, useMultiModal, useToast
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
import { getFiles } from '../api/files';
import { useAuth } from '../context/AuthContext';
import { useEditorData } from '../hooks/useEditorData';
import {
  hasAccessDeniedNodes,
  describeAccessDeniedNodes,
  getAccessDeniedNodes,
} from '../utils/canvasAccessControl';
import useCanvasStore from '../store/useCanvasStore';
import { deserializeNodes, createEdgeFromData } from '../utils/nodeFactories';
import EditorSidebar from '../components/panels/EditorSidebar';
import ProjectSourceNode from '../components/nodes/ProjectSourceNode';
import FileSourceNode from '../components/nodes/FileSourceNode';
import ApiEnrichNode from '../components/panels/ApiEnrichNode';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';
import ImdfPublisherNode from '../components/nodes/ImdfPublisherNode';
import StaticValuesNode from '../components/nodes/StaticValuesNode';

// Modali lazy-loaded
const CaricaricaModal = lazy(() => import('../components/modals/CaricaricaModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ConnectionSelectorModal = lazy(() => import('../components/arricchisci/ConnectionSelectorModal'));
const EnrichmentConfirmModal = lazy(() => import('../components/modals/EnrichmentConfirmModal'));
import { usePreview, useExecution, useCanvasCore, useMultiModal, useEnrichmentValidation, useCaricarica } from '../hooks';
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

  // ===== THEME =====
  const { theme, setTheme } = useTheme();
  const hexCanvasRef = useRef(null);
  useHexCanvas(hexCanvasRef);

  const toast = useToast();

  // ===== CANVAS CORE =====
  // Sostituisce: useNodesState, useEdgesState, useNodeHandlers, nodesRef, edgesRef,
  //             handleSaveQuiet, useCanvasKeyboard, getViewportCenter
  const {
    nodes, edges, setNodes, setEdges, onNodesChange, onEdgesChange,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    handleSaveQuiet, getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'arricchisci' });

  // ===== STATE SPECIFICO ARRICCHISCI =====
  // ===== DATA LOADING (centralizzato) =====
  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: ARRICCHISCI_SOURCE_TYPES,
  });

  // ===== STATE SPECIFICO ARRICCHISCI =====
  const [saving, setSaving] = useState(false);
  const [exportFormat, setExportFormat]         = useState('csv');
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [showEnrichDropdown, setShowEnrichDropdown] = useState(false);

  const modals = useMultiModal(['template', 'openapi', 'manual', 'datasource']);

  // ===== HOOKS SPECIFICI ARRICCHISCI =====
  const enrichmentValidation = useEnrichmentValidation();
  const preview   = usePreview(projectId, handleSaveQuiet);
  const execution = useExecution(projectId, {
    executeFunction: executeEnrichmentAsync,
    onBeforeExecute: async () => {
      const currentNodes = nodesRef.current;
      if (currentNodes.length === 0)          throw new Error('Canvas vuoto. Aggiungi nodi prima di eseguire.');
      const enrichers = currentNodes.filter(n => n.type === 'api_enrich');
      const anyNeedsSource = enrichers.some(n => {
        if (n.data?.api_type === 'generator') return false;
        const staticParameters = n.data?.static_parameters || {};
        return (n.data?.parameters || []).some(p => p.required && p.mode !== 'fixed' && !(p.name in staticParameters));
      });
      if (anyNeedsSource && !currentNodes.find(n => n.type === 'project_source' || n.type === 'file_source' || n.type === 'staticValues'))
                                              throw new Error('Aggiungi almeno una sorgente dati (progetto, file o valori statici).');
      if (!currentNodes.find(n => n.type === 'api_enrich')) throw new Error('Aggiungi almeno un nodo API Enrich.');
      await handleSaveQuiet();
    }
  });
  const caricarica = useCaricarica(projectId, {
    onBeforeExecute: handleSaveQuiet,
    getCanvas: () => ({ nodes: nodesRef.current, edges: edgesRef.current })
  });

  // ===== EFFECTS =====

  // ===== EFFECT: carica nodi nel canvas quando il progetto e pronto =====
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
  useEffect(() => {
    // NOTA: 'nodes' rimosso dalle dipendenze intenzionalmente per evitare loop React.
    // Tutto il calcolo avviene dentro la callback di setNodes (snapshot aggiornato).
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
    // 'nodes' volutamente assente: il calcolo avviene dentro la callback di
    // setNodes (snapshot React aggiornato) per evitare il loop nodes->effect.
  }, [setNodes]);

  // ===== NODE HANDLERS =====

  // NOTA: le colonne dei nodi source vengono aggiornate direttamente
  // dai nodi stessi tramite useCanvasStore.getState().updateNodeData
  // Non servono handler passati come prop.

  // ===== ENRICHMENT CONFIRM FLOW =====

  const getEnrichmentConfig = useCallback(() => {
    const currentNodes  = nodesRef.current;
    const apiNode       = currentNodes.find(n => n.type === 'api_enrich');
    if (!apiNode) return null;
    const staticColumns = currentNodes.filter(n => n.type === 'staticValues')
      .flatMap(node => (node.data?.fields || []).map(f => f.name));
    // B-FE-68: includi le colonne prodotte da enricher upstream (fromApi:true) nelle
    // staticColumns così il backend le skippa nel check missing_columns durante la validazione.
    const upstreamApiColumns = currentNodes
      .filter(n => n.type === 'api_enrich')
      .flatMap(n => (n.data?.columns || []).filter(c => c.fromApi && c.name).map(c => c.name));
    const allStaticColumns = [...new Set([...staticColumns, ...upstreamApiColumns])];
    // B-FE-41: leggi file_source_id dal nodo file_source nel canvas
    const fileSourceNode    = currentNodes.find(n => n.type === 'file_source');
    const fileSourceId      = fileSourceNode?.data?.file_source_id || null;
    // B-FE-52: leggi source_project_id dal nodo project_source per validate/estimate
    const projectSourceNode = currentNodes.find(n => n.type === 'project_source');
    const sourceProjectId   = projectSourceNode?.data?.source_project_id || null;
    return { apiId: apiNode.data?.api_id, paramMapping: apiNode.data?.param_mapping || {}, staticColumns: allStaticColumns, fileSourceId, sourceProjectId };
  }, []);

  const handleShowConfirmModal = useCallback(async () => {
    const currentNodes = nodesRef.current;
    if (currentNodes.length === 0) { toast.warning('Canvas vuoto. Aggiungi nodi prima di eseguire.'); return; }
    // Pre-flight access_denied: backend (RB.4.B/RB.7.B) marca i nodi che
    // riferiscono risorse non visibili al gruppo del progetto. Blocca il
    // run prima di chiamare il backend (che fallirebbe a runtime con
    // _assert_file_source_group_access — rete di sicurezza preservata).
    const denied = describeAccessDeniedNodes(currentNodes);
    if (denied) {
      toast.error('Esecuzione bloccata: rimuovi o sostituisci i nodi con risorse non accessibili.');
      console.warn('[run-blocked] access_denied nodes:\n' + denied.summary);
      return;
    }
    const enrichers = currentNodes.filter(n => n.type === 'api_enrich');
    const anyNeedsSource = enrichers.some(n => {
      if (n.data?.api_type === 'generator') return false;
      const staticParameters = n.data?.static_parameters || {};
      return (n.data?.parameters || []).some(p => p.required && p.mode !== 'fixed' && !(p.name in staticParameters));
    });
    if (anyNeedsSource && !currentNodes.find(n => n.type === 'project_source' || n.type === 'file_source' || n.type === 'staticValues')) { toast.warning('Aggiungi almeno una sorgente dati.'); return; }
    if (!currentNodes.find(n => n.type === 'api_enrich')) { toast.warning('Aggiungi almeno un nodo API Enrich.'); return; }
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
      execution.prepareExecute({ format });
      return;
    }
    setShowConfirmModal(true);
    const config = getEnrichmentConfig();
    if (config?.apiId) enrichmentValidation.validateAndEstimate(config.sourceProjectId || projectId, config.apiId, config.paramMapping, config.fileSourceId, config.staticColumns);
  }, [handleSaveQuiet, getEnrichmentConfig, projectId, enrichmentValidation, execution, exportFormat]);

  const handleConfirmExecution  = useCallback(() => {
    setShowConfirmModal(false);
    // Leggi il formato dal nodo Result al momento dell'esecuzione
    const resultNode = nodesRef.current.find(n => n.type === 'result');
    const format = resultNode?.data?.format || exportFormat || 'csv';
    execution.prepareExecute({ format });
  }, [execution, exportFormat]);
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

  // ===== SAVE =====

  const handleSaveCanvas = useCallback(async () => {
    try { setSaving(true); await handleSaveQuiet(); }
    catch (err) { console.error('[ArricchisciEditor] Errore salvataggio:', err); toast.error('Errore durante il salvataggio'); }
    finally { setSaving(false); }
  }, [handleSaveQuiet]);

  // ===== ADD NODE HANDLERS =====

  const getPos = () => getViewportCenter({ screenToFlowPosition });

  const handleSelectConnection = useCallback((connection, category) => {
    const newNodeId = `api_enrich_${Date.now()}`;
    setNodes(nds => nds.concat({ id: newNodeId,
      type: category === 'datasource' ? 'project_source' : 'api_enrich',
      position: { x: Math.random() * 300 + 200, y: Math.random() * 200 + 100 },
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

  // ===== EDGE & DRAG HANDLERS =====

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

  // ===== RENDER =====

  const handleBack     = () => navigate('/');
  const hasResultNode  = nodes.some(n => n.type === 'result');
  const hasCaricanNode = nodes.some(n => n.type === 'carica');
  const hasCaricanArcGISNode = nodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis');
  const hasImdfEnrich        = nodes.some(n => n.type === 'api_enrich' && n.data?.output_kind === 'imdf');
  const hasImdfPublisherNode = nodes.some(n => n.type === 'imdf_publisher');

  // Guard branches gestiti da EditorShell (FE-SPLIT-004).

  // beforeCanvas slot
  const accessDeniedBanner = hasAccessDeniedNodes(nodes) ? (
    <div
      role="alert"
      style={{
        background: '#fff4e5',
        border: '1px solid #d97706',
        borderRadius: 4,
        color: '#92400e',
        padding: '10px 14px',
        margin: '8px 16px 0',
        fontSize: '0.92em',
      }}
    >
      🔒 Il canvas contiene {getAccessDeniedNodes(nodes).length} nodo/i con risorse non accessibili al tuo gruppo. Rimuovili o sostituiscili prima di eseguire.
    </div>
  ) : null;

  // topbar slot
  const topbar = (
    <div className="arricchisci-toolbar">
        <div className="arricchisci-toolbar__left">
          <div className="arricchisci-toolbar__logo">
            <svg width="90" height="28" viewBox="0 0 350 90" xmlns="http://www.w3.org/2000/svg">
              <polygon points="16,58 52,40 88,58 52,76" fill="rgba(245,166,35,0.10)"  stroke="#F5A623" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,44 52,26 88,44 52,62" fill="rgba(155,109,255,0.10)" stroke="#9B6DFF" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,30 52,12 88,30 52,48" fill="rgba(52,193,122,0.12)"  stroke="#34C17A" strokeWidth="2"   strokeLinejoin="round"/>
              <line x1="16" y1="30" x2="16" y2="58" stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
              <text x="108" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" className="arricchisci-toolbar__logo-text">Plen</text>
              <text x="222" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill="#4B8BFF">ora</text>
            </svg>
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