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
import JoinEdgePanel from '../components/canvas/JoinEdgePanel';
import { estraiJoinEdges, paletteJoinEnds, presentJoinEdges, relationJoinEnds, selfJoinAlias } from '../utils/estraiJoins';

// Modali lazy-loaded — caricati solo quando l'utente li apre
const PreviewModal = lazy(() => import('../components/modals/PreviewModal'));
const ExecutionModal = lazy(() => import('../components/modals/ExecutionModal'));
const CanvasVersionsModal = lazy(() => import('../components/modals/CanvasVersionsModal'));
import StaticValuesNode from '../components/nodes/StaticValuesNode';
import ProjectInputNode from '../components/nodes/ProjectInputNode';
import AnnotationNode from '../components/nodes/AnnotationNode';
import { ConfirmProvider } from '../context/ConfirmContext';
import DataSourceNode from '../components/nodes/DataSourceNode';
import '../components/nodes/DataSourceNode.css';
import { columnSnapshot } from '../hooks/useColumns';
import { columnsOf, estraiSourceLabel } from '../utils/columnSource';
import { createEdgeFromData, deserializeNodes } from '../utils/nodeFactories';
import { getLayerSchema } from '../api/features';
import { featureLayerData } from '../utils/featureLayers';
import { serverVersionOf } from '../utils/canvasVersion';
import { executionModalProps, executionToolbarProps } from '../hooks/useExecution';
import { ResourceError } from '../components/common/ListState';
import { usePreview, useExecution, useValidationHighlights, useEdgeHandlers, useCanvasCore, useCanvasSave, useUnsavedChangesGuard } from '../hooks';
import { useSignOut } from '../hooks/useSignOut';
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
import { can } from '../utils/permissions';
import { ModalErrorBoundary } from '../components/common/ErrorBoundary';
import { useHexCanvas } from '../utils/hexCanvas';
import { menuEntry, term } from '../utils/glossary';
import useEditorActions from '../components/editor/useEditorActions';
import useCatalogAdd from '../components/editor/useCatalogAdd';
import { catalogConstraintNodes, catalogMenuItems, catalogPaletteItems } from '../components/editor/editorNodeCatalog';
import { pressable } from '../utils/pressable';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch, useSearchPage } from '../hooks/useSearch';
import { useCanvasDerived } from '../hooks/useCanvasDerived';

// Quello che l'editor di Estrai legge del canvas (UI-2, V4): valori semplici
// e liste di nomi, confrontati in modo superficiale da useCanvasDerived.
function estraiCanvasInfo(s) {
  const nodes = s.nodes || [];
  return {
    nodeCount: nodes.length,
    edgeCount: (s.edges || []).length,
    tableNames: nodes.filter(n => n.type === 'tableNode' || n.type === 'source').map(n => n.data?.table_name).filter(Boolean).join('\u0000'),
    layerUrls: nodes.filter(n => n.type === 'featureLayerNode').map(n => n.data?.layer_url).filter(Boolean).join('\u0000'),
    hasResultNode: nodes.some(n => n.type === 'resultNode'),
    hasCaricanNode: nodes.some(n => n.type === 'carica'),
    hasCaricanArcGISNode: nodes.some(n => n.type === 'carica_arcgis' || n.type === 'caricaArcgis'),
  };
}

const nodeTypes = {
  tableNode: TableNode, resultNode: ResultNode, staticValues: StaticValuesNode,
  project_input: ProjectInputNode,
  carica: CaricanNode, carica_arcgis: CaricanArcGISNode, caricaArcgis: CaricanArcGISNode,
  featureLayerNode: FeatureLayerNode, comment: AnnotationNode,
  datasourceNode: DataSourceNode
};

// I join stanno sull'arco e si configurano nel pannello laterale a partire
// dalla selezione dell'arco (Blocco A, raccolta b).
const edgeInspector = { ...estraiJoinEdges, Panel: JoinEdgePanel };

function EstraiEditor() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const hexCanvasRef = useRef(null);
  const [reactFlowInstance, setReactFlowInstance] = useState(null);
  const canvasLoadedForRef = useRef(null);
  const toast = useToast();

  useHexCanvas(hexCanvasRef);
  const {
    onNodesChange, onEdgesChange,
    loadGraph, addNodes, insertNode, applyDerivedNodes,
    nodesRef,
    getViewportCenter,
    isReadOnly, readOnlyProps
  } = useCanvasCore(projectId, { mode: 'estrai' });
  const {
    project, connections, loading, error: loadError,
    catalog, views, projectConnection, sourceType,
    catalogLoading, catalogError, reloadCatalog,
    reload: reloadData,
  } = useEditorData(projectId, { withCatalog: true });
  const TABLES_PAGE_SIZE = 15;

  const tableSearch = useSearch();
  const viewSearch = useSearch();
  const [viewsCollapsed, setViewsCollapsed]       = useState(false);
  const [tablesCollapsed, setTablesCollapsed]     = useState(false);
  const [showVersions, setShowVersions]           = useState(false);
  // Quante tabelle/viste si vedono: una ricerca nuova riparte dalla prima pagina.
  const [visibleTableCount, setVisibleTableCount] = useSearchPage(tableSearch, TABLES_PAGE_SIZE);
  const [visibleViewCount,  setVisibleViewCount]  = useSearchPage(viewSearch, TABLES_PAGE_SIZE);
  const { theme, setTheme: toggleTheme } = useTheme();

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
  const edgeHandlers = useEdgeHandlers({ resultNodeType: 'resultNode' });
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
    loadGraph(loadedNodes, [...loadedEdges, ...hydrateParameterBindingEdges(loadedNodes)], serverVersionOf(project));
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // loadGraph/deserializeNodes/createEdgeFromData omessi: stabili dal
    // ciclo di vita store. Includerli rifirerebbe il load a ogni re-render.
  }, [project, projectConnection, loading]);


  // Colonne dell'Output e dei Carica: dalla fonte unica (Blocco A, regola 4),
  // calcolate dal server con la stessa proiezione SQL dell'esecuzione.
  const handleAddRelatedLayer = useCallback(async (originNodeId, relation) => {
    const originNode = nodesRef.current.find(n => n.id === originNodeId);
    if (!originNode) return;
    if (nodesRef.current.some(n => n.type === 'featureLayerNode' && n.data.layer_url === relation.related_url)) {
      toast.warning('Questo layer è già presente nel canvas'); return;
    }
    let schema;
    // Fase 5 T2 (R4.6): il layer va nel canvas del progetto che l'ha chiesto.
    const forProject = useCanvasStore.getState().projectId;
    try { schema = await getLayerSchema(originNode.data.connection_id, relation.related_url); }
    catch (err) { toastApiError(toast, err, 'Errore caricamento schema'); return; }
    if (useCanvasStore.getState().projectId !== forProject) return;
    const newNodeId = `fl_${Date.now()}`;
    // Costruzione unica del layer (utils/featureLayers, R3.15).
    const newNode   = {
      id: newNodeId, type: 'featureLayerNode',
      position: { x: originNode.position.x, y: originNode.position.y + 350 },
      data: featureLayerData(schema, { layerUrl: relation.related_url, layerName: relation.name,
        connectionId: originNode.data.connection_id, connectionName: originNode.data.connection_name,
        serviceName: originNode.data.service_name }),
    };
    // Layer e join in un solo passo di annullamento; il join nasce da
    // completare (le chiavi della relazione sono solo un suggerimento nel
    // pannello, che si apre sull'arco).
    edgeHandlers.connectJoin({ node: newNode, ...relationJoinEnds(originNodeId, newNodeId, relation) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // nodesRef/toast stabili; connectJoin e' stabile (legge lo store).
  }, [edgeHandlers.connectJoin]);

  // Registra azioni editor nello store
  // Azioni dell'editor: registrate una volta, chiamano sempre l'ultima
  // versione (components/editor/useEditorActions, regola 9 Blocco A).
  useEditorActions({
    onPreview:          () => preview.handlePreview(),
    // Input e conferme: gia' nella finestra unica «Prima di avviare»
    // (editor/PipelineRunner, Blocco A regola 5).
    onExecute:          (nodeId, opts) => execution.handleExecute(opts),
    onAddRelatedLayer:  handleAddRelatedLayer,
  });

  useEffect(() => { useCanvasStore.getState().setConnections(connections); }, [connections]);
  // Output, caricamenti, Input runtime e Nota dal catalogo unico dei nodi
  // (UI-3, P30): nascono al centro della vista, con il collegamento
  // all'Output dove serve; Data Source e Valori statici restano fuori
  // (Estrai accetta solo sorgenti SQL/ArcGIS).
  const getPos = useCallback(() => getViewportCenter(reactFlowInstance), [getViewportCenter, reactFlowInstance]);
  const { addNode } = useCatalogAdd('estrai', getPos);

  // Costruisce un nodo tabella/vista (condiviso da drop, clic e palette).
  // P29 (UI-3): la stessa tabella si aggiunge piu' volte (self-join); ogni
  // copia dopo la prima riceve un alias libero (tabella_2, ...), lo stesso
  // che il backend darebbe (core/estrai: alias univoci per costruzione).
  const buildTableNode = useCallback((tableName, isView, position) => {
    const tableColumns = (isView ? views[tableName] : catalog[tableName]) || [];
    const present = nodesRef.current.filter(n => n.type === 'tableNode');
    const alias = present.some(n => n.data?.table_name === tableName)
      ? selfJoinAlias(tableName, present.map(n => n.data?.alias || n.data?.table_name))
      : null;
    return {
      id: `node_${Date.now()}`, type: 'tableNode', position,
      // Niente `label`: il titolo della tabella e' derivato da alias e nome
      // (nodeDescriptors), non una copia congelata (Blocco A, regola 7).
      data: { table_name: tableName, alias, type: 'source',
        columns: tableColumns.map(col => ({ name: typeof col === 'object' ? col.name : col,
          type: typeof col === 'object' ? col.type || 'str' : 'str', alias: null, visible: true })),
        filters: [], calculated_columns: [], db_type: projectConnection?.type || 'postgres' },
    };
  }, [catalog, views, projectConnection, nodesRef]);

  // ── Palette "Cosa vuoi fare?" (Fase 4, M2) ────────────────────────────
  // Dopo una tabella: un'altra tabella/vista del catalogo, collegata con un
  // join da completare nel pannello. Dall'Output: Carica DB / ArcGIS.
  // Rami (Fase 4, M6): in Estrai "Collega" e' un join tra tabelle, con lo
  // stesso gesto del collegamento manuale (la tabella scollegata e' la
  // sorgente del join, come trascinandola).
  const branchActions = useMemo(() => ({
    connectBranch: edgeHandlers.connectBranch,
    connectModes: [['join', 'Unisci con (Join)']],
  }), [edgeHandlers.connectBranch]);

  // U19 (UI-3): la ricerca della palette guarda anche le colonne
  // (`keywords`); oltre il limite la palette dice quante voci restano.
  // U12/E3: l'Output e' nella palette (gruppo «Uscite»), non solo nel menu.
  const tableItems = (source, kind) => Object.keys(source || {}).sort().map(t => ({
    id: `${kind}:${t}`, kind, name: t, label: t, icon: term(kind).icon,
    desc: `${(source[t] || []).length} col`,
    keywords: (source[t] || []).map(c => (typeof c === 'object' ? c.name : c)),
  }));
  const palette = useMemo(() => ({
    groups: [
      { label: 'Tabelle', items: tableItems(catalog, 'table'), limit: 40 },
      { label: 'Viste', items: tableItems(views, 'view'), limit: 40 },
      { label: 'Uscite', items: catalogPaletteItems('estrai', ['output', 'load_db', 'load_arcgis']) },
      { label: 'Strumenti', items: catalogPaletteItems('estrai', ['runtime_input', 'note']) },
    ],
    onPick: (item, afterId) => {
      if (item.kind === 'catalog') { addNode(item.catalogKey); return; }
      const after = nodesRef.current.find(n => n.id === afterId);
      const position = after ? { x: after.position.x - 290, y: after.position.y + 120 } : getViewportCenter(reactFlowInstance);
      const newNode = buildTableNode(item.name, item.kind === 'view', position);
      // Tabella dopo tabella = join: la nuova e' la sorgente, quella di
      // partenza il target (stesso verso del trascinamento manuale). Tabella
      // e join in un solo passo di annullamento, pannello sul join.
      if (after && (after.type === 'tableNode' || after.type === 'featureLayerNode')) {
        edgeHandlers.connectJoin({ node: newNode, ...paletteJoinEnds(newNode.id, after.id) });
        return;
      }
      // Posizione libera vicino al punto scelto, nodo selezionato (store.insertNode).
      insertNode({ node: newNode });
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [catalog, views, buildTableNode, insertNode, addNode, edgeHandlers.connectJoin]);

  // Click su una tabella/vista del catalogo → la aggiunge al CENTRO del canvas
  // (il drag resta per il posizionamento preciso).
  const addTableAtCenter = useCallback((tableName, isView = false) => {
    addNodes(buildTableNode(tableName, isView, getViewportCenter(reactFlowInstance)));
  }, [addNodes, buildTableNode, getViewportCenter, reactFlowInstance]);

  // Layer trascinato dal pannello dei servizi: nasce completo (colonne,
  // tipi e relazioni dallo schema del layer, utils/featureLayers), come il
  // layer correlato; niente dipende dall'apertura del pannello (N1, R3.15).
  const addFeatureLayerAt = useCallback(async (ld, position) => {
    const forProject = useCanvasStore.getState().projectId;
    let schema;
    try { schema = await getLayerSchema(ld.connectionId, ld.layerUrl); }
    catch (err) { toastApiError(toast, err, 'Schema del layer non caricato'); return; }
    if (useCanvasStore.getState().projectId !== forProject) return;
    addNodes({ id: `fl_${Date.now()}`, type: 'featureLayerNode', position,
      data: featureLayerData(schema, { layerUrl: ld.layerUrl, layerName: ld.layerName,
        connectionId: ld.connectionId, connectionName: ld.connectionName, serviceName: ld.serviceName,
        geometryType: ld.geometryType, featureCount: ld.featureCount }) });
  }, [addNodes, toast]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    if (!reactFlowInstance) return;
    const position = reactFlowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });

    const featureLayerRaw = event.dataTransfer.getData('featureLayerData');
    if (featureLayerRaw) {
      let ld;
      try { ld = JSON.parse(featureLayerRaw); } catch (err) {
        // Fase 5 T2 (N10): un trascinamento non leggibile si dice.
        console.warn('Errore parsing feature layer data:', err);
        toast.error('Il layer trascinato non è leggibile: riprova dal pannello dei servizi.');
        return;
      }
      addFeatureLayerAt(ld, position);
      return;
    }

    const tableName = event.dataTransfer.getData('tableName');
    if (!tableName) return;
    const isView = event.dataTransfer.getData('isView') === 'true';
    addNodes(buildTableNode(tableName, isView, position));
  }, [reactFlowInstance, addNodes, buildTableNode, toast, addFeatureLayerAt]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);
  // Uscite: chiede il guard unico (NavigationGuard + useBlocker); il logout
  // passa dall'unica uscita volontaria (hooks/useSignOut, una sola domanda).
  useUnsavedChangesGuard();
  const signOut = useSignOut();
  const handleBack              = () => navigate('/');
  // U19 (UI-3): la ricerca guarda anche i nomi delle colonne. P29: le
  // tabelle gia' nel canvas restano nell'elenco (self-join), segnate.
  const columnNames             = (cols) => (cols || []).map(c => (typeof c === 'object' ? c.name : c));
  const filteredTables          = useMemo(() => tableSearch.filter(Object.keys(catalog), t => [t, ...columnNames(catalog[t])]), [catalog, tableSearch.filter]); // eslint-disable-line react-hooks/exhaustive-deps
  // UI-2 (V4): quello che serve del canvas, ricalcolato solo quando cambia
  // il contenuto (hooks/useCanvasDerived), mai a ogni fotogramma.
  const canvasInfo              = useCanvasDerived(estraiCanvasInfo);
  const tablesInCanvas          = useMemo(() => new Set(canvasInfo.tableNames ? canvasInfo.tableNames.split('\u0000') : []), [canvasInfo.tableNames]);
  const availableTables         = filteredTables;
  const viewNames               = useMemo(() => Object.keys(views), [views]);
  const filteredViews           = viewSearch.filter(viewNames, v => [v, ...columnNames(views[v])]);
  const availableViews          = filteredViews;
  const tableCount              = Object.keys(catalog).length;
  const featureLayersInCanvas   = useMemo(() => new Set(canvasInfo.layerUrls ? canvasInfo.layerUrls.split('\u0000') : []), [canvasInfo.layerUrls]);

  // Guard branches (isValidProjectId redirect, loading, loadError, !project)
  // gestiti da EditorShell via le props guards. Il page conserva
  // solo l'orchestration degli hook e il JSX dei singoli slot.

  // beforeCanvas slot: banner accessibilita' nodi cross-tenant.
  const accessDeniedBanner = <AccessDeniedBanner />;

  // topbar slot
  // Barra unica (Fase 4, M7): connessione accanto al titolo, azioni nel
  // menu "⋯", tabelle dalla palette ("＋ Passo"), un solo "▶ Esegui".
  // Vincoli del catalogo (Output e caricamenti) dal grafo, senza iscriversi
  // ai nodi (UI-2, hooks/useCanvasDerived).
  const catalogNodes = useCanvasDerived(catalogConstraintNodes);
  const toolbar = {
    mode: 'estrai',
    title: project?.metadata?.name,
    onBack: handleBack,
    onSave: handleSaveCanvas,
    saving,
    ...executionToolbarProps(execution),
    theme,
    setTheme: toggleTheme,
    statusExtra: <span className="etb__stats">{canvasInfo.nodeCount} nodi · {canvasInfo.edgeCount} collegamenti</span>,
    leftExtra: projectConnection ? (
      <span className="etb__conn" title={projectConnection.name}>
        <i aria-hidden="true" />{projectConnection.name}
        <small>{sourceType === 'featureService' ? 'ArcGIS' : (projectConnection.type || 'DB').toUpperCase()}</small>
      </span>
    ) : null,
    menuItems: [
      // Nome, icona e descrizione di ogni voce dal glossario (regola 10).
      menuEntry('connections', { onClick: () => navigate('/connections') }),
      ...(isReadOnly ? [] : [
        { key: 'div0', divider: true },
        // Voci dal catalogo unico (UI-3, P30), spente con il motivo (E3).
        ...catalogMenuItems('estrai', catalogNodes, addNode),
        { key: 'div2', divider: true },
        menuEntry('versions', { onClick: () => setShowVersions(true) }),
      ]),
    ],
  };

  // sidebar slot (nascosta per viewer: niente drag&drop di tabelle)
  const sidebar = !isReadOnly ? (
        <div className="estrai-sidebar">

          {/* Search */}
          <div className="estrai-sidebar__search-wrap">
            <SearchField search={tableSearch} label="Cerca tabelle" shown={filteredTables.length} total={tableCount} />
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
                    {/* Fase 5 T2 (R4.8, R4.18): il catalogo arriva dopo
                        l'apertura e un errore e' un errore con «Riprova». */}
                    {catalogLoading ? (
                      <div className="estrai-sidebar__empty" role="status">Caricamento tabelle…</div>
                    ) : catalogError ? (
                      <ResourceError compact title="Tabelle non caricate" message={catalogError} onRetry={reloadCatalog} />
                    ) : filteredTables.length === 0 && tableSearch.active
                      ? <SearchEmpty search={tableSearch} shown={0} />
                      : availableTables.length === 0
                      ? <div className="estrai-sidebar__empty">
                          Nessuna tabella
                        </div>
                      : <>
                          {availableTables.slice(0, visibleTableCount).map(t => (
                            <div
                              key={t}
                              className="estrai-sidebar__table-item"
                              title="Clic per aggiungere al centro · trascina per posizionare"
                              draggable
                              {...pressable(() => addTableAtCenter(t, false))}
                              onDragStart={e => {
                                e.dataTransfer.setData('tableName', t);
                                e.dataTransfer.effectAllowed = 'move';
                              }}
                            >
                              <span className="estrai-sidebar__table-name">📊 {t}</span>
                              {tablesInCanvas.has(t) && <span className="estrai-sidebar__in-canvas" title="Già nel canvas: aggiungendola di nuovo riceve un alias diverso (Join della tabella con se stessa)">nel canvas</span>}
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
                          <SearchField search={viewSearch} label="Cerca viste" shown={filteredViews.length} total={viewNames.length} />
                        </div>
                        <div className="estrai-sidebar__items">
                          {filteredViews.length === 0 && viewSearch.active
                            ? <SearchEmpty search={viewSearch} shown={0} />
                            : availableViews.length === 0
                            ? <div className="estrai-sidebar__empty">Nessuna vista</div>
                            : <>
                                {availableViews.slice(0, visibleViewCount).map(v => (
                                  <div
                                    key={`view_${v}`}
                                    className="estrai-sidebar__table-item estrai-sidebar__table-item--view"
                                    title="Clic per aggiungere al centro · trascina per posizionare"
                                    draggable
                                    {...pressable(() => addTableAtCenter(v, true))}
                                    onDragStart={e => {
                                      e.dataTransfer.setData('tableName', v);
                                      e.dataTransfer.setData('isView', 'true');
                                      e.dataTransfer.effectAllowed = 'move';
                                    }}
                                  >
                                    <span className="estrai-sidebar__table-name">👁 {v}</span>
                                    {tablesInCanvas.has(v) && <span className="estrai-sidebar__in-canvas" title="Già nel canvas: aggiungendola di nuovo riceve un alias diverso">nel canvas</span>}
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
                canCreateConnection={can.createConnection(user)}
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
              onClick={signOut}
              title="Esci"
            >
              ⏻
            </button>
          </div>
        </div>
  ) : null;

  // afterCanvas slot: QueryPreview overlay sul canvas.
  const afterCanvas = (
    <QueryPreview />
  );

  // modals slot: lazy-loaded modal stack.
  // P5 (Fase 5 T7): colonne arricchite UNA volta per risposta, non a ogni
  // render (un oggetto nuovo azzerava pagina, ordinamento, ricerca e scheda).
  // Le colonne del canvas si leggono quando arriva la risposta.
  const previewData = useMemo(() => {
          // Arricchisce le colonne della preview con source_table dal resultNode,
          // così PreviewModal può raggruppare per vera sorgente invece di indovinare dal prefisso.
          if (!preview.data?.columns) return preview.data;
          const { nodes } = useCanvasStore.getState();
          const resultNode = nodes.find(n => n.type === 'resultNode');
          // Mappa name → tabella o layer: l'ingresso dell'Output nella fonte unica.
          const labels = new Map(nodes.map(n => [n.id, estraiSourceLabel(n)]));
          const sourceMap = {};
          if (resultNode) {
            columnsOf(columnSnapshot(), resultNode.id, { side: 'input' }).columns.forEach(col => {
              const label = labels.get(col.input_node);
              if (label && !(col.name in sourceMap)) sourceMap[col.name] = label;
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

  const modals = (
    <ModalErrorBoundary>
      <Suspense fallback={null}>
        {showVersions && (
          <CanvasVersionsModal
            isOpen onClose={() => setShowVersions(false)} projectId={projectId}
          />
        )}
        <ExecutionModal
          {...executionModalProps(execution)} projectId={projectId} projectName={project?.metadata?.name}
          title={`${term('run').icon} Esecuzione della query`} projectType="estrai"
        />
        {preview.showModal && (
        <PreviewModal
        isOpen={preview.showModal} onClose={preview.closeModal}
        onRefresh={limit => preview.handlePreview(limit)}
        data={previewData}
        loading={preview.loading} error={preview.error}
        permissionRevoked={preview.permissionRevoked}
        title={`${term('preview').name} della query SQL`} projectType="estrai"
      />
        )}
      </Suspense>
    </ModalErrorBoundary>
  );

  return (
    <ConfirmProvider>
      <EditorShell
        projectId={projectId}
        user={user}
        prefix="estrai"
        loading={loading}
        loadError={loadError}
        project={project}
        reload={reloadData}
        onBack={handleBack}
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
          edgeInspector,
          // Aspetto dei join dal loro stato (da completare, colonna non
          // trovata): applicato da EditorShell al cambio del contenuto.
          presentEdges: presentJoinEdges,
        }}
        canvasHandlers={{
          onNodesChange,
          onEdgesChange,
          // Clic sull'arco = selezione (pannello); tasto destro = elimina.
          onConnect: edgeHandlers.onConnect,
          onEdgeContextMenu: edgeHandlers.onEdgeContextMenu,
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
// Fase 5 T2 (R4.6): un progetto = un editor. Cambiare progetto nella
// stessa route rimonta tutto (dati, esecuzione, anteprime, polling).
function EstraiEditorWithProvider() {
  const { projectId } = useParams();
  return (
    <ReactFlowProvider key={projectId}>
      <EstraiEditor />
    </ReactFlowProvider>
  );
}

export default EstraiEditorWithProvider;
