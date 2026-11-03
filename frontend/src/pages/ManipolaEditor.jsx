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
import React, { useState, useCallback, useEffect, useMemo, useRef, lazy, Suspense } from 'react';
import { addEdge, useReactFlow, ReactFlowProvider, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';
import EditorShell from '../components/editor/EditorShell';
import { ConfirmProvider } from '../context/ConfirmContext';
import { createTask } from '../api';
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
import PlenoraLogo from '../components/common/PlenoraLogo';

// Modali lazy-loaded
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const CanvasVersionsModal = lazy(() => import('../components/modals/CanvasVersionsModal'));
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import DataSourceNode from '../components/nodes/DataSourceNode';
import { useSchemaInference, usePreview, useExecution, useCanvasCore, useCanvasSave, useUnsavedChangesGuard, useValidationHighlights } from '../hooks';
import { useEditorData } from '../hooks/useEditorData';
import useCanvasStore from '../store/useCanvasStore';
import { withAvailableColumns } from '../utils/columnPlans';
import { analyzeCanvas, branchSteps, nodeRole } from '../utils/canvasGraph';
import { backendAccessFlags, persistedNodeData, persistenceValue } from '../utils/nodeTypeRegistry';
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
import useColumnPropagation from '../hooks/useColumnPropagation';
import { deserializeNodes } from '../utils/nodeFactories';
import { describeAccessDeniedNodes } from '../utils/canvasAccessControl';
import AccessDeniedBanner from '../components/editor/AccessDeniedBanner';
import '../styles/ManipolaEditor.css';
import '../components/nodes/StaticValuesNode.css';
import '../components/nodes/AnnotationNode.css';
import '../components/nodes/DataSourceNode.css';
import { useAuth } from '../context/AuthContext';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';
import { buildManipolaPaletteGroups } from '../components/canvas/manipolaPalette';
import { fetchTransformRegistry, getCachedTransformRegistry, isMultiInputOp } from '../utils/transformRegistry';

// Costante stabile — evita array literal nelle deps di useEditorData
const MANIPOLA_SOURCE_TYPES = ['estrai', 'manipola', 'arricchisci'];

const nodeTypes = {
  project_source: ProjectSourceNode, file_source: FileSourceNode,
  project_input: ProjectInputNode,
  transform: TransformNode, multi_join: MultiJoinNode,
  result: ResultNode, staticValues: StaticValuesNode,
  carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode, comment: AnnotationNode,
  // React Flow `nodeTypes` chiave deve combaciare con `node.type`. Il
  // deserializer (`nodeFactories.createDataSourceNode`) e tutti gli altri
  // pattern del FE — EstraiEditor, QueryPreview, useEdgeHandlers,
  // graphUtils — usano la chiave camelCase
  // `datasourceNode`. Manteniamo `datasource_node` come alias per
  // i nodi appena aggiunti via `handleAddDataSourceNode` (vedi sotto)
  // finché quel path non viene allineato al deserializer.
  datasourceNode: DataSourceNode, datasource_node: DataSourceNode,
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
    nodes, edges, onNodesChange, onEdgesChange,
    loadGraph, addNodes, addEdges, editNodes, editEdges, insertNode, applyDerivedNodes,
    insertBetween, addSubgraph,
    nodesRef, edgesRef, updateNodeData, deleteNode,
    handleSaveQuiet, getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'manipola' });

  const { propagateColumns } = useColumnPropagation(applyDerivedNodes);

  const {
    project, connections, loading, error: loadError,
    availableSources, availableFiles,
    reload: reloadData,
  } = useEditorData(projectId, {
    withSources: true,
    sourceTypes: MANIPOLA_SOURCE_TYPES,
  });

  const { schemas, loading: schemasLoading, error: schemasError, refreshSchemas } = useSchemaInference(projectId);

  const preview   = usePreview(projectId, handleSaveQuiet);
  const { saving, handleSaveCanvas } = useCanvasSave(handleSaveQuiet, {
    logPrefix: '[ManipolaEditor]',
  });
  // I 3 throws fail-fast (canvas vuoto / Output mancante / sorgente mancante)
  // erano duplicati delle regole CANVAS_EMPTY / RESULT_NODE_MISSING /
  // RESULT_NO_SOURCE, ora coperte dalla validazione backend. Il flow
  // `ResultNode.handleExecuteWithValidation` valida (POST /validate) PRIMA di
  // chiamare `onExecute`: se fallisce apre il modal e ritorna; quindi questo
  // `onBeforeExecute` viene raggiunto solo a validation-passed.
  const execution = useExecution(projectId, {
    onBeforeExecute: handleSaveQuiet,
  });

  // CARICA asincrono via task worker
  // Deps `[]`: `nodesRef`/`edgesRef` sono ref Zustand stabili lifetime-bound
  // al componente (non subiscono re-render churn). `createTask` e' import
  // module-level, costante. Il polling generato dall'execute viene gestito
  // da `useExecution` che ha cleanup esplicito su unmount
  // (`useExecution.js:50-53`: `clearInterval` su tutti `caricaRefs.current`).
  // Anti-regression P0 frontend-pages: se in futuro qualcuno aggiunge
  // dipendenze closure (non-ref state), la deps `[]` diventa un bug di
  // stale-closure — aggiornare la lista deps + verificare cleanup polling.
  const executeCaricanTask = useCallback(async (projId, options) => {
    const res = await createTask({
      task_type: 'carica', project_id: projId,
      canvas: { nodes: nodesRef.current, edges: dataFlowEdges(edgesRef.current) },
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
    // loadGraph: grafo dal backend, stato pulito e cronologia azzerata.
    loadGraph(loadedNodes, loadedEdges);
    // Cleanup dei timeout su unmount: pre-fix se l'utente navigava
    // verso un altro editor entro 300ms, `propagateColumns` /
    // `refreshSchemas` giravano sul canvas del progetto successivo
    // (gia' resettato dallo store) sovrascrivendo dati appena caricati.
    const propagateTimer = setTimeout(
      () => { propagateColumns(loadedNodes, loadedEdges); },
      300,
    );
    const schemaTimer = setTimeout(() => { refreshSchemas(); }, 100);
    return () => {
      clearTimeout(propagateTimer);
      clearTimeout(schemaTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/propagateColumns/refreshSchemas/deserializeNodes
    // omessi: load programmatico una sola volta per project, non ad ogni
    // re-bind degli handler.
  }, [project, loading]);

  // Propaga schemi ai nodi transform
  useEffect(() => {
    if (!schemas || Object.keys(schemas).length === 0) return;
    // Dato DERIVATO dallo schema backend (non dirty). Le colonne esistenti
    // vengono FUSE, non ricostruite: prima ogni refresh rimetteva
    // visible:true e perdeva id/alias -> le colonne nascoste tornavano
    // visibili e le rinomine non erano piu' tracciabili (audit R3.1).
    applyDerivedNodes(nds => {
      let changed = false;
      const next = nds.map(node => {
        const nodeSchema = schemas[node.id];
        if (!nodeSchema || nodeSchema.length === 0) return node;
        const existing = new Map((node.data?.columns || []).map(col => [col.name, col]));
        const columns = nodeSchema.map(col => ({
          visible: true,
          ...(existing.get(col.name) || {}),
          name: col.name,
          type: col.type,
          source_node: col.source_node,
          is_calculated: col.is_calculated,
        }));
        if (JSON.stringify(node.data?.columns || []) === JSON.stringify(columns)) return node;
        changed = true;
        return { ...node, data: { ...node.data, columns } };
      });
      return changed ? next : nds;
    });
  }, [schemas, applyDerivedNodes]);

  // Aggiorna availableColumns in ResultNode e CaricanNode (anche quando
  // cambiano le "Colonne in uscita" dell'Output).
  // Tutto cio' che decide le colonne in uscita dall'Output (selezione e
  // GROUP BY, columnPlans.resultOutputColumns): se cambia, i Carica seguono.
  const resultOutputColumnsKey = useCanvasStore(s => {
    const d = (s.nodes || []).find(n => n.type === 'result')?.data || {};
    return JSON.stringify([d.output_columns, d.group_by_enabled, d.group_by_columns, d.aggregations]);
  });
  useEffect(() => {
    // NOTA: 'nodes' rimosso dalle dipendenze intenzionalmente per evitare loop React.
    // nodes e edges vengono letti dentro l'updater (snapshot aggiornato)
    // e da edgesRef (ref stabile). schemas è la sola vera dipendenza esterna.
    // Dato DERIVATO: non marca dirty.
    applyDerivedNodes(nds => {
      const currentEdges = dataFlowEdges(edgesRef.current);
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

      // Carica: colonne dopo "Colonne in uscita" dell'Output (utils/columnPlans).
      return withAvailableColumns(nds, availableColumns);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // 'nodes' volutamente assente: viene letto nell'updater
    // (snapshot aggiornato) per evitare il loop nodes->effect->update.
  }, [edges, schemas, applyDerivedNodes, edgesRef, resultOutputColumnsKey]);

  const handleAutosaveError = useCallback((err) => {
    console.warn('[ManipolaEditor] Autosave non completato:', err?.message || err);
  }, []);

  // Pre-fix (P1 frontend-pages): `handleUpdateProjectSource` /
  // `handleUpdateFileSourceColumns` / `onConnect` chiamavano
  // `handleSaveQuiet()` dentro `setTimeout` senza mutex. Due
  // interazioni rapide (es. cambio sorgente + connessione edge)
  // serializzavano due `handleSaveQuiet` concorrenti su snapshot
  // di canvas diversi — il piu' lento (snapshot intermedio) poteva
  // sovrascrivere il piu' veloce (snapshot finale) sul backend.
  //
  // Post-fix: `autosaveInFlightRef` single-flight semaphore.
  // Se un autosave e' in volo, l'invocazione successiva imposta
  // `autosavePendingRef=true` e re-triggera al completamento del
  // precedente. Coalesce: due update rapidi → 1 save in volo + 1
  // pending finale (no piu' di 2 save totali per N update).
  const autosaveInFlightRef = useRef(false);
  const autosavePendingRef  = useRef(false);

  const triggerAutosave = useCallback(() => {
    if (autosaveInFlightRef.current) {
      autosavePendingRef.current = true;
      return;
    }
    autosaveInFlightRef.current = true;
    handleSaveQuiet()
      .then(() => refreshSchemas())
      .catch(handleAutosaveError)
      .finally(() => {
        autosaveInFlightRef.current = false;
        // Se durante l'in-flight e' arrivato almeno un nuovo trigger,
        // re-lancia ora (coalesce in 1 save finale).
        if (autosavePendingRef.current) {
          autosavePendingRef.current = false;
          triggerAutosave();
        }
      });
  }, [handleSaveQuiet, refreshSchemas, handleAutosaveError]);

  const handleUpdateProjectSource = useCallback((nodeId, projectSourceId) => {
    updateNodeData(nodeId, { source_project_id: projectSourceId });
    setTimeout(triggerAutosave, 100);
  }, [updateNodeData, triggerAutosave]);

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
      triggerAutosave();
    }, 100);
  }, [updateNodeData, propagateColumns, triggerAutosave]);

  const handleUpdateTransform = useCallback((nodeId, newData) => {
    // TransformNode scrive gia' la config nello store (patchNodeConfig) e
    // chiama questo handler SENZA dati solo per ripropagare le colonne:
    // riscriverla qui con lo snapshot del render cancellava le patch
    // ravvicinate (audit R1.5).
    if (newData) updateNodeData(nodeId, newData);
    //
    // Non autosalviamo ogni micro-update del form transform: controlli come
    // aggregation_list emettono piu' patch ravvicinate e i save concorrenti
    // possono arrivare fuori ordine, ripristinando configurazioni vecchie
    // dopo un save manuale. Il canvas resta dirty e viene salvato dal pulsante
    // Salva o dal pre-flight di execute/preview.
    setTimeout(() => {
      // Stato corrente dello store, non nodesRef (aggiornato solo al render).
      const { nodes: currentNodes, edges: currentEdges } = useCanvasStore.getState();
      propagateColumns(currentNodes, currentEdges);
    }, 50);
  }, [updateNodeData, propagateColumns]);

  const handleDataSourceSchemaUpdated = useCallback((nodeId, updates) => {
    const updatedNodes = nodesRef.current.map(node => (
      node.id === nodeId
        ? { ...node, data: { ...node.data, ...updates } }
        : node
    ));
    propagateColumns(updatedNodes, edgesRef.current);
    triggerAutosave();
  }, [nodesRef, edgesRef, propagateColumns, triggerAutosave]);

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
      onDataSourceSchemaUpdated:    handleDataSourceSchemaUpdated,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // toast/nodesRef/describeAccessDeniedNodes letti via closure stabile;
    // re-bind solo al cambio degli handler hook delle azioni canvas.
  }, [preview, execution, caricaExecution,
      handleUpdateProjectSource, handleUpdateProjectSourceColumns,
      handleUpdateFileSourceColumns, handleUpdateTransform,
      handleDataSourceSchemaUpdated]);

  // Sincronizza connections nello store
  useEffect(() => {
    useCanvasStore.getState().setConnections(connections);
  }, [connections]);

  // Outline amber sui nodi con issue di validazione (popolati dallo store
  // da ResultNode.runValidation). Hook condiviso con ArricchisciEditor.
  useValidationHighlights(applyDerivedNodes);

  // Effetti di un nuovo collegamento: propagazione colonne, autosave, schemi.
  // Condiviso da onConnect e dall'inserimento dalla palette.
  const afterConnect = useCallback((newEdges, propagate = true) => {
    setTimeout(() => {
      if (propagate) {
        propagateColumns(nodesRef.current, newEdges);
      }
      // triggerAutosave rispetta il single-flight semaphore.
      // refreshSchemas non e' piu' chained al save: il semaphore
      // potrebbe codarlo (re-fire) o saltarlo se gia' in flight.
      // refreshSchemas locale e' safe ad essere chiamato comunque.
      triggerAutosave();
      refreshSchemas();
    }, 50);
  }, [propagateColumns, refreshSchemas, triggerAutosave]);

  // H-8 (2026-05-26): onConnect e onEdgeContextMenu DEVONO passare per
  // `triggerAutosave()` (semaphore single-flight + coda re-fire). Pre-fix:
  // chiamavano `handleSaveQuiet()` direttamente bypassando il semaphore
  // `autosaveInFlightRef`. Risultato: due interazioni veloci (es. drop
  // edge + change source) generavano due PUT /canvas concorrenti — il
  // PUT con snapshot intermedio poteva arrivare al server DOPO il PUT
  // con snapshot finale, sovrascrivendo i dati corretti.
  // refreshSchemas resta accoppiato a connect (non a edge-remove).
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
      const newEdges = addEdge(edge, eds);
      afterConnect(newEdges, !isScalarProjectInputNode(sourceNode));
      return newEdges;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editEdges, afterConnect]);


  const onEdgeContextMenu = useCallback((event, edge) => {
    event.preventDefault();
    editEdges(eds => eds.filter(e => e.id !== edge.id));
    setTimeout(() => {
      propagateColumns(nodesRef.current, edgesRef.current);
      // H-8: triggerAutosave invece di handleSaveQuiet diretto.
      triggerAutosave();
    }, 50);
  }, [editEdges, propagateColumns, triggerAutosave]);

  const getPos = () => getViewportCenter({ screenToFlowPosition });

  const handleAddTransformNode = useCallback(() => {
    addNodes({ id: `transform_${Date.now()}`, type: 'transform', position: getPos(),
      data: { transform_op: 'join', config: {}, columns: [] } });
  }, [addNodes]);

  const handleAddMultiJoinNode = useCallback(() => {
    addNodes({ id: `multi_join_${Date.now()}`, type: 'multi_join', position: getPos(),
      data: { config: { join_keys: [], join_type: 'inner' }, columns: [], inputColumns: [] } });
  }, [addNodes]);

  const handleAddStaticValuesNode = useCallback(() => {
    addNodes({ id: `static_${Date.now()}`, type: 'staticValues', position: getPos(),
      data: { label: 'Valori Statici', fields: [] } });
  }, [addNodes]);

  const handleAddProjectInputNode = useCallback(() => {
    const ordinal = nodesRef.current.filter(node => node.type === 'project_input').length + 1;
    addNodes({
      id: `project_input_${Date.now()}`,
      type: 'project_input',
      position: getPos(),
      data: { input_key: `input_${ordinal}`, label: `Input ${ordinal}`, input_kind: 'file', required: true, expected_columns: [], validation: {}, bindings: [], order: ordinal - 1 },
    });
  }, [addNodes, nodesRef]);

  const handleAddCommentNode = useCallback(() => {
    addNodes({ id: `comment_${Date.now()}`, type: 'comment', position: getPos(), zIndex: -1,
      style: { width: 250, height: 150 },
      data: { level: '', label: '', title: '', color: '#fbbf24' } });
  }, [addNodes]);

  const handleAddResultNode = useCallback(() => {
    if (nodesRef.current.some(n => n.type === 'result')) { toast.warning('Puoi avere solo un nodo risultato per progetto'); return; }
    addNodes({ id: `result_${Date.now()}`, type: 'result', position: getPos(),
      data: { isManipulate: true, fileName: 'output', format: 'csv', availableColumns: [],
        group_by_enabled: false, group_by_columns: [], aggregations: [],
        order_by_enabled: false, order_by_columns: [],
        having_enabled: false, having_conditions: [] } });
  }, [addNodes]);

  const handleAddDataSourceNode = useCallback((mode = 'source') => {
    const newNodeId = `datasource_${Date.now()}`;
    addNodes({
      id: newNodeId, type: 'datasource_node', position: getPos(),
      data: { mode, datasource_id: '', datasource_name: '', file_mode: 'fixed',
        file_path: '', folder_path: '', dynamic_rule: 'latest', pattern: '',
        dest_folder: '', dest_filename: '', dest_format: 'csv', columns: [] }
    });
  }, [addNodes]);

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
    addNodes(newNode);
    addEdges({ id: `edge_result_carica_${Date.now()}`, source: resultNode.id, target: newNode.id,
      type: 'smoothstep', animated: true, style: { stroke: '#7c3aed', strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: '#7c3aed' }, data: {} });
  }, [addNodes, addEdges]);

  // ── Palette "Cosa vuoi fare?" (Fase 4, M2) ────────────────────────────
  const [transformRegistry, setTransformRegistry] = useState(getCachedTransformRegistry());
  useEffect(() => {
    if (transformRegistry) return undefined;
    let alive = true;
    fetchTransformRegistry().then(r => { if (alive && r) setTransformRegistry(r); });
    return () => { alive = false; };
  }, [transformRegistry]);

  // Il nuovo passo nasce dopo il nodo di partenza, gia' selezionato
  // (pannello aperto). Se quel nodo ha un solo passo successivo e il nuovo
  // lascia passare i dati, entra nella catena (store.insertNode).
  const handlePalettePick = useCallback((item, afterId) => {
    if (item.kind === 'carica') { handleAddCaricanNode(); return; }
    const current = nodesRef.current;
    if (item.kind === 'result' && current.some(n => n.type === 'result')) {
      toast.warning('Puoi avere solo un nodo risultato per progetto');
      return;
    }
    const position = getPos();
    const stamp = Date.now();
    let newNode;
    if (item.kind === 'multi_join') {
      newNode = { id: `multi_join_${stamp}`, type: 'multi_join', position,
        data: { config: { join_keys: [], join_type: 'inner' }, columns: [], inputColumns: [] } };
    } else if (item.kind === 'result') {
      newNode = { id: `result_${stamp}`, type: 'result', position,
        data: { isManipulate: true, fileName: 'output', format: 'csv', availableColumns: [],
          group_by_enabled: false, group_by_columns: [], aggregations: [],
          order_by_enabled: false, order_by_columns: [],
          having_enabled: false, having_conditions: [] } };
    } else {
      newNode = { id: `transform_${stamp}`, type: 'transform', position,
        data: { transform_op: item.id, config: {}, columns: [] } };
    }
    const inHandle = item.kind === 'multi_join' || isMultiInputOp(item.id)
      ? 'input-0'
      : (item.kind === 'result' ? 'input' : null);
    const outcome = insertNode({
      node: newNode,
      afterId,
      passThrough: item.kind !== 'result',
      inHandle,
      makeEdge: (params) => ({
        ...params, ...defaultEdgeOptions,
        id: `xy-edge__${params.source}${params.sourceHandle || ''}-${params.target}${params.targetHandle || ''}`,
      }),
    });
    if (outcome?.added.length) afterConnect(outcome.edges);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insertNode, afterConnect, handleAddCaricanNode, toast]);

  const palette = useMemo(() => ({
    groups: buildManipolaPaletteGroups(transformRegistry),
    onPick: handlePalettePick,
  }), [transformRegistry, handlePalettePick]);

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
    const visibleCols = (n) => (n.data?.columns || [])
      .filter(c => c && typeof c === 'object' && c.visible !== false)
      .map(c => ({ ...c, name: c.alias || c.name }));
    const leftCols = visibleCols(target);
    const rightCols = visibleCols(end);
    const shared = leftCols.find(c => rightCols.some(r => r.name === c.name))?.name;
    const config = op === 'join'
      ? { how: 'left', name_conflict: 'keep_left', ...joinKeysPatch(shared ? [[shared, shared]] : [], leftCols, rightCols) }
      : {};
    // Il ramo si chiama come la sua corsia (vista Passi e pannello).
    const headId = analyzeCanvas(current, edgesRef.current).laneOf.get(endId)?.headId;
    const head = current.find(n => n.id === headId) || end;
    const branchName = nodeTitle(head.type, head.data);
    const newNode = {
      id: `transform_${Date.now()}`, type: 'transform', position: getPos(),
      data: { transform_op: op, config, columns: [], title: `${op === 'join' ? 'Unisci' : 'Accoda'} ${branchName}` },
    };
    const done = insertBetween({
      node: newNode, targetId, inHandle: 'input-0', makeEdge: branchEdge,
      extraEdges: [branchEdge({ source: endId, sourceHandle: null, target: newNode.id, targetHandle: 'input-1' })],
    });
    if (!done) return;
    afterConnect(done.edges);
    if (op === 'join' && !shared) toast.warning(`Ramo collegato: scegli le chiavi del Join nel pannello (nessuna colonna con lo stesso nome).`);
    else toast.success(`Ramo "${branchName}" collegato dopo "${nodeTitle(target.type, target.data)}".`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insertBetween, afterConnect, branchEdge, toast]);

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
    const clones = steps.map((n) => {
      // Senza colonne (le ricalcola la propagazione) ne' flag di accesso
      // del nodo originale (stessa regola di backendAccessFlags); titolo
      // sempre distinto dall'originale.
      const accessKeys = Object.keys(backendAccessFlags(n));
      const data = Object.fromEntries(Object.entries(persistenceValue(persistedNodeData(n)))
        .filter(([k]) => k !== 'columns' && !accessKeys.includes(k)));
      return {
        id: idMap.get(n.id), type: n.type,
        position: { x: n.position.x + dx, y: n.position.y + dy },
        data: { ...data, columns: [], title: `${nodeTitle(n.type, n.data)} · ${otherName}` },
      };
    });
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
    afterConnect(done.edges);
    toast.success(`Ramo duplicato su "${otherName}": ${clones.length} ${clones.length === 1 ? 'passo copiato' : 'passi copiati'}. Collegalo dal pannello a destra.`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addSubgraph, afterConnect, toast]);

  const branchActions = useMemo(() => ({
    connectBranch,
    connectModes: [['join', 'Unisci con (Join)'], ['concat', 'Accoda a (Concatena)']],
    duplicateBranch,
  }), [connectBranch, duplicateBranch]);


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
      addNodes(newNode);
      setTimeout(() => {
        triggerAutosave();
      }, 100);
    }
  }, [screenToFlowPosition, addNodes, handleUpdateProjectSource, handleUpdateProjectSourceColumns, handleUpdateFileSourceColumns, triggerAutosave]);

  const { confirmNavigation } = useUnsavedChangesGuard();
  const handleBack = () => { if (confirmNavigation()) navigate('/'); };
  const hasResultNode  = nodes.some(n => n.type === 'result');
  const hasCaricanNode = nodes.some(n => n.type === 'carica');

  // beforeCanvas slot: banner accessibilita' nodi cross-tenant.
  const accessDeniedBanner = <AccessDeniedBanner nodes={nodes} />;

  // topbar slot
  const topbar = (
    <div className="manipola-toolbar">
        <div className="manipola-toolbar__left">
          <div className="manipola-toolbar__logo">
            <PlenoraLogo gridLines="left" plenClassName="manipola-toolbar__logo-text" />
          </div>
          <div className="manipola-toolbar__sep" />
          <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--back" onClick={handleBack}>← Indietro</button>
        </div>
        <div className="manipola-toolbar__center">
          <h2 className="manipola-toolbar__title">{project?.metadata?.name}</h2>
          <span className="manipola-toolbar__badge">MANIPOLA</span>
          {schemasLoading && <span className="manipola-toolbar__badge" style={{background: '#FF9800'}}>⏳ Aggiornamento schemi...</span>}
          {schemasError && !schemasLoading && (
            <span
              className="manipola-toolbar__badge"
              style={{ background: '#D32F2F', cursor: 'pointer' }}
              title={`Errore inferenza schema: ${schemasError}. Clicca per ritentare.`}
              onClick={refreshSchemas}
              role="button"
              tabIndex={0}
            >
              ⚠️ Schemi non caricati
            </span>
          )}
        </div>
        <div className="manipola-toolbar__right">
          {!isReadOnly && (
            <>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--static" onClick={handleAddStaticValuesNode} title="Aggiungi valori statici con variabili template">📝 Valori Statici</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--static" onClick={handleAddProjectInputNode} title="Aggiungi un input valorizzato quando il progetto viene eseguito">⌨ Input</button>
              <button type="button" className="manipola-toolbar__btn manipola-toolbar__btn--versions" onClick={() => setShowVersions(true)} title="Versioni del canvas (snapshot / ripristino)">🕘 Versioni</button>
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
        {showVersions && (
          <CanvasVersionsModal isOpen onClose={() => setShowVersions(false)} projectId={projectId} />
        )}
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
