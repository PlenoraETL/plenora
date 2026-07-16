/**
 * TransformNode.jsx - Nodo per trasformazioni dati (MANIPOLA)
 * 
 * VERSIONE SEMPLIFICATA: Nessun calcolo colonne, nessuna propagazione.
 * Legge data.inputColumns popolato da useColumnPropagation.
 */
import React, { memo, useState, useEffect, useMemo } from 'react';
import { Handle, Position, useReactFlow } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import './TransformNode.css';
import DynamicConfig from './DynamicConfig';

import { authBus } from '../../utils/authBus';
import { getTransformRegistry } from '../../api/transforms';
import RunHistoryModal from '../modals/RunHistoryModal';
import { useConfirm } from '../../context/ConfirmContext';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';

// Cache globale per il registry
let registryCache = null;
let registryCachePromise = null;

const fetchRegistry = async () => {
  if (registryCache) return registryCache;
  if (registryCachePromise) return registryCachePromise;

  registryCachePromise = getTransformRegistry()
    .then(data => {
      registryCache = data;
      registryCachePromise = null;
      return data;
    })
    .catch(err => {
      console.error('[TransformNode] Errore caricamento registry:', err);
      registryCachePromise = null;
      return null;
    });

  return registryCachePromise;
};

const TransformNode = memo(({ data, id }) => {
  const { setNodes, getEdges, getNodes } = useReactFlow();
  
  const transform_op = data.transform_op || 'filter';
  const config = data.config || {};

  // Registry
  const [registry, setRegistry] = useState(registryCache);
  const [loading, setLoading] = useState(!registryCache);
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);

  // Invalida la cache al logout: evita che User B veda il registry di User A
  useEffect(() => {
    return authBus.addLogoutListener(() => {
      registryCache = null;
      registryCachePromise = null;
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

    return { 
      leftColumns: left.columns, 
      rightColumns: right.columns,
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

  const updateConfig = (field, value) => {
    const patch = typeof field === 'object' && field !== null
      ? field
      : { [field]: value };
    const nextConfig = { ...config, ...patch };
    updateNodeData(id, { config: nextConfig });
    setTimeout(() => {
      const onUpdateTransform = useCanvasStore.getState().editorActionsRef?.current?.onUpdateTransform;
      if (onUpdateTransform) onUpdateTransform(id, { config: nextConfig });
    }, 0);
  };

  const onOpChange = (evt) => {
    updateNodeData(id, { transform_op: evt.target.value, config: {} });
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
  const KNOWN_MULTI_INPUT_OPS = ['join', 'concat', 'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference', 'table_diff'];
  const isMultiInput = transformConfig?.inputs === 'multi' || KNOWN_MULTI_INPUT_OPS.includes(transform_op);

  if (loading) {
    // Anche in loading, mostra dual handles se è operazione multi-input nota
    const showDualHandles = KNOWN_MULTI_INPUT_OPS.includes(transform_op);
    
    return (
      <div className="transform-node transform-node--loading">
        {showDualHandles ? (
          <>
            <Handle type="target" position={Position.Left} id="input-0" 
              className="transform-node__handle transform-node__handle--left" style={{ top: '30%' }} />
            <Handle type="target" position={Position.Left} id="input-1" 
              className="transform-node__handle transform-node__handle--right" style={{ top: '70%' }} />
          </>
        ) : (
          <Handle type="target" position={Position.Left} className="transform-node__handle" />
        )}
        <div className="transform-node__header">
          <span>⚙️ Trasformazione</span>
        </div>
        <div className="transform-node__body">
          <div className="transform-node__loading">Caricamento...</div>
        </div>
        <Handle type="source" position={Position.Right} className="transform-node__handle" />
      </div>
    );
  }

  return (
    <div className={`transform-node transform-node--${transform_op} ${isMultiInput ? 'transform-node--multi-input' : ''} ${collapsed ? 'transform-node--collapsed' : ''}`}>
      {/* Handle input: singolo o doppio in base all'operazione */}
      {isMultiInput ? (
        <>
          {/* Handle SINISTRA (input-0) - in alto */}
          <Handle
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
          <Handle
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
        <Handle
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

      <Handle
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
