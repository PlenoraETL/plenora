/**
 * TransformNode.jsx - Nodo per trasformazioni dati (MANIPOLA)
 * 
 * VERSIONE SEMPLIFICATA: Nessun calcolo colonne, nessuna propagazione.
 * Legge data.inputColumns popolato da useColumnPropagation.
 */
import React, { memo, useState, useEffect, useMemo } from 'react';
import { Position, useReactFlow } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import './TransformNode.css';
import DynamicConfig from './DynamicConfig';

import { authBus } from '../../utils/authBus';
import RunHistoryModal from '../modals/RunHistoryModal';
import { useConfirm } from '../../context/ConfirmContext';
import { dataFlowEdges, isParameterBindingEdge } from '../../utils/runtimeBindingEdges';
import { OPERATION_PANELS, LookupSuggestion } from './panels';
import NodeHandle from '../canvas/NodeHandle';
import { fetchTransformRegistry, getCachedTransformRegistry, isMultiInputOp } from '../../utils/transformRegistry';

// Cache del registry condivisa con i riassunti dei nodi compatti.
const fetchRegistry = fetchTransformRegistry;
const getRegistryCache = getCachedTransformRegistry;

const TransformNode = memo(({ data, id, inInspector = false }) => {
  const { getEdges, getNodes } = useReactFlow();
  
  const transform_op = data.transform_op || 'filter';
  const config = data.config || {};

  // Registry
  const [registry, setRegistry] = useState(getRegistryCache());
  const [loading, setLoading] = useState(!getRegistryCache());
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);

  // Invalida la cache al logout: evita che User B veda il registry di User A
  useEffect(() => {
    // La cache condivisa si svuota da sola (utils/transformRegistry).
    return authBus.addLogoutListener(() => {
      setRegistry(null);
      setLoading(true);
    });
  }, []);

  useEffect(() => {
    if (!registry) {
      fetchRegistry().then(data => {
        if (data) setRegistry(data);
        setLoading(false);
      });
    }
  }, [registry]);

  // Config della trasformazione corrente
  const transformConfig = registry?.transforms?.[transform_op];
  const categories = registry?.categories || { 
    base: { id: 'base', name: 'Base', icon: '📊' },
    spatial: { id: 'spatial', name: 'Spaziale', icon: '🌍' },
    privacy: { id: 'privacy', name: 'Privacy', icon: '🔒' }
  };

  // Tab attiva
  const [activeTab, setActiveTab] = useState(() => transformConfig?.category || 'base');
  const collapsed = data.collapsed || false;

  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const patchNodeConfig = useCanvasStore(s => s.patchNodeConfig);
  const deleteNode     = useCanvasStore(s => s.deleteNode);
  const confirm        = useConfirm();

  const toggleCollapsed = () => {
    updateNodeData(id, { collapsed: !collapsed });
  };
  
  useEffect(() => {
    if (transformConfig?.category) {
      setActiveTab(transformConfig.category);
    }
  }, [transform_op, transformConfig]);

  // useColumnPropagation popola data.inputColumns, noi le leggiamo e basta
  const inputColumns = useMemo(() => {
    return data.inputColumns || [];
  }, [data.inputColumns]);
  
  const geometryColumns = useMemo(() => {
    return inputColumns.filter(c => 
      c.type?.toLowerCase().includes('geometry')
    );
  }, [inputColumns]);

  // Per join: separa colonne sinistra (primo input) e destra (secondo input)
  const { leftColumns, rightColumns, leftSourceName, rightSourceName } = useMemo(() => {
    const isMultiInput = transformConfig?.inputs === 'multi';
    if (!isMultiInput) {
      return { leftColumns: inputColumns, rightColumns: [], leftSourceName: '', rightSourceName: '' };
    }

    const edges = dataFlowEdges(getEdges());
    const nodes = getNodes();
    const incomingEdges = edges.filter(e => e.target === id);

    // Trova edge per handle specifico
    const leftEdge = incomingEdges.find(e => e.targetHandle === 'input-0');
    const rightEdge = incomingEdges.find(e => e.targetHandle === 'input-1');

    const getNodeInfo = (edge) => {
      if (!edge) return { columns: [], name: '' };
      const node = nodes.find(n => n.id === edge.source);
      if (!node) return { columns: [], name: '?' };
      const cols = node.data?.columns || [];
      
      // Risolvi nome leggibile
      let name = node.data?.label ||
                 node.data?.source_project_id ||
                 node.data?.file_name;
      
      // Per TransformNode: mostra il tipo di operazione
      if (!name && node.type === 'transform') {
        const op = node.data?.transform_op;
        const opConfig = registry?.transforms?.[op];
        name = opConfig ? `${opConfig.icon || ''} ${opConfig.name}` : op || node.id;
      }
      
      // Per ProjectSourceNode
      if (!name && node.type === 'projectSource') {
        name = node.data?.source_project_id || 'Progetto';
      }

      // Fallback
      if (!name) name = node.id;
      
      if (name.length > 20) name = name.substring(0, 17) + '...';

      return { 
        columns: cols.map(c => typeof c === 'string' ? { name: c, type: 'str' } : c),
        name
      };
    };

    const left = getNodeInfo(leftEdge);
    const right = getNodeInfo(rightEdge);
    // Colonne per lato: quelle gia' propagate (con alias e visibilita'
    // applicati, marcate left/right) sono i nomi che il backend vede; le
    // colonne grezze del nodo a monte solo se la propagazione non le marca.
    const marked = inputColumns.some(c => c.source === 'left' || c.source === 'right');

    return { 
      leftColumns: marked ? inputColumns.filter(c => c.source === 'left') : left.columns,
      rightColumns: marked ? inputColumns.filter(c => c.source === 'right') : right.columns,
      leftSourceName: left.name,
      rightSourceName: right.name
    };
  }, [id, getEdges, getNodes, inputColumns, transformConfig, registry]);

  // Filtra operazioni per tab
  const filteredOperations = useMemo(() => {
    if (!registry?.transforms) return [];
    return Object.entries(registry.transforms)
      .filter(([_, cfg]) => cfg.category === activeTab)
      .map(([opId, cfg]) => ({
        value: opId,
        label: `${cfg.icon || '⚙️'} ${cfg.name}`
      }));
  }, [registry, activeTab]);

  const handleTabChange = (newTab) => {
    setActiveTab(newTab);
    
    // Se l'operazione corrente non è nella nuova categoria, seleziona la prima
    if (transformConfig?.category !== newTab) {
      const firstOp = Object.entries(registry?.transforms || {})
        .find(([_, cfg]) => cfg.category === newTab);
      if (firstOp) {
        updateNodeData(id, { transform_op: firstOp[0], config: {} });
      }
    }
  };

  // La patch si fonde con la config CORRENTE dello store (patchNodeConfig),
  // non con quella di questo render: piu' patch nello stesso tick non si
  // sovrascrivono piu' (audit R1.5). L'editor viene solo avvisato per
  // ripropagare le colonne; non riscrive la config.
  const notifyConfigChanged = () => {
    setTimeout(() => {
      const onUpdateTransform = useCanvasStore.getState().editorActionsRef?.current?.onUpdateTransform;
      if (onUpdateTransform) onUpdateTransform(id);
    }, 0);
  };
  const toPatch = (field, value) => (
    typeof field === 'object' && field !== null ? field : { [field]: value }
  );
  // Modifica dell'utente: dirty + undo.
  const updateConfig = (field, value) => {
    patchNodeConfig(id, toPatch(field, value));
    notifyConfigChanged();
  };
  // Correzione automatica (default, id colonna, rinomine): dato derivato,
  // non rende il canvas "modificato" all'apertura (audit R1.8).
  const applyDerivedConfig = (field, value) => {
    patchNodeConfig(id, toPatch(field, value), { derived: true });
    notifyConfigChanged();
  };

  const onOpChange = (evt) => {
    updateNodeData(id, { transform_op: evt.target.value, config: {} });
    notifyConfigChanged();
  };

  // Pannello laterale (Fase 4, M4): input runtime collegati a un parametro
  // singolo -> resta il form generico (i pannelli usano le liste).
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const hasParameterBindings = useCanvasStore(s => (s.edges || []).some(e => e.target === id && isParameterBindingEdge(e)));
  const convertToLookup = (lookupConfig) => {
    updateNodeData(id, { transform_op: 'lookup', config: lookupConfig });
    notifyConfigChanged();
  };

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Elimina nodo',
      message: 'Eliminare questo nodo?',
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (ok) deleteNode(id);
  };

  // Determina se l'operazione richiede multi-input
  // Controlla sia dal registry (se caricato) che da lista nota di operazioni multi-input
  const isMultiInput = transformConfig?.inputs === 'multi' || isMultiInputOp(transform_op);

  if (loading) {
    // Anche in loading, mostra dual handles se è operazione multi-input nota
    const showDualHandles = isMultiInputOp(transform_op);
    
    return (
      <div className="transform-node transform-node--loading">
        {showDualHandles ? (
          <>
            <NodeHandle type="target" position={Position.Left} id="input-0" 
              className="transform-node__handle transform-node__handle--left" style={{ top: '30%' }} />
            <NodeHandle type="target" position={Position.Left} id="input-1" 
              className="transform-node__handle transform-node__handle--right" style={{ top: '70%' }} />
          </>
        ) : (
          <NodeHandle type="target" position={Position.Left} className="transform-node__handle" />
        )}
        <div className="transform-node__header">
          <span>⚙️ Trasformazione</span>
        </div>
        <div className="transform-node__body">
          <div className="transform-node__loading">Caricamento...</div>
        </div>
        <NodeHandle type="source" position={Position.Right} className="transform-node__handle" />
      </div>
    );
  }

  if (inInspector) {
    const Panel = hasParameterBindings ? null : OPERATION_PANELS[transform_op];
    const opsByCategory = Object.entries(categories).map(([catId, cat]) => ({
      catId, cat,
      ops: Object.entries(registry?.transforms || {}).filter(([, cfg]) => cfg.category === catId),
    })).filter(g => g.ops.length > 0);
    return (
      <div className="transform-inspector ppanel">
        <div className="ppanel__field">
          <label className="ppanel__label" htmlFor={`op-${id}`}>Operazione</label>
          <select id={`op-${id}`} value={transform_op} onChange={onOpChange} className="ppanel__input" disabled={isReadOnly || !registry}>
            {!registry && <option value={transform_op}>Caricamento operazioni…</option>}
            {opsByCategory.map(({ catId, cat, ops }) => (
              <optgroup key={catId} label={`${cat.icon || ''} ${cat.name}`}>
                {ops.map(([opId, cfg]) => <option key={opId} value={opId}>{cfg.icon} {cfg.name}</option>)}
              </optgroup>
            ))}
          </select>
          {Panel && transformConfig?.description && <div className="ppanel__note">{transformConfig.description}</div>}
        </div>
        {transform_op === 'conditional' && !hasParameterBindings && (
          <LookupSuggestion config={config} columns={inputColumns} onConvert={convertToLookup} disabled={isReadOnly} />
        )}
        {Panel ? (
          <Panel
            config={config}
            updateConfig={updateConfig}
            inputColumns={inputColumns}
            leftColumns={leftColumns}
            rightColumns={rightColumns}
            leftSourceName={leftSourceName}
            rightSourceName={rightSourceName}
            disabled={isReadOnly}
          />
        ) : registry?.transforms ? (
          <DynamicConfig
            transformId={transform_op}
            config={config}
            updateConfig={updateConfig}
            applyDerivedConfig={applyDerivedConfig}
            registry={registry.transforms}
            inputColumns={inputColumns}
            leftColumns={leftColumns}
            rightColumns={rightColumns}
            geometryColumns={geometryColumns}
            targetNodeId={id}
          />
        ) : (
          <div className="ppanel__note">Caricamento configurazione…</div>
        )}
        {hasParameterBindings && OPERATION_PANELS[transform_op] && (
          <div className="ppanel__hint">Questo passo ha input runtime collegati ai parametri: si usa il modulo completo.</div>
        )}
        <div className="transform-inspector__foot">
          <button type="button" className="ppanel__btn" onClick={() => setIsRunHistoryOpen(true)}>🕐 Cronologia run</button>
          {!isReadOnly && <button type="button" className="ppanel__btn transform-inspector__delete" onClick={handleDelete}>Elimina passo</button>}
        </div>
        <RunHistoryModal
          isOpen={isRunHistoryOpen}
          onClose={() => setIsRunHistoryOpen(false)}
          canvasId={projectId}
          nodeId={id}
          nodeLabel={data.label || transform_op}
        />
      </div>
    );
  }

  return (
    <div className={`transform-node transform-node--${transform_op} ${isMultiInput ? 'transform-node--multi-input' : ''} ${collapsed ? 'transform-node--collapsed' : ''}`}>
      {/* Handle input: singolo o doppio in base all'operazione */}
      {isMultiInput ? (
        <>
          {/* Handle SINISTRA (input-0) - in alto */}
          <NodeHandle
            type="target"
            position={Position.Left}
            id="input-0"
            className="transform-node__handle transform-node__handle--left"
            style={{ top: '30%' }}
          />
          {/* Label SX */}
          <div className="transform-node__handle-label transform-node__handle-label--left" style={{ top: '26%' }}>
            SX
          </div>
          
          {/* Handle DESTRA (input-1) - in basso */}
          <NodeHandle
            type="target"
            position={Position.Left}
            id="input-1"
            className="transform-node__handle transform-node__handle--right"
            style={{ top: '70%' }}
          />
          {/* Label DX */}
          <div className="transform-node__handle-label transform-node__handle-label--right" style={{ top: '66%' }}>
            DX
          </div>
        </>
      ) : (
        <NodeHandle
          type="target"
          position={Position.Left}
          className="transform-node__handle"
        />
      )}

      {/* Header */}
      <div className="transform-node__header">
        <div className="transform-node__title">
          <button type="button"
            className="transform-node__collapse-btn"
            onClick={toggleCollapsed}
            onMouseDown={(e) => e.stopPropagation()}
            title={collapsed ? 'Espandi' : 'Comprimi'}
          >
            {collapsed ? '▸' : '▾'}
          </button>
          <span>{transformConfig?.icon || '⚙️'} {collapsed ? (transformConfig?.name || 'Trasformazione') : 'Trasformazione'}</span>
        </div>
        <button type="button"
          onClick={handleDelete}
          className="transform-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body — nascosto quando collapsed */}
      {!collapsed && (
      <div className="transform-node__body">
        {/* Tabs per categoria */}
        <div className="transform-node__tabs">
          {Object.entries(categories).map(([catId, cat]) => (
            <button type="button"
              key={catId}
              className={`transform-node__tab ${activeTab === catId ? 'transform-node__tab--active' : ''}`}
              onClick={() => handleTabChange(catId)}
            >
              {cat.icon} {cat.name}
            </button>
          ))}
        </div>

        {/* Selezione tipo operazione */}
        <div className="transform-node__section">
          <label className="transform-node__label">Tipo operazione:</label>
          <select 
            value={transform_op} 
            onChange={onOpChange}
            className="transform-node__select"
          >
            {filteredOperations.map(op => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>
        </div>

        {/* Descrizione operazione */}
        {transformConfig?.description && (
          <div className="transform-node__description">
            {transformConfig.description}
          </div>
        )}

        {/* Sorgenti collegate (per join) */}
        {transformConfig?.inputs === 'multi' && leftSourceName && rightSourceName && (
          <div className="transform-node__sources-info">
            <span className="transform-node__source-tag transform-node__source-tag--left">⬅ SX: {leftSourceName}</span>
            <span className="transform-node__source-tag transform-node__source-tag--right">➡ DX: {rightSourceName}</span>
          </div>
        )}

        {/* DynamicConfig per parametri */}
        {registry?.transforms ? (
          <DynamicConfig
            transformId={transform_op}
            config={config}
            updateConfig={updateConfig}
            applyDerivedConfig={applyDerivedConfig}
            registry={registry.transforms}
            inputColumns={inputColumns}
            leftColumns={leftColumns}
            rightColumns={rightColumns}
            geometryColumns={geometryColumns}
            targetNodeId={id}
          />
        ) : (
          <div className="transform-node__message">
            ⏳ Caricamento configurazione...
          </div>
        )}
      </div>
      )}

      <NodeHandle
        type="source"
        position={Position.Right}
        className="transform-node__handle"
      />

      {/* Cronologia run */}
      <div className="transform-node__footer">
        <button
          type="button"
          className="transform-node__history-btn"
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }} onMouseDown={(e) => e.stopPropagation()}
          title="Cronologia esecuzioni"
        >
          🕐 Cronologia run
        </button>
      </div>
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={projectId}
        nodeId={id}
        nodeLabel={data.label || transform_op}
      />
    </div>
  );
});

TransformNode.displayName = 'TransformNode';

export default TransformNode;
