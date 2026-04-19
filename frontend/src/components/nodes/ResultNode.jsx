/**
 * ResultNode.jsx — Nodo output unificato (ESTRAI, MANIPOLA, ARRICCHISCI)
 *
 * Nodo terminale della pipeline. Configura il dataset di output:
 *  - Selezione/esclusione colonne con alias
 *  - Formato output (CSV, GeoJSON, GeoPackage, Shapefile, XLSX)
 *  - GROUP BY e funzioni di aggregazione
 *  - Bottoni Preview ed Esegui
 *
 * Varianti: estrai | manipola | arricchisci (cambiano colori e label bottoni)
 */
import React, { useState, memo, useMemo, useEffect } from 'react';
import { Handle, Position, useReactFlow, useStore } from 'reactflow';
import ValidationModal from '../modals/ValidationModal';
import RunHistoryModal from '../modals/RunHistoryModal';
import ImdfPreviewModal from '../modals/ImdfPreviewModal';
import ImdfManifestModal from '../modals/ImdfManifestModal';
import ImdfValidationModal from '../modals/ImdfValidationModal';
import { validateManipolaCanvas } from '../../utils/validateManipolaCanvas';
import { validateEstraiCanvas } from '../../utils/validateEstraiCanvas';
import { validateArricchisciCanvas } from '../../utils/validateArricchisciCanvas';
import { getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isGeometryType } from '../../utils/columnUtils';
import useCanvasStore from '../../store/useCanvasStore';
import { getImdfBundleMetadata, listImdfBundles, deleteImdfBundle } from '../../api/imdf';
import BundleSlotCard from '../imdf/BundleSlotCard';
import ImdfBundlesManagerModal from '../modals/ImdfBundlesManagerModal';
import './ResultNode.css';

/**
 * Componente nodo risultato (output) UNIFICATO
 * Funziona sia per progetti SQL (estrai) che Pandas (manipola) che API (arricchisci)
 * 
 * Props:
 * - data.isManipulate: true per progetti Manipola
 * - data.isArricchisci: true per progetti Arricchisci
 * - data.fileName: nome file output
 * - data.format: formato (csv, json, xlsx)
 * - data.distinct: (solo SQL) usa DISTINCT
 * - data.limit: (solo SQL) limite righe
 * - data.columns: (solo SQL) colonne output calcolate (con type)
 * - data.availableColumns: (solo MANIPOLA) colonne disponibili
 * - data.group_by_enabled: (SQL e MANIPOLA) abilita GROUP BY
 * - data.group_by_columns: (SQL e MANIPOLA) colonne di raggruppamento
 * - data.aggregations: (SQL e MANIPOLA) funzioni aggregate
 * Tutti gli aggiornamenti avvengono tramite useCanvasStore (updateNodeData, deleteNode).
 * Non usare callback data.onXxx — pattern deprecato, rimosso.
 */
const ResultNode = memo(({ data, id }) => {
  const { getEdges, getNodes } = useReactFlow();

  // Azioni dallo store — referenze stabili, non causano re-render
  const updateNodeData  = useCanvasStore(s => s.updateNodeData);

  // Sottoscrizioni reattive per i bottoni Preview/Esegui
  const hasOnPreview = useCanvasStore(s => !!s.editorActionsRef?.current?.onPreview);
  const hasOnExecute = useCanvasStore(s => !!s.editorActionsRef?.current?.onExecute);
  
  // Conta edge in entrata
  const incomingEdgeCount = useStore((state) => {
    const edges = state.edges || [];
    return edges.filter(e => e.target === id).length;
  });
  
  // Sottoscrivi ai cambiamenti delle colonne dei nodi collegati
  const sourceColumnsSignature = useStore((state) => {
    const edges = state.edges || [];
    const nodes = state.nodes || [];
    const incomingEdges = edges.filter(e => e.target === id);
    
    return incomingEdges.map(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const cols = sourceNode?.data?.columns || [];
      return cols.map(c => typeof c === 'string' ? c : c.name).join(',');
    }).join('|');
  });
  
  // Calcola colonne upstream (per MANIPOLA/ARRICCHISCI)
  const upstreamColumns = useMemo(() => {
    const edges = getEdges();
    const nodes = getNodes();
    const incomingEdges = edges.filter(e => e.target === id);

    if (incomingEdges.length === 0) return [];

    const sourceEdge = incomingEdges[0];
    const sourceNode = nodes.find(n => n.id === sourceEdge.source);

    if (!sourceNode) return [];

    const cols = sourceNode.data?.columns || [];
    return cols.map(c => ({
      name: typeof c === 'string' ? c : (c.alias || c.name),
      type: typeof c === 'string' ? 'str' : (c.type || 'str')
    }));
  }, [id, getEdges, getNodes, incomingEdgeCount, sourceColumnsSignature]);

  // Detection: il nodo upstream e' un api_enrich con output_kind impostato?
  // Se sì, il result si specializza (es. modalita' IMDF).
  // Legge da useCanvasStore (single source of truth) cosi' i cambi fatti via
  // updateNodeData sull'upstream triggherano il re-render del Result.
  const upstreamOutputKind = useCanvasStore((state) => {
    const edges = state.edges || [];
    const nodes = state.nodes || [];
    const incomingEdges = edges.filter(e => e.target === id);
    if (incomingEdges.length === 0) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdges[0].source);
    if (!sourceNode || sourceNode.type !== 'api_enrich') return null;
    return sourceNode.data?.output_kind || null;
  });
  const isImdfMode = upstreamOutputKind === 'imdf';

  const [isEditingFileName, setIsEditingFileName] = useState(false);
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const [showValidation, setShowValidation] = useState(false);
  const [validationResult, setValidationResult] = useState({ isValid: true, issues: [] });
  const [tempFileName, setTempFileName] = useState(data.fileName || 'output');
  const [executionMode, setExecutionMode] = useState('cache');
  const [showGroupBySection, setShowGroupBySection] = useState(false);
  const [showOrderBySection, setShowOrderBySection] = useState(false);
  const [columnsExpanded, setColumnsExpanded] = useState(false);
  const COLUMNS_PREVIEW = 5;

  // Determina il tipo di progetto
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;
  const isEstrai = !isManipulate && !isArricchisci;
  
  // Manipola o Arricchisci hanno opzioni cache/ricalcola
  const hasExecutionOptions = isManipulate || isArricchisci;

  const fileName = data.fileName || 'output';
  const format = data.format || 'csv';
  const limit = data.limit || null;
  const distinct = data.distinct || false;
  const selectedGeomColumn = data.geomColumn || null;

  // GROUP BY state
  const groupByEnabled = data.group_by_enabled || false;
  const groupByColumns = data.group_by_columns || [];
  const aggregations = data.aggregations || [];

  // ORDER BY state
  const orderByEnabled = data.order_by_enabled || false;
  const orderByColumns = data.order_by_columns || [];

  // HAVING state
  const havingEnabled = data.having_enabled || false;
  const havingConditions = data.having_conditions || [];

  // ── IMDF mode: stato dei modal di anteprima ──────────────────────
  // openImdfModal: 'preview' | 'manifest' | 'validation' | null
  const [openImdfModal, setOpenImdfModal] = useState(null);

  // Dati IMDF dell'ultima esecuzione. Dopo lo slim del task result il
  // payload inline contiene solo {site_id, bundle_task_id, has_gdb, ...} —
  // manifest/validation/geojson/renderers stanno sul disco del server.
  // Il ResultNode in IMDF mode fa self-hydration via /api/imdf/bundles/{id}
  // quando rileva un bundle_task_id ma manifest non ancora caricato.
  const lastTaskResult = useCanvasStore(s => s.lastTaskResult);
  const setLastTaskResult = useCanvasStore(s => s.setLastTaskResult);
  const imdfData = lastTaskResult?.imdf || null;


  // ── Bundle browser UI state (solo IMDF mode) ────────────────────
  const [bundles, setBundles]                     = useState([]);
  const [loadingBundles, setLoadingBundles]       = useState(false);
  const [selectedBundleId, setSelectedBundleId]   = useState(null); // override storico
  const [bundlesManagerOpen, setBundlesManagerOpen] = useState(false);
  const [bundleSwitcherOpen, setBundleSwitcherOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);

  // bundleTaskId effettivo: live (da lastTaskResult) oppure override esplicito utente
  const liveBundleTaskId = imdfData?.bundle_task_id || null;
  const bundleTaskId = selectedBundleId || liveBundleTaskId || null;
  const selectedBundle = bundles.find(b => b.task_id === bundleTaskId) || null;
  const isBundleFallback = !liveBundleTaskId && !!selectedBundleId;

  // Carica lista bundle del progetto al mount (solo in IMDF mode).
  // Se non c'e' un live task result, preseleziona automaticamente il
  // bundle piu' recente cosi' che l'utente veda subito qualcosa al
  // caricamento della pagina (stesso comportamento del vecchio Publisher).
  useEffect(() => {
    if (!isImdfMode) return;
    let cancelled = false;
    (async () => {
      setLoadingBundles(true);
      try {
        const list = await listImdfBundles({ project_id: projectId });
        if (cancelled) return;
        const arr = Array.isArray(list) ? list : [];
        setBundles(arr);
        // Auto-select del piu' recente se non c'e' nulla di live ne'
        // una scelta esplicita precedente. Uso getState() per leggere
        // fuori closure ed evitare stale reads.
        const state = useCanvasStore.getState();
        const hasLive = !!state.lastTaskResult?.imdf?.bundle_task_id;
        if (!hasLive && arr.length > 0) {
          setSelectedBundleId(prev => prev || arr[0].task_id);
        }
      } catch (e) {
        console.warn('listImdfBundles:', e);
      } finally {
        if (!cancelled) setLoadingBundles(false);
      }
    })();
    return () => { cancelled = true; };
  }, [isImdfMode, projectId]);

  // Quando arriva un nuovo live task post-Esegui, resetta l'override e
  // ri-fetcha la lista (così il nuovo bundle appare nel select).
  useEffect(() => {
    if (!isImdfMode || !liveBundleTaskId) return;
    if (liveBundleTaskId === selectedBundleId) return;
    setSelectedBundleId(null);
    let cancelled = false;
    (async () => {
      try {
        const list = await listImdfBundles({ project_id: projectId });
        if (!cancelled) setBundles(Array.isArray(list) ? list : []);
      } catch (e) {
        console.warn('listImdfBundles refresh:', e);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isImdfMode, liveBundleTaskId, projectId]);

  // Hydration: quando il bundleTaskId corrente non è gia' riflesso nello
  // store con manifest presente, fetcha il contenuto completo. Copre sia il
  // caso live (slim payload dal task) sia il caso historical (utente cambia
  // bundle nella card). Niente cache per-id: il controllo sullo store è
  // sufficiente a evitare re-fetch ridondanti, e cachare separatamente
  // causava "torno al bundle X e vedo ancora Y" dopo un ciclo di switch.
  useEffect(() => {
    if (!isImdfMode) return;
    const bid = bundleTaskId;
    if (!bid) return;
    // Se lo store ha già manifest per QUESTO id, non re-fetchare
    if (imdfData?.bundle_task_id === bid && imdfData?.manifest) return;

    let cancelled = false;
    (async () => {
      try {
        const meta = await getImdfBundleMetadata(bid);
        if (cancelled) return;
        const prev = useCanvasStore.getState().lastTaskResult || {};
        setLastTaskResult({
          ...prev,
          imdf: {
            ...(prev.imdf || {}),
            ...meta,
            bundle_task_id: bid,
          },
        });
      } catch (e) {
        console.warn('ResultNode hydrate IMDF:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [isImdfMode, bundleTaskId, imdfData?.bundle_task_id, imdfData?.manifest, setLastTaskResult]);

  // Cancella un bundle dal server + aggiorna la lista locale
  const handleDeleteBundle = async (tid) => {
    try {
      await deleteImdfBundle(tid);
      setBundles(prev => prev.filter(b => b.task_id !== tid));
      if (selectedBundleId === tid) setSelectedBundleId(null);
    } catch (e) {
      console.error('deleteImdfBundle:', e);
      alert(`Errore cancellazione bundle: ${e?.response?.data?.detail || e.message}`);
    }
  };

  // Verifica se il formato selezionato è geospaziale
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Estrai colonne geometriche disponibili
  // Per MANIPOLA e ARRICCHISCI: usa upstreamColumns (calcolate da nodi collegati) o fallback a data.availableColumns
  // Per ESTRAI: usa data.columns + controlla upstream per feature_source nodes
  const geomSourceColumns = (() => {
    if (isManipulate || isArricchisci) {
      return upstreamColumns.length > 0 ? upstreamColumns : (data.availableColumns || []);
    }
    // ESTRAI: usa data.columns, ma integra anche upstreamColumns per feature_source
    const cols = data.columns || [];
    if (upstreamColumns.length > 0) {
      // Merge: aggiungi colonne upstream non presenti (es. geometry_wkt da featureLayerNode)
      const existingNames = new Set(cols.map(c => c.name || c.alias));
      const extraCols = upstreamColumns.filter(c => !existingNames.has(c.name));
      return [...cols, ...extraCols];
    }
    return cols;
  })();
  
  // Costruisci lista colonne geometriche con nome completo (tabella.colonna)
  const availableGeomColumns = geomSourceColumns
    .filter(col => isGeometryType(col.type))
    .map(col => {
      const displayName = col.alias || col.name || col;
      // Per ESTRAI usa source_table, per MANIPOLA/ARRICCHISCI potrebbe non esserci
      return col.source_table ? `${col.source_table}.${displayName}` : displayName;
    });
  
  // Flag: mostra formati geo solo se ci sono colonne geometriche
  const hasGeometryColumns = availableGeomColumns.length > 0;

  // Auto-reset formato: se il formato è geo ma non ci sono più colonne geometry → torna a CSV
  useEffect(() => {
    if (!hasGeometryColumns && isGeoFormat) {
      updateNodeData(id, { format: 'csv' });
    }
  }, [hasGeometryColumns, isGeoFormat, id]);


  // Estrai tutte le colonne disponibili per GROUP BY (da data.columns per ESTRAI) - DEDUPLICATO
  const availableColumnsRaw = (data.columns || []).map(col => ({
    name: col.alias || col.name,
    type: col.type || 'str',
    fullName: col.full_name || `${col.source_table}.${col.alias || col.name}`
  }));
  // Rimuovi duplicati per nome
  const availableColumns = [...new Map(availableColumnsRaw.map(col => [col.name, col])).values()];

  // Colonne disponibili per MANIPOLA (da upstreamColumns o fallback a data.availableColumns) - DEDUPLICATO
  const manipolaSourceCols = upstreamColumns.length > 0 ? upstreamColumns : (data.availableColumns || []);
  const manipolaColumnsRaw = manipolaSourceCols.map(col => ({
    name: col.name || col,
    type: col.type || 'str',
    fullName: col.name || col
  }));
  // Rimuovi duplicati per nome
  const manipolaColumns = [...new Map(manipolaColumnsRaw.map(col => [col.name, col])).values()];

  // Colonne da usare per GROUP BY (ESTRAI usa availableColumns, MANIPOLA usa manipolaColumns)
  const groupByAvailableColumns = isManipulate ? manipolaColumns : availableColumns;

  // Colonne numeriche per aggregazioni
  const numericColumns = groupByAvailableColumns.filter(col => 
    col.type === 'int' || col.type === 'real'
  );

  // Determina variante CSS
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');

  // Configurazione per tipo
  const config = {
    estrai: { icon: '📁', title: 'Output', executeLabel: 'Esegui Query' },
    manipola: { icon: '🔧', title: 'Output Manipola', executeLabel: 'Esegui Pipeline' },
    arricchisci: { icon: '🔗', title: 'Output Arricchisci', executeLabel: 'Esegui Arricchimento' }
  };

  const { icon, title, executeLabel } = config[nodeVariant];

  const formats = [
    { value: 'csv', label: 'CSV', icon: '📊', category: 'standard' },
    { value: 'json', label: 'JSON', icon: '📄', category: 'standard' },
    { value: 'xlsx', label: 'XLSX', icon: '📗', category: 'standard' },
    { value: 'geojson', label: 'GeoJSON', icon: '🌐', category: 'geo' },
    { value: 'gpkg', label: 'GeoPackage', icon: '📦', category: 'geo' },
    { value: 'shp', label: 'Shapefile', icon: '🗺️', category: 'geo' }
  ];

  const aggregateFunctions = [
    { value: 'COUNT', label: 'COUNT', desc: 'Conta' },
    { value: 'SUM', label: 'SUM', desc: 'Somma' },
    { value: 'AVG', label: 'AVG', desc: 'Media' },
    { value: 'MIN', label: 'MIN', desc: 'Minimo' },
    { value: 'MAX', label: 'MAX', desc: 'Massimo' }
  ];

  const standardFormats = formats.filter(f => f.category === 'standard');
  const geoFormats = formats.filter(f => f.category === 'geo');

  // Handlers
  const handleSaveFileName = () => {
    updateNodeData(id, { fileName: tempFileName.trim() || 'output' });
    setIsEditingFileName(false);
  };

  const handleChangeFormat = (newFormat) => {
    updateNodeData(id, { format: newFormat });
  };

  const handleChangeGeomColumn = (columnName) => {
    updateNodeData(id, { geomColumn: columnName });
  };

  const handleToggleDistinct = () => {
    updateNodeData(id, { distinct: !distinct });
  };

  const handleUpdateLimit = (newLimit) => {
    const limitValue = newLimit === '' ? null : parseInt(newLimit) || null;
    updateNodeData(id, { limit: limitValue });
  };

  // GROUP BY Handlers
  const handleToggleGroupBy = () => {
    updateNodeData(id, {
      group_by_enabled: !groupByEnabled,
      group_by_columns: groupByEnabled ? [] : groupByColumns,
      aggregations: groupByEnabled ? [] : aggregations
    });
  };

  const handleAddGroupByColumn = (columnName) => {
    if (!columnName || groupByColumns.includes(columnName)) return;
    updateNodeData(id, {
      group_by_enabled: true,
      group_by_columns: [...groupByColumns, columnName],
      aggregations
    });
  };

  const handleRemoveGroupByColumn = (columnName) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns.filter(c => c !== columnName),
      aggregations
    });
  };

  const handleAddAggregation = () => {
    {
      const newAgg = {
        column: '*',
        function: 'COUNT',
        alias: `count_${aggregations.length + 1}`
      };
      updateNodeData(id, {
        group_by_enabled: true,
        group_by_columns: groupByColumns,
        aggregations: [...aggregations, newAgg]
      });
    }
  };

  const handleUpdateAggregation = (index, field, value) => {
    {
      const newAggregations = [...aggregations];
      newAggregations[index] = { ...newAggregations[index], [field]: value };
      
      // Auto-genera alias se cambiano colonna o funzione
      if (field === 'column' || field === 'function') {
        const agg = newAggregations[index];
        const colName = agg.column === '*' ? 'all' : agg.column;
        newAggregations[index].alias = `${agg.function.toLowerCase()}_${colName}`;
      }
      
      updateNodeData(id, {
        group_by_enabled: groupByEnabled,
        group_by_columns: groupByColumns,
        aggregations: newAggregations
      });
    }
  };

  const handleRemoveAggregation = (index) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns,
      aggregations: aggregations.filter((_, i) => i !== index)
    });
  };

  // ORDER BY Handlers
  const handleToggleOrderBy = () => {
    updateNodeData(id, {
      order_by_enabled: !orderByEnabled,
      order_by_columns: orderByEnabled ? [] : orderByColumns
    });
  };

  const handleAddOrderByColumn = (columnName) => {
    if (!columnName || orderByColumns.some(c => c.column === columnName)) return;
    updateNodeData(id, {
      order_by_enabled: true,
      order_by_columns: [...orderByColumns, { column: columnName, direction: 'ASC' }]
    });
  };

  const handleRemoveOrderByColumn = (index) => {
    updateNodeData(id, {
      order_by_columns: orderByColumns.filter((_, i) => i !== index)
    });
  };

  const handleToggleOrderByDirection = (index) => {
    const newColumns = [...orderByColumns];
    newColumns[index] = {
      ...newColumns[index],
      direction: newColumns[index].direction === 'ASC' ? 'DESC' : 'ASC'
    };
    updateNodeData(id, { order_by_columns: newColumns });
  };

  // HAVING Handlers
  const handleToggleHaving = () => {
    updateNodeData(id, {
      having_enabled: !havingEnabled,
      having_conditions: havingEnabled ? [] : havingConditions
    });
  };

  const handleAddHavingCondition = () => {
    updateNodeData(id, {
      having_enabled: true,
      having_conditions: [...havingConditions, {
        function: 'COUNT', column: '*', operator: '>', value: '0'
      }]
    });
  };

  const handleUpdateHavingCondition = (index, field, value) => {
    const newConditions = [...havingConditions];
    newConditions[index] = { ...newConditions[index], [field]: value };
    updateNodeData(id, { having_conditions: newConditions });
  };

  const handleRemoveHavingCondition = (index) => {
    updateNodeData(id, {
      having_conditions: havingConditions.filter((_, i) => i !== index)
    });
  };

  const handleDelete = () => {
    if (confirm('Eliminare il nodo risultato?')) {
      useCanvasStore.getState().deleteNode(id);
    }
  };

  const runValidation = (availableConnectionIds = null) => {
    const state = useCanvasStore.getState();
    const allNodes = state.nodes || [];
    const allEdges = state.edges || [];
    if (isManipulate) {
      const registry = state.transformsRegistry || null;
      return validateManipolaCanvas(allNodes, allEdges, registry);
    }
    if (isArricchisci) {
      return validateArricchisciCanvas(allNodes, allEdges, availableConnectionIds);
    }
    return validateEstraiCanvas(allNodes, allEdges);
  };

  const handleValidate = async () => {
    let availableConnectionIds = null;
    if (isArricchisci) {
      try {
        const { listApiConnections } = await import('../../api/apiConnections');
        const res = await listApiConnections();
        availableConnectionIds = new Set((res.api_connections || res.connections || []).map(c => c.id));
      } catch (e) {
        console.warn('Impossibile caricare connessioni per validazione:', e);
      }
    }
    const result = runValidation(availableConnectionIds);
    setValidationResult(result);
    setShowValidation(true);
  };

  const handleExecuteWithValidation = async () => {
    let availableConnectionIds = null;
    if (isArricchisci) {
      try {
        const { listApiConnections } = await import('../../api/apiConnections');
        const res = await listApiConnections();
        availableConnectionIds = new Set((res.api_connections || res.connections || []).map(c => c.id));
      } catch (e) { /* fallback senza check */ }
    }
    const result = runValidation(availableConnectionIds);
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    const onExecute = useCanvasStore.getState().editorActionsRef.current.onExecute;
    if (onExecute) onExecute(id, { force_recalculate: executionMode === 'recalculate' });
  };

  const handlePreviewWithValidation = () => {
    const result = runValidation();
    if (!result.isValid) {
      setValidationResult(result);
      setShowValidation(true);
      return;
    }
    useCanvasStore.getState().editorActionsRef.current.onPreview?.(id);
  };


  return (
    <div className={`result-node result-node--${nodeVariant}`}>
      {/* Handle input (da sinistra) */}
      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="result-node__handle result-node__handle--input"
      />

      {/* Handle output (a destra) - per connessione a CARICA */}
      <Handle
        type="source"
        position={Position.Right}
        id="output"
        className="result-node__handle result-node__handle--output"
      />

      {/* Header */}
      <div className="result-node__header">
        <div className="result-node__title">
          <span className="result-node__title-icon">{icon}</span>
          <span>{title}</span>
        </div>
        <button type="button"
          onClick={handleDelete}
          className="result-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="result-node__body">
        {/* ── MODALITA' IMDF ────────────────────────────────────────────
            Si attiva quando il nodo upstream (api_enrich con rest_file) ha
            output_kind='imdf'. Sostituisce le sezioni standard del result
            (cronologia/esecuzione/preview/colonne) con 3 bottoni che aprono
            modal di anteprima sul contenuto del file IMDF prodotto. */}
        {isImdfMode ? (
          <div className="result-node__section result-node__section--imdf"
               style={{ background: 'rgba(124,58,237,0.08)', borderLeft: '3px solid #7C3AED', padding: '14px 14px', borderRadius: 6 }}>
            <div className="result-node__label" style={{ fontWeight: 700, color: '#7C3AED', marginBottom: 10 }}>
              📦 Bundle IMDF
              {!imdfData && (
                <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.6, fontWeight: 400 }}>
                  (esegui per generare i dati)
                </span>
              )}
            </div>

            {/* Card bundle attivo con switcher e link manager */}
            <BundleSlotCard
              loading={loadingBundles}
              bundle={selectedBundle}
              bundleTaskId={bundleTaskId}
              isFallback={isBundleFallback}
              bundles={bundles}
              switcherOpen={bundleSwitcherOpen}
              onToggleSwitcher={(e) => { e?.stopPropagation?.(); setBundleSwitcherOpen(v => !v); }}
              onSelectBundle={(tid) => {
                setSelectedBundleId(tid);
                setBundleSwitcherOpen(false);
              }}
              onOpenManager={(e) => {
                e?.stopPropagation?.();
                setBundlesManagerOpen(true);
                setBundleSwitcherOpen(false);
              }}
            />

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button
                type="button"
                disabled={!imdfData}
                onClick={(e) => { e.stopPropagation(); setOpenImdfModal('preview'); }}
                onMouseDown={(e) => e.stopPropagation()}
                className="result-node__filename-input"
                style={{ width: '100%', cursor: imdfData ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData ? 1 : 0.5 }}
              >
                🗺️ Visualizza IMDF
              </button>
              <button
                type="button"
                disabled={!imdfData?.manifest}
                onClick={(e) => { e.stopPropagation(); setOpenImdfModal('manifest'); }}
                onMouseDown={(e) => e.stopPropagation()}
                className="result-node__filename-input"
                style={{ width: '100%', cursor: imdfData?.manifest ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData?.manifest ? 1 : 0.5 }}
              >
                📋 Visualizza Manifest
              </button>
              <button
                type="button"
                disabled={!imdfData?.validation}
                onClick={(e) => { e.stopPropagation(); setOpenImdfModal('validation'); }}
                onMouseDown={(e) => e.stopPropagation()}
                className="result-node__filename-input"
                style={{ width: '100%', cursor: imdfData?.validation ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData?.validation ? 1 : 0.5 }}
              >
                ⚠️ Visualizza Validation
              </button>

              {/* Bottone Esegui — anche in modalita' IMDF, perche' senza il
                  Run dell'utente i modali sono vuoti. */}
              {hasOnExecute && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); handleExecuteWithValidation(); }}
                  onMouseDown={(e) => e.stopPropagation()}
                  className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
                  style={{ marginTop: 6, width: '100%' }}
                >
                  <span className="result-node__execute-icon">▶️</span>
                  <span>{executeLabel}</span>
                </button>
              )}
            </div>
          </div>
        ) : (
        <>
        {/* Nome file */}
        <div className="result-node__section">
          <div className="result-node__label">Nome file:</div>
          {isEditingFileName ? (
            <input
              type="text"
              value={tempFileName}
              onChange={(e) => setTempFileName(e.target.value)}
              onBlur={handleSaveFileName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveFileName();
                if (e.key === 'Escape') {
                  setIsEditingFileName(false);
                  setTempFileName(fileName);
                }
              }}
              autoFocus
              placeholder="nome file..."
              className="result-node__filename-input"
            />
          ) : (
            <div
              onClick={() => {
                setIsEditingFileName(true);
                setTempFileName(fileName);
              }}
              className="result-node__filename-display"
              title="Click per modificare"
            >
              {fileName}.{format}
            </div>
          )}
        </div>

        {/* Formato */}
        <div className="result-node__section">
          <div className="result-node__label">Formato:</div>
          {/* Formati standard */}
          <div className="result-node__format-group">
            {standardFormats.map(fmt => (
              <button type="button"
                key={fmt.value}
                onClick={() => handleChangeFormat(fmt.value)}
                className={`result-node__format-btn result-node__format-btn--${fmt.value} ${
                  format === fmt.value ? 'result-node__format-btn--active' : ''
                }`}
              >
                <span className="result-node__format-icon">{fmt.icon}</span>
                <span>{fmt.label}</span>
              </button>
            ))}
          </div>
          {/* Formati geospaziali - mostrati solo se ci sono colonne geometry */}
          {hasGeometryColumns && (
            <>
              <div className="result-node__label result-node__label--geo">🌐 Formati geospaziali:</div>
              <div className="result-node__format-group">
                {geoFormats.map(fmt => (
                  <button type="button"
                    key={fmt.value}
                    onClick={() => handleChangeFormat(fmt.value)}
                    className={`result-node__format-btn result-node__format-btn--${fmt.value} result-node__format-btn--geo ${
                      format === fmt.value ? 'result-node__format-btn--active' : ''
                    }`}
                  >
                    <span className="result-node__format-icon">{fmt.icon}</span>
                    <span>{fmt.label}</span>
                  </button>
                ))}
              </div>
              
              {/* Selezione colonna geometria */}
              {isGeoFormat && availableGeomColumns.length > 0 && (
                <div className="result-node__geom-select">
                  <div className="result-node__label">📍 Colonna geometria:</div>
                  <select
                    value={selectedGeomColumn || availableGeomColumns[0] || ''}
                    onChange={(e) => handleChangeGeomColumn(e.target.value)}
                    className="result-node__geom-dropdown"
                  >
                    {availableGeomColumns.map(col => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </select>
                </div>
              )}
              
              {isGeoFormat && availableGeomColumns.length === 0 && (
                <div className="result-node__geom-warning">
                  ⚠️ Nessuna colonna geometrica trovata
                </div>
              )}
            </>
          )}
        </div>

        {/* Colonne Output (solo SQL/Estrai) */}
        {isEstrai && data.columns && data.columns.length > 0 && !groupByEnabled && (
          <div className="result-node__section result-node__section--bordered">
            <div className="result-node__label result-node__label--with-badge"
              style={{ cursor: 'pointer' }}
              onClick={() => setColumnsExpanded(v => !v)}
            >
              <span>📋 Colonne output:</span>
              <span className="result-node__columns-badge">{data.columns.length}</span>
              <span style={{ marginLeft: 'auto', fontSize: '9px', color: 'var(--plen-text-dim)' }}>
                {columnsExpanded ? '▲' : '▼'}
              </span>
            </div>
            <div className="result-node__columns-list">
              {(columnsExpanded ? data.columns : data.columns.slice(0, COLUMNS_PREVIEW)).map((col, idx) => (
                <div key={idx} className="result-node__column-item">
                  <span
                    className={`result-node__column-type-icon ${isGeometryType(col.type) ? 'result-node__column-type-icon--geo' : ''}`}
                    title={getColumnTypeLabel(col.type)}
                  >
                    {getColumnTypeIcon(col.type)}
                  </span>
                  <span className="result-node__column-table">{col.source_table}</span>
                  <span className="result-node__column-separator">.</span>
                  <span className="result-node__column-name">{col.alias || col.name}</span>
                  {isGeometryType(col.type) && getColumnSRID(col.type) && (
                    <span className="result-node__column-srid-badge" title={`EPSG:${getColumnSRID(col.type)}`}>
                      {getColumnSRID(col.type)}
                    </span>
                  )}
                </div>
              ))}
            </div>
            {data.columns.length > COLUMNS_PREVIEW && (
              <button
                type="button"
                className="result-node__columns-expand-btn"
                onClick={() => setColumnsExpanded(v => !v)}
              >
                {columnsExpanded
                  ? '▲ Comprimi'
                  : `▼ Mostra tutte (${data.columns.length - COLUMNS_PREVIEW} nascoste)`}
              </button>
            )}
          </div>
        )}

        {/* Opzioni Query SQL (DISTINCT + LIMIT) - solo Estrai */}
        {isEstrai && (
          <div className="result-node__section result-node__section--bordered">
            <div className="result-node__label">⚙️ Opzioni query:</div>

            {/* DISTINCT - disabilitato se GROUP BY attivo */}
            <label className={`result-node__checkbox-label ${groupByEnabled ? 'result-node__checkbox-label--disabled' : ''}`}>
              <input
                type="checkbox"
                checked={distinct}
                onChange={handleToggleDistinct}
                disabled={groupByEnabled}
                className="result-node__checkbox"
              />
              <span>DISTINCT (rimuovi duplicati)</span>
            </label>

            {/* LIMIT */}
            <div>
              <label className="result-node__limit-label">
                Limita risultati:
              </label>
              <input
                type="number"
                value={limit || ''}
                onChange={(e) => handleUpdateLimit(e.target.value)}
                placeholder="Nessun limite"
                min="1"
                className="result-node__limit-input"
              />
            </div>
          </div>
        )}

        {/* ✅ GROUP BY Section - ESTRAI e MANIPOLA */}
        {(isEstrai || isManipulate) && (availableColumns.length > 0 || manipolaColumns.length > 0) && (
          <div className="result-node__section result-node__section--bordered">
            <div 
              className="result-node__groupby-header"
              onClick={() => setShowGroupBySection(!showGroupBySection)}
            >
              <div className="result-node__label result-node__label--clickable">
                <span>📊 GROUP BY (Raggruppamento)</span>
                {groupByEnabled && (
                  <span className="result-node__groupby-badge">Attivo</span>
                )}
              </div>
              <span className="result-node__groupby-toggle">
                {showGroupBySection ? '▼' : '▶'}
              </span>
            </div>

            {showGroupBySection && (
              <div className="result-node__groupby-content">
                {/* Toggle abilitazione */}
                <label className="result-node__checkbox-label">
                  <input
                    type="checkbox"
                    checked={groupByEnabled}
                    onChange={handleToggleGroupBy}
                    className="result-node__checkbox"
                  />
                  <span>Abilita raggruppamento</span>
                </label>

                {groupByEnabled && (
                  <>
                    {/* Colonne di raggruppamento */}
                    <div className="result-node__groupby-section">
                      <div className="result-node__label">Raggruppa per:</div>
                      
                      {/* Lista colonne selezionate */}
                      {groupByColumns.length > 0 && (
                        <div className="result-node__groupby-selected">
                          {groupByColumns.map((col, idx) => (
                            <span key={idx} className="result-node__groupby-tag">
                              {col}
                              <button type="button" 
                                onClick={() => handleRemoveGroupByColumn(col)}
                                className="result-node__groupby-tag-remove"
                              >
                                ×
                              </button>
                            </span>
                          ))}
                        </div>
                      )}
                      
                      {/* Dropdown per aggiungere colonna */}
                      <select
                        value=""
                        onChange={(e) => handleAddGroupByColumn(e.target.value)}
                        className="result-node__groupby-dropdown"
                      >
                        <option value="">+ Aggiungi colonna...</option>
                        {groupByAvailableColumns
                          .filter(col => !groupByColumns.includes(col.name))
                          .map((col, idx) => (
                            <option key={idx} value={col.name}>
                              {getColumnTypeIcon(col.type)} {col.name}
                            </option>
                          ))}
                      </select>
                    </div>

                    {/* Aggregazioni */}
                    <div className="result-node__groupby-section">
                      <div className="result-node__label">Aggregazioni:</div>
                      
                      {/* Lista aggregazioni */}
                      {aggregations.map((agg, idx) => (
                        <div key={idx} className="result-node__aggregation-row">
                          {/* Funzione */}
                          <select
                            value={agg.function}
                            onChange={(e) => handleUpdateAggregation(idx, 'function', e.target.value)}
                            className="result-node__agg-select result-node__agg-select--func"
                          >
                            {aggregateFunctions.map(f => (
                              <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                          </select>
                          
                          {/* Colonna */}
                          <select
                            value={agg.column}
                            onChange={(e) => handleUpdateAggregation(idx, 'column', e.target.value)}
                            className="result-node__agg-select result-node__agg-select--col"
                          >
                            <option value="*">* (tutte)</option>
                            {groupByAvailableColumns.map((col, colIdx) => (
                              <option key={colIdx} value={col.name}>{col.name}</option>
                            ))}
                          </select>
                          
                          {/* Alias */}
                          <input
                            type="text"
                            value={agg.alias || ''}
                            onChange={(e) => handleUpdateAggregation(idx, 'alias', e.target.value)}
                            placeholder="alias"
                            className="result-node__agg-alias"
                          />
                          
                          {/* Rimuovi */}
                          <button type="button"
                            onClick={() => handleRemoveAggregation(idx)}
                            className="result-node__agg-remove"
                            title="Rimuovi aggregazione"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                      
                      {/* Bottone aggiungi aggregazione */}
                      <button type="button"
                        onClick={handleAddAggregation}
                        className="result-node__add-agg-btn"
                      >
                        + Aggiungi aggregazione
                      </button>
                    </div>

                    {/* Preview colonne output con GROUP BY */}
                    {(groupByColumns.length > 0 || aggregations.length > 0) && (
                      <div className="result-node__groupby-preview">
                        <div className="result-node__label">📋 Output previsto:</div>
                        <div className="result-node__groupby-preview-list">
                          {groupByColumns.map((col, idx) => (
                            <span key={`g-${idx}`} className="result-node__preview-col result-node__preview-col--group">
                              {col}
                            </span>
                          ))}
                          {aggregations.map((agg, idx) => (
                            <span key={`a-${idx}`} className="result-node__preview-col result-node__preview-col--agg">
                              {agg.alias || `${agg.function}(${agg.column})`}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* ✅ ORDER BY Section - ESTRAI e MANIPOLA */}
        {(isEstrai || isManipulate) && (availableColumns.length > 0 || manipolaColumns.length > 0) && (
          <div className="result-node__section result-node__section--bordered">
            <div
              className="result-node__groupby-header"
              onClick={() => setShowOrderBySection(!showOrderBySection)}
            >
              <div className="result-node__label result-node__label--clickable">
                <span>↕️ ORDER BY (Ordinamento)</span>
                {orderByEnabled && orderByColumns.length > 0 && (
                  <span className="result-node__groupby-badge">Attivo</span>
                )}
              </div>
              <span className="result-node__groupby-toggle">
                {showOrderBySection ? '▼' : '▶'}
              </span>
            </div>

            {showOrderBySection && (
              <div className="result-node__groupby-content">
                {/* Toggle abilitazione */}
                <label className="result-node__checkbox-label">
                  <input
                    type="checkbox"
                    checked={orderByEnabled}
                    onChange={handleToggleOrderBy}
                    className="result-node__checkbox"
                  />
                  <span>Abilita ordinamento</span>
                </label>

                {orderByEnabled && (
                  <>
                    {/* Colonne ordinate */}
                    {orderByColumns.length > 0 && (
                      <div className="result-node__groupby-selected">
                        {orderByColumns.map((item, idx) => (
                          <span key={idx} className="result-node__groupby-tag">
                            {item.column}
                            <button type="button"
                              onClick={() => handleToggleOrderByDirection(idx)}
                              className="result-node__orderby-dir-btn"
                              title={`Cambia direzione (${item.direction})`}
                            >
                              {item.direction === 'ASC' ? '↑' : '↓'}
                            </button>
                            <button type="button"
                              onClick={() => handleRemoveOrderByColumn(idx)}
                              className="result-node__groupby-tag-remove"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Dropdown per aggiungere colonna */}
                    <select
                      value=""
                      onChange={(e) => handleAddOrderByColumn(e.target.value)}
                      className="result-node__groupby-dropdown"
                    >
                      <option value="">+ Aggiungi colonna...</option>
                      {groupByAvailableColumns
                        .filter(col => !orderByColumns.some(c => c.column === col.name))
                        .map((col, idx) => (
                          <option key={idx} value={col.name}>
                            {getColumnTypeIcon(col.type)} {col.name}
                          </option>
                        ))}
                    </select>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* ✅ HAVING Section - Solo quando GROUP BY è attivo */}
        {groupByEnabled && aggregations.length > 0 && (
          <div className="result-node__section result-node__section--bordered">
            <div className="result-node__groupby-content">
              <label className="result-node__checkbox-label">
                <input
                  type="checkbox"
                  checked={havingEnabled}
                  onChange={handleToggleHaving}
                  className="result-node__checkbox"
                />
                <span>🔍 HAVING (Filtro su aggregati)</span>
              </label>

              {havingEnabled && (
                <div className="result-node__groupby-section">
                  {/* Lista condizioni HAVING */}
                  {havingConditions.map((cond, idx) => (
                    <div key={idx} className="result-node__aggregation-row">
                      {/* Funzione */}
                      <select
                        value={cond.function}
                        onChange={(e) => handleUpdateHavingCondition(idx, 'function', e.target.value)}
                        className="result-node__agg-select result-node__agg-select--func"
                      >
                        {aggregateFunctions.map(f => (
                          <option key={f.value} value={f.value}>{f.label}</option>
                        ))}
                      </select>

                      {/* Colonna */}
                      <select
                        value={cond.column}
                        onChange={(e) => handleUpdateHavingCondition(idx, 'column', e.target.value)}
                        className="result-node__agg-select result-node__agg-select--col"
                      >
                        <option value="*">* (tutte)</option>
                        {groupByAvailableColumns.map((col, colIdx) => (
                          <option key={colIdx} value={col.name}>{col.name}</option>
                        ))}
                      </select>

                      {/* Operatore */}
                      <select
                        value={cond.operator}
                        onChange={(e) => handleUpdateHavingCondition(idx, 'operator', e.target.value)}
                        className="result-node__agg-select result-node__agg-select--func"
                      >
                        <option value=">">&gt;</option>
                        <option value=">=">&gt;=</option>
                        <option value="<">&lt;</option>
                        <option value="<=">&lt;=</option>
                        <option value="=">=</option>
                        <option value="!=">!=</option>
                      </select>

                      {/* Valore */}
                      <input
                        type="text"
                        value={cond.value}
                        onChange={(e) => handleUpdateHavingCondition(idx, 'value', e.target.value)}
                        placeholder="valore"
                        className="result-node__agg-alias"
                      />

                      {/* Rimuovi */}
                      <button type="button"
                        onClick={() => handleRemoveHavingCondition(idx)}
                        className="result-node__agg-remove"
                        title="Rimuovi condizione"
                      >
                        ×
                      </button>
                    </div>
                  ))}

                  <button type="button"
                    onClick={handleAddHavingCondition}
                    className="result-node__add-agg-btn"
                  >
                    + Aggiungi condizione HAVING
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Modalità Esecuzione (Manipola/Arricchisci) */}
        {hasExecutionOptions && (
          <div className="result-node__execution-options">
            <div className="result-node__label">Esecuzione:</div>
            
            <label className={`result-node__execution-option ${
              executionMode === 'cache' ? 'result-node__execution-option--selected' : ''
            }`}>
              <input 
                type="radio" 
                name={`executionMode-${id}`}
                value="cache"
                checked={executionMode === 'cache'}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="result-node__execution-radio"
              />
              <div className="result-node__execution-content">
                <div className="result-node__execution-title">
                  ⚡ Usa cache esistente
                </div>
                <div className="result-node__execution-desc">
                  Veloce - carica dati già elaborati (se disponibili)
                </div>
              </div>
            </label>
            
            <label className={`result-node__execution-option ${
              executionMode === 'recalculate' ? 'result-node__execution-option--selected' : ''
            }`}>
              <input 
                type="radio" 
                name={`executionMode-${id}`}
                value="recalculate"
                checked={executionMode === 'recalculate'}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="result-node__execution-radio"
              />
              <div className="result-node__execution-content">
                <div className="result-node__execution-title">
                  🔄 Ricalcola tutto
                </div>
                <div className="result-node__execution-desc">
                  {isArricchisci 
                    ? 'Aggiornato - riesegue tutte le chiamate API' 
                    : 'Aggiornato - riesegue tutte le query SQL'}
                </div>
              </div>
            </label>
          </div>
        )}

        {/* Bottoni azione */}
        <div className="result-node__actions">
          {/* Bottone Valida */}
          <button type="button"
            onClick={handleValidate}
            className="result-node__validate-btn"
            title="Valida il canvas prima di eseguire"
          >
            <span>🔍</span>
            <span>Valida</span>
          </button>

          {/* Bottone Preview */}
          {hasOnPreview && (
            <button type="button"
              onClick={handlePreviewWithValidation}
              className={`result-node__preview-btn result-node__preview-btn--${nodeVariant}`}
            >
              <span className="result-node__preview-icon">👁️</span>
              <span>Preview</span>
            </button>
          )}

          {/* Bottone Esegui */}
          {hasOnExecute && (
            <button type="button"
              onClick={handleExecuteWithValidation}
              className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
            >
              <span className="result-node__execute-icon">▶️</span>
              <span>{executeLabel}</span>
            </button>
          )}
        </div>

        {/* Footer cronologia run */}
        <div className="result-node__footer">
          <button type="button"
            onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }} onMouseDown={(e) => e.stopPropagation()}
            className="result-node__history-btn"
            title="Cronologia esecuzioni"
          >
            <span>🕐</span>
            <span>Cronologia run</span>
          </button>
        </div>
        </>
        )}

        <ValidationModal
          isOpen={showValidation}
          onClose={() => setShowValidation(false)}
          issues={validationResult.issues}
          isValid={validationResult.isValid}
        />

        {/* Modal cronologia run */}
        <RunHistoryModal
          isOpen={isRunHistoryOpen}
          onClose={() => setIsRunHistoryOpen(false)}
          canvasId={data.project_id || data.canvas_id}
          nodeId={id}
          nodeLabel="Output"
        />

        {/* Modali IMDF: leggono dati dall'ultimo task result */}
        <ImdfPreviewModal
          isOpen={openImdfModal === 'preview'}
          onClose={() => setOpenImdfModal(null)}
          imdfData={imdfData}
        />
        <ImdfManifestModal
          isOpen={openImdfModal === 'manifest'}
          onClose={() => setOpenImdfModal(null)}
          manifest={imdfData?.manifest}
          siteId={imdfData?.site_id}
          publish={imdfData?.publish}
        />
        <ImdfValidationModal
          isOpen={openImdfModal === 'validation'}
          onClose={() => setOpenImdfModal(null)}
          validation={imdfData?.validation}
        />

        {/* Modal gestione cronologia bundle */}
        <ImdfBundlesManagerModal
          isOpen={bundlesManagerOpen}
          onClose={() => setBundlesManagerOpen(false)}
          bundles={bundles}
          selectedTaskId={bundleTaskId}
          onSelect={(tid) => {
            setSelectedBundleId(tid);
            setBundlesManagerOpen(false);
          }}
          onDelete={handleDeleteBundle}
        />
      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;