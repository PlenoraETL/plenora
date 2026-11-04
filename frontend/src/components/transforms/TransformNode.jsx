/**
 * TransformNode.jsx - Modulo di un passo di trasformazione (MANIPOLA).
 *
 * Montato solo nel pannello laterale (NodeInspector): sul canvas il passo e'
 * un CompactNode. Intestazione, titolo, chiusura ed eliminazione sono del
 * pannello (regola 6 del Blocco A); qui restano operazione e parametri.
 * Anche durante il caricamento del registry non c'e' una vecchia
 * intestazione: il modulo mostra "Caricamento operazioni…".
 *
 * Nessun calcolo colonne, nessuna propagazione: legge data.inputColumns
 * popolato da useColumnPropagation.
 */
import React, { memo, useState, useEffect, useMemo } from 'react';
import { useReactFlow } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import './TransformNode.css';
import DynamicConfig from './DynamicConfig';

import { authBus } from '../../utils/authBus';
import RunHistoryModal from '../modals/RunHistoryModal';
import { dataFlowEdges, isParameterBindingEdge } from '../../utils/runtimeBindingEdges';
import { OPERATION_PANELS, LookupSuggestion } from './panels';
import { fetchTransformRegistry, getCachedTransformRegistry } from '../../utils/transformRegistry';
import { nodeTitle } from '../canvas/nodeDescriptors';

// Cache del registry condivisa con i riassunti dei nodi compatti.
const fetchRegistry = fetchTransformRegistry;
const getRegistryCache = getCachedTransformRegistry;

const TransformNode = memo(({ data, id }) => {
  const { getEdges, getNodes } = useReactFlow();

  const transform_op = data.transform_op || 'filter';
  const config = data.config || {};

  // Registry
  const [registry, setRegistry] = useState(getRegistryCache());
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);

  // Invalida la cache al logout: evita che User B veda il registry di User A
  useEffect(() => {
    // La cache condivisa si svuota da sola (utils/transformRegistry).
    return authBus.addLogoutListener(() => {
      setRegistry(null);
    });
  }, []);

  useEffect(() => {
    if (!registry) {
      fetchRegistry().then(data => {
        if (data) setRegistry(data);
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

  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const patchNodeConfig = useCanvasStore(s => s.patchNodeConfig);

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

      // Nome leggibile: lo stesso titolo del resto dell'interfaccia (nodeDescriptors)
      let name = nodeTitle(node.type, node.data) || node.id;
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
  }, [id, getEdges, getNodes, inputColumns, transformConfig]);

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

  // Cambio di operazione e conversione in Mappa valori: il titolo derivato
  // segue da solo (nodeDescriptors), nessun titolo da aggiornare qui.
  const onOpChange = (evt) => {
    updateNodeData(id, { transform_op: evt.target.value, config: {} });
    notifyConfigChanged();
  };

  // Input runtime collegati a un parametro singolo -> resta il form
  // generico (i pannelli usano le liste).
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const hasParameterBindings = useCanvasStore(s => (s.edges || []).some(e => e.target === id && isParameterBindingEdge(e)));
  const convertToLookup = (lookupConfig) => {
    updateNodeData(id, { transform_op: 'lookup', config: lookupConfig });
    notifyConfigChanged();
  };

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
      </div>
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={projectId}
        nodeId={id}
        nodeLabel={nodeTitle('transform', data)}
      />
    </div>
  );
});

TransformNode.displayName = 'TransformNode';

export default TransformNode;
