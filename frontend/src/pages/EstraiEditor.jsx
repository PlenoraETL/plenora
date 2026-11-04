/**
 * EstraiEditor.jsx — Editor visuale per il modulo ESTRAI
 * Logica invariata — solo classi CSS aggiornate al design system Plenora
 */
import { ReactFlowProvider } from '@xyflow/react';
import React, { useState, useEffect, useCallback, useMemo, useRef, lazy, Suspense } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import '@xyflow/react/dist/style.css';
import EditorShell from '../components/editor/EditorShell';
import TableNode from '../components/nodes/TableNode';
import FeatureLayerNode from '../components/nodes/FeatureLayerNode';
import FeatureServicePanel from '../components/panels/FeatureServicePanel';
import QueryPreview from '../components/panels/QueryPreview';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';

// Modali lazy-loaded — caricati solo quando l'utente li apre
const JoinModal = lazy(() => import('../components/modals/JoinModal'));
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const CanvasVersionsModal = lazy(() => import('../components/modals/CanvasVersionsModal'));
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import ProjectInputNode from '../components/nodes/ProjectInputNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import { ConfirmProvider } from '../context/ConfirmContext';
import DataSourceNode from '../components/nodes/DataSourceNode';
import '../components/nodes/DataSourceNode.css';
import { calculateOutputColumnsRecursive } from '../utils/columnUtils';
import { createEdgeFromData, deserializeNodes } from '../utils/nodeFactories';
import { getLayerSchema } from '../api/features';
import useRuntimeInputDefinitions from '../hooks/useRuntimeInputDefinitions';
import { executionNeedsConfig } from '../utils/executionConfig';
import { usePreview, useExecution, useValidationHighlights, useEdgeHandlers, useCanvasCore, useCanvasSave, useUnsavedChangesGuard } from '../hooks';
import { useEditorData } from '../hooks/useEditorData';
import { beforePreview, saveForRun, saveNow } from '../utils/serverActions';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import useCanvasStore from '../store/useCanvasStore';
import { dataFlowEdges, hydrateParameterBindingEdges } from '../utils/runtimeBindingEdges';
import { useToast } from '../hooks/useToast';
import { toastApiError } from '../utils/toastApiError';
import { useTheme } from '../hooks/useTheme';
import '../styles/EstraiEditor.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/nodes/AnnotationNode.css';
import '../components/nodes/FeatureLayerNode.css';
import '../components/panels/FeatureServicePanel.css';
import { useAuth } from '../context/AuthContext';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';

const nodeTypes = {
  tableNode: TableNode, resultNode: ResultNode, staticValues: StaticValuesNode,
  project_input: ProjectInputNode,
  carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode,
  featureLayerNode: FeatureLayerNode, comment: AnnotationNode,
  datasourceNode: DataSourceNode
};

function EstraiEditor() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const hexCanvasRef = useRef(null);
  const [reactFlowInstance, setReactFlowInstance] = useState(null);
  const canvasLoadedForRef = useRef(null);
  const toast = useToast();

  useHexCanvas(hexCanvasRef);
  const {
    nodes, edges, onNodesChange, onEdgesChange,
    loadGraph, addNodes, addEdges, editEdges, insertNode, applyDerivedNodes,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    dataNodes, getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'estrai' });
  const {
    project, connections, loading, error: loadError,
    catalog, views, projectConnection, sourceType,
    reload: reloadData,
  } = useEditorData(projectId, { withCatalog: true });
  const TABLES_PAGE_SIZE = 15;

  const [searchQuery, setSearchQuery]             = useState('');
  const [viewSearchQuery, setViewSearchQuery]     = useState('');
  const [viewsCollapsed, setViewsCollapsed]       = useState(false);
  const [tablesCollapsed, setTablesCollapsed]     = useState(false);
  const [showVersions, setShowVersions]           = useState(false);
  const [visibleTableCount, setVisibleTableCount] = useState(TABLES_PAGE_SIZE);
  const [visibleViewCount,  setVisibleViewCount]  = useState(TABLES_PAGE_SIZE);
  const { theme, setTheme: toggleTheme } = useTheme();

  // Reset visible count when search changes
  useEffect(() => { setVisibleTableCount(TABLES_PAGE_SIZE); }, [searchQuery]);
  useEffect(() => { setVisibleViewCount(TABLES_PAGE_SIZE);  }, [viewSearchQuery]);
  // Input runtime chiesti all'esecuzione: stesso hook dei tre editor (R9.1).
  const runtimeInputDefinitions = useRuntimeInputDefinitions(nodes);
  // Letto al clic su Esegui (le azioni registrate non vedono i render nuovi).
  const needsRunConfigRef = useRef(false);
  needsRunConfigRef.current = executionNeedsConfig(runtimeInputDefinitions, user);
  const preview    = usePreview(projectId, beforePreview);
  const execution  = useExecution(projectId, { onBeforeExecute: saveForRun, tracksRunState: true });
  const { saving, handleSaveCanvas } = useCanvasSave(saveNow, {
    logPrefix: '[EstraiEditor]',
  });
  // Highlight backend-driven (Epic CanvasVal §12): i nodi con issue sono
  // evidenziati dallo store (setValidationHighlights, popolato da Valida/Esegui
  // via ResultNode). Coerente con Manipola/Arricchisci. La validazione live
  // in-memory è stata rimossa: la validazione è on-demand (pulsante → backend).
  useValidationHighlights(applyDerivedNodes);
  const edgeHandlers = useEdgeHandlers(nodes, editEdges, { resultNodeType: 'resultNode' });
  useEffect(() => {
    // H-9 (2026-05-26): early-return su loading PRIMA del check
    // canvas.nodes. Pre-fix: `if (!project?.canvas?.nodes || loading) return`
    // restituiva early su `nodes=[]` (canvas vuoto valido) SENZA settare
    // `canvasLoadedForRef.current = canvasKey`. Al successivo re-render
    // (es. selezione connection cambiata, project reload) il guard
    // non si attivava perche' la ref non era stata settata, e il load
    // ripartiva. Post-fix: usa `Array.isArray` per accettare anche
    // `nodes=[]` (canvas legittimamente vuoto, contract con
    // ProjectCanvas), settando canvasLoadedForRef anche in quel caso.
    if (loading) return;
    if (!project?.canvas || !Array.isArray(project.canvas.nodes)) return;
    const canvasKey = `${projectId}::${projectConnection?.id ?? 'none'}`;
    if (canvasLoadedForRef.current === canvasKey) return;
    canvasLoadedForRef.current = canvasKey;
    const loadedNodes = deserializeNodes(project.canvas.nodes, { connection: projectConnection });
    const loadedEdges = (project.canvas.edges || []).map(e => createEdgeFromData(e, loadedNodes));
    // loadGraph: grafo dal backend, stato pulito e cronologia azzerata.
    loadGraph(loadedNodes, [...loadedEdges, ...hydrateParameterBindingEdges(loadedNodes)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/deserializeNodes/createEdgeFromData omessi: stabili dal
    // ciclo di vita store. Includerli rifirerebbe il load a ogni re-render.
  }, [project, projectConnection, loading]);


  useEffect(() => {
    if (dataNodes.length === 0) return;
    const resultNode = dataNodes.find(n => n.type === 'resultNode');
    if (!resultNode) return;
    const outputColumns = calculateOutputColumnsRecursive(dataNodes, edges, resultNode.id);
    const signature = JSON.stringify(outputColumns);
    // Dato DERIVATO (non dirty). Confronto PER NODO: prima un early-return
    // sulle sole colonne del result lasciava vuote le availableColumns dei
    // Carica dopo il reload (audit R3.6).
    applyDerivedNodes(nds => {
      let changed = false;
      const next = nds.map(node => {
        if (node.type === 'resultNode' && JSON.stringify(node.data.columns || []) !== signature) {
          changed = true;
          return { ...node, data: { ...node.data, columns: outputColumns } };
        }
        if ((node.type === 'carica' || node.type === 'carica_arcgis' || node.type === 'caricaArcgis')
          && JSON.stringify(node.data.availableColumns || []) !== signature) {
          changed = true;
          return { ...node, data: { ...node.data, availableColumns: outputColumns } };
        }
        return node;
      });
      return changed ? next : nds;
    });
  }, [dataNodes, edges, applyDerivedNodes]);
  const handleUpdateFeatureWhere  = useCallback((nodeId, v)      => updateNodeData(nodeId, { where: v }), [updateNodeData]);
  const handleFeatureSchemaLoaded = useCallback((nodeId, schema) => {
    applyDerivedNodes(nds => nds.map(node => {
      if (node.id !== nodeId) return node;
      const columns = schema.columns
        ? schema.columns.map(c => ({ name: c.name, type: c.type || 'str', alias: null, visible: true }))
        : (schema.fields || []).map(f => ({ name: f.name, type: f.urby_type || f.field_type || f.type || 'str', alias: null, visible: true }));
      return { ...node, data: { ...node.data, columns, geometry_type: schema.geometry_type || node.data.geometry_type,
        geometry_type_urby: schema.geometry_type_urby || node.data.geometry_type_urby,
        feature_count: schema.feature_count ?? node.data.feature_count, relationships: schema.relationships || [] } };
    }));
  }, [applyDerivedNodes]);

  const handleAddRelatedLayer = useCallback(async (originNodeId, relation) => {
    const originNode = nodesRef.current.find(n => n.id === originNodeId);
    if (!originNode) return;
    if (nodesRef.current.some(n => n.type === 'featureLayerNode' && n.data.layer_url === relation.related_url)) {
      toast.warning('Questo layer è già presente nel canvas'); return;
    }
    let schema;
    try { schema = await getLayerSchema(originNode.data.connection_id, relation.related_url); }
    catch (err) { toastApiError(toast, err, 'Errore caricamento schema'); return; }
    const newNodeId = `fl_${Date.now()}`;
    const columns   = (schema.fields || []).map(f => ({ name: f.name, type: f.field_type || f.type || 'str', alias: null, visible: true }));
    const newNode   = {
      id: newNodeId, type: 'featureLayerNode',
      position: { x: originNode.position.x, y: originNode.position.y + 350 },
      data: { type: 'feature_source', layer_name: schema.name || relation.name,
        layer_url: relation.related_url, connection_id: originNode.data.connection_id,
        connection_name: originNode.data.connection_name, service_name: originNode.data.service_name,
        geometry_type: schema.geometry_type_urby || schema.geometry_type || '',
        feature_count: schema.feature_count, where: '', columns, relationships: schema.relationships || [] }
    };
    const isOrigin = relation.role === 'origin';
    const joinType = relation.cardinality === '1:1' ? 'inner' : 'left';
    const newEdge  = {
      id: `edge_rel_${Date.now()}`,
      source: isOrigin ? originNodeId : newNodeId, target: isOrigin ? newNodeId : originNodeId,
      label: `🔗 ${joinType} join (${relation.cardinality})`, animated: true,
      style: { stroke: '#4B8BFF', strokeWidth: 2 },
      data: { join_type: joinType,
        source_column: isOrigin ? relation.key_field : (relation.key_field_in_relationship_table || relation.key_field),
        target_column: isOrigin ? (relation.key_field_in_relationship_table || relation.key_field) : relation.key_field,
        join_conditions: null, on_filters: null, is_spatial: false,
        spatial_predicate: null, source_geom_column: null, target_geom_column: null, spatial_distance: null }
    };
    addNodes(newNode);
    addEdges(newEdge);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // useCallback intenzionalmente vincolato a setNodes/setEdges + handler
    // schema; le altre identità non cambiano la semantica dell'add layer.
  }, [addNodes, addEdges, handleUpdateFeatureWhere, handleFeatureSchemaLoaded]);

  // Registra azioni editor nello store
  useEffect(() => {
    useCanvasStore.getState().setEditorActions({
      onPreview:          () => preview.handlePreview(),
      onExecute:          (nodeId, opts) => {
        // Risorse non accessibili: bloccate prima del salvataggio nel
        // percorso unico di ResultNode (Fase 4, M7).
        return execution.prepareExecute(opts, { needsConfig: needsRunConfigRef.current });
      },
      onAddRelatedLayer:  handleAddRelatedLayer,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // toast/nodesRef letti via closure stabile;
    // re-bind solo quando cambiano gli handler hook delle azioni canvas.
  }, [preview.handlePreview, execution.prepareExecute, handleAddRelatedLayer]);

  useEffect(() => { useCanvasStore.getState().setConnections(connections); }, [connections]);
  // Pre-fix (P2 frontend-pages): le 3 handleAdd* callback avevano
  // `nodes` nelle deps — ricreate ad ogni cambio nodi (drag, sel) →
  // i bottoni toolbar che le ricevevano come prop si re-rendevano.
  // ManipolaEditor/ArricchisciEditor gia' usavano `nodesRef.current`
  // per il check di esistenza; allineiamo EstraiEditor.
  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'resultNode')) { toast.warning('Puoi avere solo un nodo risultato per progetto'); return; }
    addNodes({
      id: `result_${Date.now()}`, type: 'resultNode', position: getViewportCenter(reactFlowInstance),
      data: { fileName: 'output', format: 'csv', distinct: false, limit: null,
        group_by_enabled: false, group_by_columns: [], aggregations: [],
        order_by_enabled: false, order_by_columns: [],
        having_enabled: false, having_conditions: [] }
    });
  }, [addNodes, getViewportCenter, reactFlowInstance, toast]);

  const handleAddCaricanNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'resultNode')) { toast.warning('Aggiungi prima un nodo Output'); return; }
    if (currentNodes.some(n => n.type === 'carica'))      { toast.warning('Puoi avere solo un nodo CARICA DB per progetto'); return; }
    const resultNode = currentNodes.find(n => n.type === 'resultNode');
    const newNode = {
      id: `carica_${Date.now()}`, type: 'carica',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y },
      data: { connection_id: '', schema_name: '', table_name: '', write_mode: 'create',
        column_mappings: [], match_columns: [], batch_size: 1000,
        transaction_mode: 'all_or_nothing', create_spatial_index: true, conflict_policy: 'abort',
        availableColumns: resultNode.data.columns || [] }
    };
    addNodes(newNode);
    addEdges({ id: `edge_result_carica_${Date.now()}`, source: resultNode.id, target: newNode.id,
      label: 'Carica DB', animated: true, style: { stroke: '#7c3aed', strokeWidth: 2 }, data: { join_type: 'carica' } });
  }, [addNodes, addEdges, toast]);

  const handleAddCaricanArcGISNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'resultNode')) { toast.warning('Aggiungi prima un nodo Output'); return; }
    if (currentNodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis')) { toast.warning('Puoi avere solo un nodo CARICA ArcGIS per progetto'); return; }
    const resultNode = currentNodes.find(n => n.type === 'resultNode');
    const newNode = {
      id: `carica_arcgis_${Date.now()}`, type: 'carica_arcgis',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y + 200 },
      data: { arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '',
        arcgis_operation: 'add', arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
        column_mappings: [], availableColumns: resultNode.data.columns || [] }
    };
    addNodes(newNode);
    addEdges({ id: `edge_result_carica_arcgis_${Date.now()}`, source: resultNode.id, target: newNode.id,
      label: 'Carica ArcGIS', animated: true, style: { stroke: '#2DD4BF', strokeWidth: 2 }, data: { join_type: 'carica_arcgis' } });
  }, [addNodes, addEdges, toast]);

  // ESTRAI accetta solo sorgenti SQL/ArcGIS; Data Source resta in MANIPOLA.

  // Static values non sono una sorgente SQL ESTRAI.

  const handleAddProjectInputNode = useCallback(() => {
    const ordinal = nodesRef.current.filter(node => node.type === 'project_input').length + 1;
    addNodes({
      id: `project_input_${Date.now()}`,
      type: 'project_input',
      position: getViewportCenter(reactFlowInstance),
      data: { input_key: `input_${ordinal}`, label: `Input ${ordinal}`, input_kind: 'text', required: true, expected_columns: [], validation: {}, bindings: [], order: ordinal - 1 },
    });
  }, [addNodes, nodesRef, getViewportCenter, reactFlowInstance]);

  const handleAddCommentNode = useCallback(() => {
    const pos = getViewportCenter(reactFlowInstance);
    addNodes({ id: `comment_${Date.now()}`, type: 'comment', position: pos, zIndex: -1,
      style: { width: 250, height: 150 },
      data: { level: '', label: '', title: '', color: '#fbbf24' } });
  }, [addNodes, getViewportCenter, reactFlowInstance]);
  // Costruisce un nodo tabella/vista (condiviso da drop e click-to-add).
  const buildTableNode = useCallback((tableName, isView, position) => {
    const tableColumns = (isView ? views[tableName] : catalog[tableName]) || [];
    return {
      id: `node_${Date.now()}`, type: 'tableNode', position,
      data: { label: tableName, table_name: tableName, alias: null, type: 'source',
        columns: tableColumns.map(col => ({ name: typeof col === 'object' ? col.name : col,
          type: typeof col === 'object' ? col.type || 'str' : 'str', alias: null, visible: true })),
        filters: [], calculated_columns: [], db_type: projectConnection?.type || 'postgres' },
    };
  }, [catalog, views, projectConnection]);

  // ── Palette "Cosa vuoi fare?" (Fase 4, M2) ────────────────────────────
  // Dopo una tabella: un'altra tabella/vista del catalogo, collegata con un
  // join (si apre il JoinModal). Dall'Output: Carica DB / ArcGIS.
  // Rami (Fase 4, M6): in Estrai "Collega" e' un join tra tabelle, con lo
  // stesso gesto del collegamento manuale (la tabella scollegata e' la
  // sorgente del join, come trascinandola).
  const branchActions = useMemo(() => ({
    connectBranch: (endId, targetId) => edgeHandlers.onConnect({ source: endId, sourceHandle: null, target: targetId, targetHandle: null }),
    connectModes: [['join', 'Unisci con (Join)']],
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [edgeHandlers.onConnect]);

  const palette = useMemo(() => ({
    groups: [
      { label: 'Tabelle', items: Object.keys(catalog || {}).sort().map(t => ({
        id: `table:${t}`, kind: 'table', name: t, label: t, icon: '🗂️',
        desc: `${(catalog[t] || []).length} col`,
      })), limit: 40 },
      { label: 'Viste', items: Object.keys(views || {}).sort().map(v => ({
        id: `view:${v}`, kind: 'view', name: v, label: v, icon: '👁',
        desc: `${(views[v] || []).length} col`,
      })), limit: 40 },
      { label: 'Uscite', items: [
        { id: 'carica', kind: 'carica', label: 'Carica su database', icon: '💾', desc: "dopo l'Output" },
        { id: 'carica_arcgis', kind: 'carica_arcgis', label: 'Carica su ArcGIS', icon: '🌐', desc: "dopo l'Output" },
      ] },
    ],
    onPick: (item, afterId) => {
      if (item.kind === 'carica') { handleAddCaricanNode(); return; }
      if (item.kind === 'carica_arcgis') { handleAddCaricanArcGISNode(); return; }
      const after = nodesRef.current.find(n => n.id === afterId);
      const position = after ? { x: after.position.x - 290, y: after.position.y + 120 } : getViewportCenter(reactFlowInstance);
      const newNode = buildTableNode(item.name, item.kind === 'view', position);
      // Posizione libera vicino al punto scelto, nodo selezionato (store.insertNode).
      insertNode({ node: newNode });
      // Tabella dopo tabella = join: la nuova e' la sorgente, quella di
      // partenza il target (stesso verso del trascinamento manuale).
      if (after && (after.type === 'tableNode' || after.type === 'featureLayerNode')) {
        edgeHandlers.onConnect({ source: newNode.id, sourceHandle: null, target: after.id, targetHandle: null });
      }
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [catalog, views, buildTableNode, insertNode, handleAddCaricanNode, handleAddCaricanArcGISNode]);

  // Click su una tabella/vista del catalogo → la aggiunge al CENTRO del canvas
  // (il drag resta per il posizionamento preciso).
  const addTableAtCenter = useCallback((tableName, isView = false) => {
    addNodes(buildTableNode(tableName, isView, getViewportCenter(reactFlowInstance)));
  }, [addNodes, buildTableNode, getViewportCenter, reactFlowInstance]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    if (!reactFlowInstance) return;
    const position = reactFlowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });

    const featureLayerRaw = event.dataTransfer.getData('featureLayerData');
    if (featureLayerRaw) {
      try {
        const ld = JSON.parse(featureLayerRaw);
        addNodes({ id: `fl_${Date.now()}`, type: 'featureLayerNode', position,
          data: { type: 'feature_source', layer_name: ld.layerName, layer_url: ld.layerUrl,
            connection_id: ld.connectionId, connection_name: ld.connectionName, service_name: ld.serviceName,
            geometry_type: ld.geometryType, feature_count: ld.featureCount, where: '', columns: [] } });
      } catch (err) { console.error('Errore parsing feature layer data:', err); }
      return;
    }

    // Data Source come sorgente e' supportato in MANIPOLA, non in ESTRAI.
    const datasourceRaw = event.dataTransfer.getData('datasourceData');
    if (datasourceRaw) {
      try { JSON.parse(datasourceRaw); } catch { /* noop */ }
      return;
    }

    const tableName = event.dataTransfer.getData('tableName');
    if (!tableName) return;
    const isView = event.dataTransfer.getData('isView') === 'true';
    addNodes(buildTableNode(tableName, isView, position));
  }, [reactFlowInstance, addNodes, buildTableNode]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);
  const { confirmNavigation } = useUnsavedChangesGuard();
  const handleBack              = () => { if (confirmNavigation()) navigate('/'); };
  const filteredTables          = Object.keys(catalog).filter(t => t.toLowerCase().includes(searchQuery.toLowerCase()));
  const sourceNodes             = nodes.filter(n => n.type === 'tableNode' || n.type === 'source');
  const tablesInCanvas          = new Set(sourceNodes.map(n => n.data.table_name).filter(Boolean));
  const availableTables         = filteredTables.filter(t => !tablesInCanvas.has(t));
  const viewNames               = Object.keys(views);
  const filteredViews           = viewNames.filter(v => v.toLowerCase().includes(viewSearchQuery.toLowerCase()));
  const viewsInCanvas           = new Set(sourceNodes.map(n => n.data.table_name).filter(Boolean));
  const availableViews          = filteredViews.filter(v => !viewsInCanvas.has(v));
  const tableCount              = Object.keys(catalog).length;
  const hasResultNode           = nodes.some(n => n.type === 'resultNode');
  const hasCaricanNode          = nodes.some(n => n.type === 'carica');
  const hasCaricanArcGISNode    = nodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis');
  const featureNodes            = nodes.filter(n => n.type === 'featureLayerNode');
  const featureLayersInCanvas   = new Set(featureNodes.map(n => n.data.layer_url).filter(Boolean));

  // Guard branches (isValidProjectId redirect, loading, loadError, !project)
  // gestiti da EditorShell via le props guards. Il page conserva
  // solo l'orchestration degli hook e il JSX dei singoli slot.

  // beforeCanvas slot: banner accessibilita' nodi cross-tenant.
  const accessDeniedBanner = <AccessDeniedBanner nodes={nodes} />;

  // topbar slot
  // Barra unica (Fase 4, M7): connessione accanto al titolo, azioni nel
  // menu "⋯", tabelle dalla palette ("＋ Passo"), un solo "▶ Esegui".
  const toolbar = {
    mode: 'estrai',
    title: project?.metadata?.name,
    onBack: handleBack,
    onSave: handleSaveCanvas,
    saving,
    executing: execution.isExecuting,
    theme,
    setTheme: toggleTheme,
    statusExtra: <span className="etb__stats">{nodes.length} nodi · {edges.length} collegamenti</span>,
    leftExtra: projectConnection ? (
      <span className="etb__conn" title={projectConnection.name}>
        <i aria-hidden="true" />{projectConnection.name}
        <small>{sourceType === 'featureService' ? 'ArcGIS' : (projectConnection.type || 'DB').toUpperCase()}</small>
      </span>
    ) : null,
    menuItems: [
      { key: 'connections', icon: '🔌', label: 'Connessioni', onClick: () => { if (confirmNavigation()) navigate('/connections'); } },
      ...(isReadOnly ? [] : [
        { key: 'div0', divider: true },
        { key: 'input', icon: '⌨', label: 'Aggiungi input runtime', onClick: handleAddProjectInputNode, title: 'Un parametro chiesto quando il progetto viene eseguito' },
        { key: 'note', icon: '💬', label: 'Nota', onClick: handleAddCommentNode },
        { key: 'div1', divider: true },
        { key: 'output', icon: '📁', label: 'Output', onClick: handleAddResultNode, disabled: hasResultNode },
        { key: 'carica', icon: '⬆', label: 'Carica su database', onClick: handleAddCaricanNode, disabled: !hasResultNode || hasCaricanNode },
        { key: 'carica-arcgis', icon: '🌐', label: 'Carica su ArcGIS', onClick: handleAddCaricanArcGISNode, disabled: !hasResultNode || hasCaricanArcGISNode },
        { key: 'div2', divider: true },
        { key: 'versions', icon: '🕘', label: 'Versioni', onClick: () => setShowVersions(true) },
      ]),
    ],
  };

  // sidebar slot (nascosta per viewer: niente drag&drop di tabelle)
  const sidebar = !isReadOnly ? (
        <div className="estrai-sidebar">

          {/* Search */}
          <div className="estrai-sidebar__search-wrap">
            <div className="estrai-sidebar__search">
              <span className="estrai-sidebar__search-icon">⌕</span>
              <input
                placeholder="Cerca tabelle e viste…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="estrai-sidebar__body">
            {sourceType === 'database' && (
              <>
                {/* Tables section */}
                <div
                  className="estrai-sidebar__section-header"
                  role="button" tabIndex={0}
                  onClick={() => setTablesCollapsed(!tablesCollapsed)}
                  onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setTablesCollapsed(c => !c)}
                >
                  <span className="estrai-sidebar__section-toggle">
                    {tablesCollapsed ? '▶' : '▼'}
                  </span>
                  <span className="estrai-sidebar__title">🗄 Tabelle</span>
                  <span className="estrai-sidebar__count-badge">{tableCount}</span>
                </div>

                {!tablesCollapsed && (
                  <div className="estrai-sidebar__items">
                    {availableTables.length === 0
                      ? <div className="estrai-sidebar__empty">
                          {filteredTables.length === 0 ? 'Nessuna tabella trovata' : 'Tutte nel canvas'}
                        </div>
                      : <>
                          {availableTables.slice(0, visibleTableCount).map(t => (
                            <div
                              key={t}
                              className="estrai-sidebar__table-item"
                              title="Click per aggiungere al centro · trascina per posizionare"
                              draggable
                              onClick={() => addTableAtCenter(t, false)}
                              onDragStart={e => {
                                e.dataTransfer.setData('tableName', t);
                                e.dataTransfer.effectAllowed = 'move';
                              }}
                            >
                              <span className="estrai-sidebar__table-name">📊 {t}</span>
                              <span className="estrai-sidebar__table-cols">{catalog[t]?.length || 0} col</span>
                            </div>
                          ))}
                          {availableTables.length > visibleTableCount && (
                            <button
                              type="button"
                              className="estrai-sidebar__load-more"
                              onClick={() => setVisibleTableCount(c => c + TABLES_PAGE_SIZE)}
                            >
                              ▼ Mostra altri {Math.min(TABLES_PAGE_SIZE, availableTables.length - visibleTableCount)} di {availableTables.length - visibleTableCount} rimasti
                            </button>
                          )}
                        </>
                    }
                  </div>
                )}

                {/* Views section */}
                {viewNames.length > 0 && (
                  <>
                    <div
                      className="estrai-sidebar__section-header estrai-sidebar__section-header--views"
                      role="button" tabIndex={0}
                      onClick={() => setViewsCollapsed(!viewsCollapsed)}
                      onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setViewsCollapsed(c => !c)}
                    >
                      <span className="estrai-sidebar__section-toggle">
                        {viewsCollapsed ? '▶' : '▼'}
                      </span>
                      <span className="estrai-sidebar__title">👁 Viste</span>
                      <span className="estrai-sidebar__count-badge">{viewNames.length}</span>
                    </div>

                    {!viewsCollapsed && (
                      <>
                        <div className="estrai-sidebar__search-wrap" style={{ paddingTop: 0 }}>
                          <div className="estrai-sidebar__search">
                            <span className="estrai-sidebar__search-icon">⌕</span>
                            <input
                              placeholder="Cerca viste…"
                              value={viewSearchQuery}
                              onChange={e => setViewSearchQuery(e.target.value)}
                            />
                          </div>
                        </div>
                        <div className="estrai-sidebar__items">
                          {availableViews.length === 0
                            ? <div className="estrai-sidebar__empty">Nessuna vista trovata</div>
                            : <>
                                {availableViews.slice(0, visibleViewCount).map(v => (
                                  <div
                                    key={`view_${v}`}
                                    className="estrai-sidebar__table-item estrai-sidebar__table-item--view"
                                    title="Click per aggiungere al centro · trascina per posizionare"
                                    draggable
                                    onClick={() => addTableAtCenter(v, true)}
                                    onDragStart={e => {
                                      e.dataTransfer.setData('tableName', v);
                                      e.dataTransfer.setData('isView', 'true');
                                      e.dataTransfer.effectAllowed = 'move';
                                    }}
                                  >
                                    <span className="estrai-sidebar__table-name">👁 {v}</span>
                                    <span className="estrai-sidebar__table-cols">{views[v]?.length || 0} col</span>
                                  </div>
                                ))}
                                {availableViews.length > visibleViewCount && (
                                  <button
                                    type="button"
                                    className="estrai-sidebar__load-more"
                                    onClick={() => setVisibleViewCount(c => c + TABLES_PAGE_SIZE)}
                                  >
                                    ▼ Mostra altri {Math.min(TABLES_PAGE_SIZE, availableViews.length - visibleViewCount)} di {availableViews.length - visibleViewCount} rimaste
                                  </button>
                                )}
                              </>
                          }
                        </div>
                      </>
                    )}
                  </>
                )}
              </>
            )}

            {sourceType === 'featureService' && (
              <FeatureServicePanel
                layersInCanvas={featureLayersInCanvas}
                lockedConnection={projectConnection}
              />
            )}
          </div>

          {/* User footer */}
          <div className="estrai-sidebar__user">
            <div>
              <div className="estrai-sidebar__user-name">{user?.username}</div>
              <div className="estrai-sidebar__user-email">{user?.email}</div>
            </div>
            <button
              type="button"
              className="estrai-sidebar__logout"
              onClick={() => { if (confirmNavigation()) { logout(); navigate('/login'); } }}
              title="Logout"
            >
              ⏻
            </button>
          </div>
        </div>
  ) : null;

  // afterCanvas slot: QueryPreview overlay sul canvas.
  const afterCanvas = (
    <QueryPreview nodes={dataNodes} edges={edges} />
  );

  // modals slot: lazy-loaded modal stack.
  const modals = (
    <ModalErrorBoundary>
      <Suspense fallback={null}>
        {showVersions && (
          <CanvasVersionsModal
            isOpen onClose={() => setShowVersions(false)} projectId={projectId}
          />
        )}
        {edgeHandlers.showJoinModal && (
          <JoinModal
            edge={edgeHandlers.editingEdge || edgeHandlers.pendingEdge}
            sourceNode={edgeHandlers.sourceNode} targetNode={edgeHandlers.targetNode}
            onSave={edgeHandlers.handleSaveJoin} onCancel={edgeHandlers.handleCancelJoin}
            onDelete={edgeHandlers.handleDeleteEdge} isEditing={edgeHandlers.isEditing}
          />
        )}
        <ExecutionModal
          isOpen={execution.showModal} onClose={execution.closeModal}
          isExecuting={execution.isExecuting} result={execution.result}
          onDownload={execution.downloadTaskResult}
          title="▶ Esecuzione Query" projectType="estrai"
          caricaTasks={execution.caricaTasks} caricaPolling={execution.caricaPolling}
          awaitingLaunch={execution.awaitingLaunch} onLaunch={execution.launch} user={user}
          inputDefinitions={runtimeInputDefinitions}
        />
        <PreviewModal
        isOpen={preview.showModal} onClose={preview.closeModal}
        onRefresh={limit => preview.handlePreview(limit)}
        data={(() => {
          // Arricchisce le colonne della preview con source_table dal resultNode,
          // così PreviewModal può raggruppare per vera sorgente invece di indovinare dal prefisso.
          if (!preview.data?.columns) return preview.data;
          const resultNode = nodes.find(n => n.type === 'resultNode');
          const canvasCols = resultNode?.data?.columns || [];
          // Mappa name → source_table dal canvas (calculateOutputColumnsRecursive)
          const sourceMap = {};
          canvasCols.forEach(col => {
            if (col.source_table) sourceMap[col.name] = col.source_table;
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
        title="Anteprima Query SQL" projectType="estrai"
      />
      </Suspense>
    </ModalErrorBoundary>
  );

  return (
    <ConfirmProvider>
      <EditorShell
        projectId={projectId}
        prefix="estrai"
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
          isReadOnly,
          readOnlyProps,
          // Zoom iniziale meno spinto: fitView centra il contenuto ma senza
          // superare 0.8× (l'utente può comunque zoomare fino a maxZoom a mano).
          fitView: true,
          fitViewOptions: { padding: 0.25, maxZoom: 0.8 },
          minZoom: 0.3,
          maxZoom: 2,
          palette,
          branchActions,
        }}
        canvasHandlers={{
          onNodesChange,
          onEdgesChange,
          onConnect: edgeHandlers.onConnect,
          onEdgeClick: edgeHandlers.onEdgeClick,
          onInit: setReactFlowInstance,
          onDrop,
          onDragOver,
        }}
        slots={{
          toolbar,
          sidebar,
          beforeCanvas: accessDeniedBanner,
          afterCanvas,
          modals,
        }}
      />
    </ConfirmProvider>
  );
}

// Il pannello laterale della nuova interfaccia (NodeInspector) mostra i form
// dei nodi fuori dal canvas: serve il provider React Flow sopra l'editor,
// come in Manipola e Arricchisci.
function EstraiEditorWithProvider() {
  return (
    <ReactFlowProvider>
      <EstraiEditor />
    </ReactFlowProvider>
  );
}

export default EstraiEditorWithProvider;
