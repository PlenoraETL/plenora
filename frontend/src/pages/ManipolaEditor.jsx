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
 *              useColumnPropagation, useExecution, usePreview,
 *              useCaricarica, useToast
 *
 * Route: /editor/manipola/:id
 */
import React, { useState, useCallback, useEffect, useRef, lazy, Suspense } from 'react';
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from 'reactflow';
import 'reactflow/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { ConfirmProvider } from '../context/ConfirmContext';
import { getDownloadUrl, createTask } from '../api';
import { getFiles } from '../api/files';
import ProjectSourceNode from '../components/nodes/ProjectSourceNode';
import FileSourceNode from '../components/nodes/FileSourceNode';
import TransformNode from '../components/transforms/TransformNode';
import MultiJoinNode from '../components/transforms/MultiJoinNode';
import ResultNode from '../components/nodes/ResultNode';
import CaricanNode from '../components/nodes/CaricanNode';
import CaricanArcGISNode from '../components/nodes/CaricanArcGISNode';
import '../components/nodes/CaricanArcGISNode.css';
import EditorSidebar from '../components/panels/EditorSidebar';

// Modali lazy-loaded
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import DataSourceNode from '../components/nodes/DataSourceNode';
import { useSchemaInference, usePreview, useExecution, useCanvasCore } from '../hooks';
import { useEditorData } from '../hooks/useEditorData';
import useCanvasStore from '../store/useCanvasStore';
import ValidationStatusBadge from '../components/editor/ValidationStatusBadge';
import { useToast } from '../hooks/useToast';
import { useTheme } from '../hooks/useTheme';
import useColumnPropagation from '../hooks/useColumnPropagation';
import { deserializeNodes, createEdgeFromData } from '../utils/nodeFactories';
import { describeAccessDeniedNodes } from '../utils/canvasAccessControl';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import '../styles/ManipolaEditor.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/nodes/AnnotationNode.css';
import '../components/nodes/DataSourceNode.css';
import { useAuth } from '../context/AuthContext';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';

// Costante stabile — evita array literal nelle deps di useEditorData
const MANIPOLA_SOURCE_TYPES = ['estrai', 'manipola', 'arricchisci'];

const nodeTypes = {
  project_source: ProjectSourceNode, file_source: FileSourceNode,
  transform: TransformNode, multi_join: MultiJoinNode,
  result: ResultNode, staticValues: StaticValuesNode,
  carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode, comment: AnnotationNode,
  datasource_node: DataSourceNode
};

const defaultEdgeOptions = {
  type: 'smoothstep', animated: false,
  style: { stroke: '#9C27B0', strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#9C27B0' }
};

const ManipolaEditorContent = () => {
  const { id: projectId } = useParams();

  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { screenToFlowPosition } = useReactFlow();
  const toast = useToast();

  // ===== THEME =====
  const { theme, setTheme } = useTheme();
  const hexCanvasRef = useRef(null);
  useHexCanvas(hexCanvasRef);

  // ===== CANVAS CORE =====
  const {
    nodes, edges, setNodes, setEdges, onNodesChange, onEdgesChange,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    handleSaveQuiet, getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'manipola' });

  // ===== COLUMN PROPAGATION (specifico Manipola) =====
  const { propagateColumns } = useColumnPropagation(setNodes);

  // ===== DATA LOADING (centralizzato) =====
  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: MANIPOLA_SOURCE_TYPES,
  });

  // ===== STATE SPECIFICO MANIPOLA =====
  const [saving, setSaving] = useState(false);

  // ===== SCHEMA INFERENCE (specifico Manipola) =====
  const { schemas, loading: schemasLoading, refreshSchemas } = useSchemaInference(projectId);

  // ===== HOOKS COMUNI =====
  const preview   = usePreview(projectId, handleSaveQuiet);
  // I 3 throws fail-fast (canvas vuoto / Output mancante / sorgente mancante)
  // erano duplicati delle regole CANVAS_EMPTY / RESULT_NODE_MISSING /
  // RESULT_NO_SOURCE in `validateManipolaCanvas`. Il flow `ResultNode.handleExecute
  // WithValidation` esegue `runValidation()` PRIMA di chiamare `onExecute`:
  // se la validazione fallisce apre il modal e ritorna; quindi questo
  // `onBeforeExecute` viene raggiunto solo a validation-passed e i throws
  // erano dead code (FE-PHASE2-VALIDATION-002).
  const execution = useExecution(projectId, {
    // Arrow wrapper per evitare TDZ: `handleSaveCanvas` e' definito piu' in
    // basso e questa config viene letta sincrono al call site di useExecution.
    onBeforeExecute: () => handleSaveCanvas(),
  });

  // CARICA asincrono via Celery
  const executeCaricanTask = useCallback(async (projId, options) => {
    const res = await createTask({
      task_type: 'carica', project_id: projId,
      canvas: { nodes: nodesRef.current, edges: edgesRef.current },
      carica_node_data: options.carica_node_data
    });
    const result = res;
    if (result.task_id) return { async: true, task_id: result.task_id, message: result.message };
    throw new Error(result.detail || result.error || 'Errore creazione task CARICA');
  }, []);

  const caricaExecution = useExecution(projectId, {
    onBeforeExecute: handleSaveQuiet,
    executeFunction: executeCaricanTask,
    pollInterval: 1500
  });

  // ===== EFFECTS =====

  // ===== EFFECT: carica nodi nel canvas quando il progetto e pronto =====
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

    setNodes(loadedNodes);
    setEdges(rawEdges);
    // Reset dirty: setNodes/setEdges hanno marcato dirty perché inseriscono
    // nuovi ID, ma è un load programmatico, non una modifica utente.
    useCanvasStore.getState().markClean();
    setTimeout(() => { propagateColumns(loadedNodes, rawEdges); }, 300);
    setTimeout(() => { refreshSchemas(); }, 100);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // setNodes/setEdges/propagateColumns/refreshSchemas/deserializeNodes
    // omessi: load programmatico una sola volta per project, non ad ogni
    // re-bind degli handler.
  }, [project, loading]);

  // Propaga schemi ai nodi transform
  useEffect(() => {
    if (!schemas || Object.keys(schemas).length === 0) return;
    setNodes(nds => nds.map(node => {
      const nodeSchema = schemas[node.id];
      if (!nodeSchema || nodeSchema.length === 0) return node;
      const columns = nodeSchema.map(col => ({ name: col.name, type: col.type, visible: true, source_node: col.source_node, is_calculated: col.is_calculated }));
      if (JSON.stringify(node.data?.columns || []) === JSON.stringify(columns)) return node;
      return { ...node, data: { ...node.data, columns } };
    }));
  }, [schemas, setNodes]);

  // Aggiorna availableColumns in ResultNode e CaricanNode
  useEffect(() => {
    // NOTA: 'nodes' rimosso dalle dipendenze intenzionalmente per evitare loop React.
    // nodes e edges vengono letti dentro la callback di setNodes (snapshot aggiornato)
    // e da edgesRef (ref stabile). schemas è la sola vera dipendenza esterna.
    setNodes(nds => {
      const currentEdges = edgesRef.current;
      const resultNode = nds.find(n => n.type === 'result');
      if (!resultNode) return nds;
      const incomingEdge = currentEdges.find(e => e.target === resultNode.id);

      if (!incomingEdge) {
        if ((resultNode.data?.availableColumns || []).length === 0) return nds;
        return nds.map(node => {
          if (node.type === 'result' || node.type === 'carica' || node.type === 'carica_arcgis' || node.type === 'caricaArcgis') return { ...node, data: { ...node.data, availableColumns: [] } };
          return node;
        });
      }

      const sourceNodeId     = incomingEdge.source;
      const availableColumns = [];
      const nodeSchema       = schemas[sourceNodeId] || [];
      if (nodeSchema.length > 0) {
        nodeSchema.forEach(col => availableColumns.push({ name: col.name, type: col.type || 'str', source: sourceNodeId }));
      } else {
        const sourceNode = nds.find(n => n.id === sourceNodeId);
        (sourceNode?.data?.columns || []).forEach(col => availableColumns.push({ name: col.name || col, type: col.type || 'str', source: sourceNodeId }));
      }

      if (JSON.stringify(availableColumns) === JSON.stringify(resultNode.data?.availableColumns || [])) return nds;
      return nds.map(node => {
        if (node.type === 'result' || node.type === 'carica' || node.type === 'carica_arcgis' || node.type === 'caricaArcgis') return { ...node, data: { ...node.data, availableColumns } };
        return node;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // 'nodes' volutamente assente: viene letto via callback di setNodes
    // (snapshot React aggiornato) per evitare il loop nodes->effect->setNodes.
  }, [edges, schemas, setNodes, edgesRef]);

  // ===== NODE HANDLERS =====

  const handleUpdateProjectSource = useCallback((nodeId, projectSourceId) => {
    updateNodeData(nodeId, { source_project_id: projectSourceId });
    setTimeout(() => { handleSaveQuiet().then(() => refreshSchemas()); }, 100);
  }, [updateNodeData, refreshSchemas, handleSaveQuiet]);

  const handleUpdateProjectSourceColumns = useCallback((nodeId, columns) => {
    updateNodeData(nodeId, { columns });
    setTimeout(() => { propagateColumns(nodesRef.current, edgesRef.current); }, 50);
  }, [updateNodeData, propagateColumns]);

  const handleUpdateFileSourceColumns = useCallback((nodeId, columns) => {
    updateNodeData(nodeId, { columns });
    setTimeout(() => {
      const updatedNodes = nodesRef.current.map(n =>
        n.id === nodeId ? { ...n, data: { ...n.data, columns } } : n
      );
      propagateColumns(updatedNodes, edgesRef.current);
      handleSaveQuiet().then(() => refreshSchemas());
    }, 100);
  }, [updateNodeData, propagateColumns, refreshSchemas, handleSaveQuiet]);

  const handleUpdateTransform = useCallback((nodeId, newData) => {
    updateNodeData(nodeId, newData);
    // Costruiamo i nodi aggiornati inline invece di aspettare nodesRef.current
    // (che viene aggiornato dall'useEffect solo al prossimo render)
    setTimeout(() => {
      const updatedNodes = nodesRef.current.map(n =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...newData } } : n
      );
      propagateColumns(updatedNodes, edgesRef.current);
      handleSaveQuiet().then(() => refreshSchemas());
    }, 50);
  }, [updateNodeData, propagateColumns, refreshSchemas, handleSaveQuiet]);

  // handleUpdateProjectSource/Columns/FileSource/Transform rimangono perché
  // fanno side-effect (refreshSchemas, propagateColumns) registrati in setEditorActions

  // Registra azioni editor nello store — DEVE stare dopo tutti gli hook e useCallback
  useEffect(() => {
    useCanvasStore.getState().setEditorActions({
      onPreview:                    () => preview.handlePreview(),
      onExecute:                    (nodeId, opts) => {
        // Pre-flight access_denied (RB.4.B/RB.7.B): blocca run se il
        // canvas contiene nodi con risorse non visibili al gruppo.
        const denied = describeAccessDeniedNodes(nodesRef.current);
        if (denied) {
          toast.error('Esecuzione bloccata: rimuovi o sostituisci i nodi con risorse non accessibili.');
          console.warn('[run-blocked] access_denied nodes:\n' + denied.summary);
          return;
        }
        return execution.prepareExecute(opts);
      },
      onCaricanExecute:             (nodeId, cfg) => caricaExecution.handleExecute({ carica_node_data: cfg }),
      onUpdateProjectSource:        handleUpdateProjectSource,
      onUpdateProjectSourceColumns: handleUpdateProjectSourceColumns,
      onUpdateFileSourceColumns:    handleUpdateFileSourceColumns,
      onUpdateTransform:            handleUpdateTransform,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // toast/nodesRef/describeAccessDeniedNodes letti via closure stabile;
    // re-bind solo al cambio degli handler hook delle azioni canvas.
  }, [preview, execution, caricaExecution,
      handleUpdateProjectSource, handleUpdateProjectSourceColumns,
      handleUpdateFileSourceColumns, handleUpdateTransform]);

  // Sincronizza connections nello store
  useEffect(() => {
    useCanvasStore.getState().setConnections(connections);
  }, [connections]);

  // ===== POST-VALIDATION NODE HIGHLIGHTS (FE-PHASE3-VALIDATION-UX-002) =====
  // Dopo che ResultNode esegue validation (manuale, execute, preview), lo store
  // contiene i nodeId con issue. Applichiamo un outline amber ai nodi coinvolti.
  const validationHighlights = useCanvasStore(s => s.validationHighlights);

  useEffect(() => {
    if (!validationHighlights || validationHighlights.length === 0) {
      // Rimuovi eventuali highlight precedenti
      setNodes(nds => nds.map(n => {
        if (!n.style?.border?.includes('#FBBF24')) return n;
        const { border, boxShadow, ...restStyle } = n.style || {};
        return { ...n, style: Object.keys(restStyle).length ? restStyle : undefined };
      }));
      return;
    }
    const highlightSet = new Set(validationHighlights);
    setNodes(nds => nds.map(n => ({
      ...n,
      style: highlightSet.has(n.id)
        ? { ...n.style, border: '2px solid #FBBF24', boxShadow: '0 0 8px rgba(251,191,36,0.25)' }
        : n.style,
    })));
  }, [validationHighlights, setNodes]);

  // ===== SAVE =====

  const handleSaveCanvas = useCallback(async () => {
    try { setSaving(true); await handleSaveQuiet(); }
    catch (err) { console.error('[ManipolaEditor] Errore salvataggio:', err); toast.error('Errore durante il salvataggio'); }
    finally { setSaving(false); }
  }, [handleSaveQuiet]);

  // ===== EDGE HANDLERS =====

  const onConnect = useCallback((params) => {
    setEdges(eds => {
      const newEdges = addEdge({ ...params, ...defaultEdgeOptions }, eds);
      setTimeout(() => { propagateColumns(nodesRef.current, newEdges); handleSaveQuiet().then(() => refreshSchemas()); }, 50);
      return newEdges;
    });
  }, [setEdges, propagateColumns, refreshSchemas, handleSaveQuiet]);

  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    setEdges(eds => eds.filter(e => e.id !== edge.id));
    setTimeout(() => { propagateColumns(nodesRef.current, edgesRef.current); handleSaveQuiet(); }, 50);
  }, [setEdges, propagateColumns, handleSaveQuiet]);

  // ===== ADD NODE HANDLERS =====

  const getPos = () => getViewportCenter({ screenToFlowPosition });

  const handleAddTransformNode = useCallback(() => {
    setNodes(nds => nds.concat({ id: `transform_${Date.now()}`, type: 'transform', position: getPos(),
      data: { transform_op: 'join', config: {}, columns: [] } }));
  }, [setNodes]);

  const handleAddMultiJoinNode = useCallback(() => {
    setNodes(nds => nds.concat({ id: `multi_join_${Date.now()}`, type: 'multi_join', position: getPos(),
      data: { config: { join_keys: [], join_type: 'inner' }, columns: [], inputColumns: [] } }));
  }, [setNodes]);

  const handleAddStaticValuesNode = useCallback(() => {
    setNodes(nds => nds.concat({ id: `static_${Date.now()}`, type: 'staticValues', position: getPos(),
      data: { label: 'Valori Statici', fields: [] } }));
  }, [setNodes]);

  const handleAddCommentNode = useCallback(() => {
    setNodes(nds => nds.concat({ id: `comment_${Date.now()}`, type: 'comment', position: getPos(), zIndex: -1,
      style: { width: 250, height: 150 },
      data: { level: '', label: '', title: '', color: '#fbbf24' } }));
  }, [setNodes]);

  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'result')) { toast.warning('Puoi avere solo un nodo risultato per progetto'); return; }
    setNodes(nds => nds.concat({ id: `result_${Date.now()}`, type: 'result', position: getPos(),
      data: { isManipulate: true, fileName: 'output', format: 'csv', availableColumns: [],
        group_by_enabled: false, group_by_columns: [], aggregations: [],
        order_by_enabled: false, order_by_columns: [],
        having_enabled: false, having_conditions: [] } }));
  }, [setNodes]);

  const handleAddDataSourceNode = useCallback((mode = 'source') => {
    const newNodeId = `datasource_${Date.now()}`;
    setNodes(nds => nds.concat({
      id: newNodeId, type: 'datasource_node', position: getPos(),
      data: { mode, datasource_id: '', datasource_name: '', file_mode: 'fixed',
        file_path: '', folder_path: '', dynamic_rule: 'latest', pattern: '',
        dest_folder: '', dest_filename: '', dest_format: 'csv', columns: [] }
    }));
  }, [setNodes]);

  const handleAddCaricanNode = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!currentNodes.some(n => n.type === 'result')) { toast.warning('Aggiungi prima un nodo Output'); return; }
    if (currentNodes.some(n => n.type === 'carica'))  { toast.warning('Puoi avere solo un nodo CARICA per progetto'); return; }
    const resultNode = currentNodes.find(n => n.type === 'result');
    const newNode = { id: `carica_${Date.now()}`, type: 'carica',
      position: { x: resultNode.position.x + 400, y: resultNode.position.y },
      data: { dest_type: 'db', connection_id: '', schema_name: '', table_name: '', write_mode: 'create',
        column_mappings: [], match_columns: [], batch_size: 1000, transaction_mode: 'all_or_nothing',
        create_spatial_index: true, conflict_policy: 'abort',
        arcgis_connection_id: '', arcgis_service_url: '', arcgis_layer_url: '', arcgis_operation: 'add',
        arcgis_new_service_name: '', arcgis_new_layer_name: 'Layer 0',
        availableColumns: resultNode.data?.availableColumns || [] } };
    setNodes(nds => nds.concat(newNode));
    setEdges(eds => eds.concat({ id: `edge_result_carica_${Date.now()}`, source: resultNode.id, target: newNode.id,
      type: 'smoothstep', animated: true, style: { stroke: '#7c3aed', strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#7c3aed' }, data: {} }));
  }, [setNodes, setEdges]);

  // ===== DRAG & DROP =====

  const onDragStart = (event, type, data) => {
    event.dataTransfer.setData('application/reactflow', type);
    event.dataTransfer.setData('itemData', JSON.stringify(data));
    event.dataTransfer.effectAllowed = 'move';
  };

  const onDragOver = useCallback((event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }, []);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const type        = event.dataTransfer.getData('application/reactflow');
    const itemDataStr = event.dataTransfer.getData('itemData');
    if (!type || !itemDataStr) return;
    const itemData = JSON.parse(itemDataStr);
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });

    let newNode;
    if (type === 'project_source') {
      const newNodeId = `source_${Date.now()}`;
      newNode = { id: newNodeId, type: 'project_source', position,
        data: { source_project_id: itemData.id, columns: [],
          onChange: (val) => handleUpdateProjectSource(newNodeId, val),
          onUpdateColumns: handleUpdateProjectSourceColumns } };
    } else if (type === 'file_source') {
      const newNodeId = `file_${Date.now()}`;
      newNode = { id: newNodeId, type: 'file_source', position,
        data: { file_source_id: itemData.id, columns: [],
          onUpdateColumns: handleUpdateFileSourceColumns } };
    }

    if (newNode) {
      setNodes(nds => nds.concat(newNode));
      setTimeout(() => { handleSaveQuiet().then(() => refreshSchemas()); }, 100);
    }
  }, [screenToFlowPosition, setNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleUpdateFileSourceColumns, refreshSchemas, handleSaveQuiet]);

  // ===== RENDER =====

  const handleBack = () => navigate('/');
  const hasResultNode  = nodes.some(n => n.type === 'result');
  const hasCaricanNode = nodes.some(n => n.type === 'carica');

  // Guard branches gestiti da EditorShell (FE-SPLIT-004).

  // beforeCanvas slot: banner accessibilita' nodi cross-tenant.
  const accessDeniedBanner = <AccessDeniedBanner nodes={nodes} />;

  // topbar slot
  const topbar = (
    <div className="manipola-toolbar">
        <div className="manipola-toolbar__left">
          <div className="manipola-toolbar__logo">
            <svg width="90" height="28" viewBox="0 0 350 90" xmlns="http://www.w3.org/2000/svg">
              <polygon points="16,58 52,40 88,58 52,76" fill="rgba(245,166,35,0.10)"  stroke="#F5A623" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,44 52,26 88,44 52,62" fill="rgba(155,109,255,0.10)" stroke="#9B6DFF" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,30 52,12 88,30 52,48" fill="rgba(52,193,122,0.12)"  stroke="#34C17A" strokeWidth="2"   strokeLinejoin="round"/>
              <line x1="16" y1="30" x2="16" y2="58" stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
              <text x="108" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" className="manipola-toolbar__logo-text">Plen</text>
              <text x="222" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill="#4B8BFF">ora</text>
            </svg>
          </div>
          <div className="manipola-toolbar__sep" />
          <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--back" onClick={handleBack}>← Indietro</button>
        </div>
        <div className="manipola-toolbar__center">
          <h2 className="manipola-toolbar__title">{project?.metadata?.name}</h2>
          <span className="manipola-toolbar__badge">MANIPOLA</span>
          {schemasLoading && <span className="manipola-toolbar__badge" style={{background: '#FF9800'}}>⏳ Aggiornamento schemi...</span>}
        </div>
        <div className="manipola-toolbar__right">
          {!isReadOnly && (
            <>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--static" onClick={handleAddStaticValuesNode} title="Aggiungi valori statici con variabili template">📝 Valori Statici</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--annotation" onClick={handleAddCommentNode} title="Aggiungi annotazione al canvas">💬 Nota</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--transform" onClick={handleAddTransformNode}>+ Transform</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--join" onClick={handleAddMultiJoinNode} title="Unisci più sorgenti dati">🔗 Multi-Join</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--output" onClick={handleAddResultNode}>📁 Output</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--datasource" onClick={() => handleAddDataSourceNode('source')} title="Aggiungi sorgente DataSource (SharePoint/FTP/Local)">🗄️ Data Source</button>
            </>
          )}
          <ValidationStatusBadge />
          <div className="manipola-theme-toggle">
            <div className={`manipola-theme-opt${theme==='dark'?' manipola-theme-opt--on':''}`} onClick={() => setTheme('dark')}>🌙 Scuro</div>
            <div className={`manipola-theme-opt${theme==='light'?' manipola-theme-opt--on':''}`} onClick={() => setTheme('light')}>☀️ Chiaro</div>
          </div>
          {!isReadOnly && (
            <>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--carica" onClick={handleAddCaricanNode} disabled={!hasResultNode || hasCaricanNode} title="Aggiungi nodo per caricare su database">💾 Carica DB</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--save" onClick={handleSaveCanvas} disabled={saving}>{saving ? 'Salvataggio...' : '💾 Salva'}</button>
            </>
          )}
        </div>
      </div>
  );

  // sidebar slot
  const sidebar = !isReadOnly ? (
    <EditorSidebar
      variant="manipola" projects={availableSources} files={availableFiles} onDragStart={onDragStart}
      projectsConfig={{ title: '📊 Sorgenti Progetto', subtitle: 'Trascina un progetto SQL nel canvas',
        emptyMessage: 'Nessun progetto SQL disponibile.', emptyHint: 'Crea prima un progetto di tipo Estrai.',
        filterTypes: ['estrai', 'manipola', 'arricchisci'], showSearch: true }}
      filesConfig={{ title: '📁 File Esterni', subtitle: 'Trascina un file nel canvas',
        emptyMessage: 'Nessun file disponibile.', uploadLink: '/files', uploadLinkText: 'Carica file esterni' }}
    />
  ) : null;

  // modals slot
  const modals = (
    <ModalErrorBoundary>
      <Suspense fallback={null}>
        <ExecutionModal isOpen={execution.showModal} onClose={() => execution.closeModal()} isExecuting={execution.isExecuting} result={execution.result} onDownload={execution.downloadTaskResult} title="🔄 Esecuzione Pipeline Manipola" projectType="manipola" caricaTasks={execution.caricaTasks} caricaPolling={execution.caricaPolling} awaitingLaunch={execution.awaitingLaunch} onLaunch={execution.launch} user={user} />
        <ExecutionModal isOpen={caricaExecution.showModal} onClose={() => caricaExecution.closeModal()} isExecuting={caricaExecution.isExecuting} result={caricaExecution.result} title="📤 Caricamento Dati" projectType="arricchisci" progress={caricaExecution.progress} progressMessage={caricaExecution.progressMessage} taskStatus={caricaExecution.taskStatus} onCancel={caricaExecution.cancelTask} />
        <PreviewModal isOpen={preview.showModal} onClose={preview.closeModal} onRefresh={limit => preview.handlePreview(limit)}
          data={preview.data} loading={preview.loading} error={preview.error}
          permissionRevoked={preview.permissionRevoked}
          title="Anteprima Pipeline Manipola" projectType="manipola" disableGrouping />
      </Suspense>
    </ModalErrorBoundary>
  );

  return (
    <EditorShell
      projectId={projectId}
      prefix="manipola"
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
        fitViewOptions: { padding: 0.2 },
        minZoom: 0.5,
        maxZoom: 2,
        backgroundProps: { color: '#aaa', gap: 20 },
        showMiniMap: false,
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
        modals,
      }}
    />
  );
};

const ManipolaEditor = () => (
  <ReactFlowProvider>
    <ConfirmProvider>
      <ManipolaEditorContent />
    </ConfirmProvider>
  </ReactFlowProvider>
);

export default ManipolaEditor;