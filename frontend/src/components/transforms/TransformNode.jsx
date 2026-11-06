/**
 * TransformNode.jsx - Modulo di un passo di trasformazione (MANIPOLA).
 *
 * Montato solo nel pannello laterale (NodeInspector): sul canvas il passo e'
 * un CompactNode. Intestazione, titolo, chiusura ed eliminazione sono del
 * pannello (regola 6 del Blocco A); qui restano operazione e parametri.
 * Anche durante il caricamento del registry non c'e' una vecchia
 * intestazione: il modulo mostra "Caricamento operazioni…".
 *
 * Nessun calcolo colonne: le colonne in ingresso (tutte e per lato, nell'ordine
 * delle maniglie) vengono dalla fonte unica (hooks/useColumns, columnsOf).
 */
import React, { memo, useState, useMemo } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { useColumnsOf, useNodesInfo } from '../../hooks/useColumns';
import { columnsConfirmed } from '../../utils/columnSource';
import ColumnStatusNote from '../common/ColumnStatusNote';
import DynamicConfig from './DynamicConfig';

import RunHistoryModal from '../modals/RunHistoryModal';
import { isParameterBindingEdge } from '../../utils/runtimeBindingEdges';
import { OPERATION_PANELS, LookupSuggestion } from './panels';
import { useTransformRegistry } from '../../hooks/useTransformRegistry';
import { ResourceError } from '../common/ListState';
import { nodeTitle } from '../canvas/nodeDescriptors';
import { term } from '../../utils/glossary';
import { READ_ONLY_SAFE } from '../common/readOnlySafe';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';


const TransformNode = memo(({ data, id }) => {
  const transform_op = data.transform_op || 'filter';
  const config = data.config || {};

  // Registry
  // Catalogo condiviso (hooks/useTransformRegistry): errore con «Riprova».
  const registryResource = useTransformRegistry();
  const registry = registryResource.data || null;
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);


  // Config della trasformazione corrente
  const transformConfig = registry?.transforms?.[transform_op];
  const categories = registry?.categories || {
    base: { id: 'base', name: 'Base', icon: '📊' },
    spatial: { id: 'spatial', name: 'Spaziale', icon: '🌍' },
    privacy: { id: 'privacy', name: 'Privacy', icon: '🔒' }
  };

  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const patchNodeConfig = useCanvasStore(s => s.patchNodeConfig);

  // Fonte unica (Blocco A, regola 4): ingresso intero e lati sinistro/destro
  // con gli id, nello stesso ordine dei lati del backend.
  const input = useColumnsOf(id, { side: 'input' });
  const left = useColumnsOf(id, { side: 'left' });
  const right = useColumnsOf(id, { side: 'right' });
  // Titoli dei lati: tipo e dati dei soli nodi a monte (non le posizioni).
  const [leftNode, rightNode] = useNodesInfo([left.inputs[0]?.nodeId || '', right.inputs[0]?.nodeId || '']);
  const inputColumns = input.columns;
  // Correzioni automatiche dei parametri solo su colonne confermate (regola
  // unica columnSource.columnsConfirmed).
  const columnsReady = columnsConfirmed(input);

  const geometryColumns = useMemo(() => {
    return inputColumns.filter(c =>
      c.type?.toLowerCase().includes('geometry')
    );
  }, [inputColumns]);

  // Per join: colonne sinistra (primo input) e destra (secondo input).
  const { leftColumns, rightColumns, leftSourceName, rightSourceName } = useMemo(() => {
    const isMultiInput = transformConfig?.inputs === 'multi';
    if (!isMultiInput) {
      return { leftColumns: inputColumns, rightColumns: [], leftSourceName: '', rightSourceName: '' };
    }
    // Nome leggibile: lo stesso titolo del resto dell'interfaccia (nodeDescriptors)
    const sideName = (side, node) => {
      const nodeId = side.inputs[0]?.nodeId;
      if (!nodeId) return '';
      if (!node) return '?';
      const name = nodeTitle(node.type, node.data) || node.id;
      return name.length > 20 ? name.substring(0, 17) + '...' : name;
    };
    return {
      leftColumns: left.columns,
      rightColumns: right.columns,
      leftSourceName: sideName(left, leftNode),
      rightSourceName: sideName(right, rightNode),
    };
  }, [inputColumns, left, right, leftNode, rightNode, transformConfig]);

  // La patch si fonde con la config CORRENTE dello store (patchNodeConfig),
  // non con quella di questo render: piu' patch nello stesso tick non si
  // sovrascrivono piu' (audit R1.5). Le colonne a valle le ricalcola la
  // fonte unica dal canvas cambiato: nessuna propagazione da avviare qui.
  const toPatch = (field, value) => (
    typeof field === 'object' && field !== null ? field : { [field]: value }
  );
  // Modifica dell'utente: dirty + undo.
  const updateConfig = (field, value) => {
    patchNodeConfig(id, toPatch(field, value));
  };
  // Correzione automatica (default, id colonna, rinomine): dato derivato,
  // non rende il canvas "modificato" all'apertura (audit R1.8).
  const applyDerivedConfig = (field, value) => {
    patchNodeConfig(id, toPatch(field, value), { derived: true });
  };

  // Input runtime collegati a un parametro singolo -> resta il form
  // generico (i pannelli usano le liste).
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const bindingCount = useCanvasDerived(s => (s.edges || []).filter(e => e.target === id && isParameterBindingEdge(e)).length, { deps: [id] });
  const hasParameterBindings = bindingCount > 0;
  const editGraph = useCanvasStore(s => s.editGraph);
  const confirm = useConfirmOrDeny();

  // Cambio di operazione (Fase 5, T6, R9-U N2): la configurazione e i
  // collegamenti agli input runtime sono della VECCHIA operazione. Se ce ne
  // sono si chiede conferma; poi operazione nuova, config vuota e
  // collegamenti tolti in UNA azione (un solo Ctrl+Z). Il titolo derivato
  // segue da solo (nodeDescriptors), nessun titolo da aggiornare qui.
  const onOpChange = async (evt) => {
    const nextOp = evt.target.value;
    if (nextOp === transform_op) return;
    const hasConfig = Object.keys(config).length > 0;
    if (hasConfig || bindingCount > 0) {
      const lost = [hasConfig ? 'la configurazione del passo' : null,
        bindingCount > 0 ? `${bindingCount === 1 ? 'il collegamento' : `${bindingCount} collegamenti`} agli input runtime` : null]
        .filter(Boolean).join(' e ');
      const ok = await confirm({
        title: 'Cambiare operazione?',
        message: `Cambiando operazione si perdono ${lost}. Continuare?`,
        confirmText: 'Cambia operazione',
        variant: 'warning',
      });
      if (!ok) return;
    }
    editGraph(({ nodes, edges }) => ({
      nodes: nodes.map(n => (n.id === id ? { ...n, data: { ...n.data, transform_op: nextOp, config: {} } } : n)),
      edges: edges.filter(e => !(e.target === id && isParameterBindingEdge(e))),
    }));
  };
  const convertToLookup = (lookupConfig) => {
    updateNodeData(id, { transform_op: 'lookup', config: lookupConfig });
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
          {!registry && <option value={transform_op}>{registryResource.status === 'error' ? 'Operazioni non disponibili' : 'Caricamento operazioni…'}</option>}
          {opsByCategory.map(({ catId, cat, ops }) => (
            <optgroup key={catId} label={`${cat.icon || ''} ${cat.name}`}>
              {ops.map(([opId, cfg]) => <option key={opId} value={opId}>{cfg.icon} {cfg.name}</option>)}
            </optgroup>
          ))}
        </select>
        {registryResource.status === 'error' && (
          <ResourceError compact resource={registryResource} title="Operazioni non caricate" />
        )}
        {Panel && transformConfig?.description && <div className="ppanel__note">{transformConfig.description}</div>}
        <ColumnStatusNote result={input} />
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
          columnsConfirmed={columnsReady}
          targetNodeId={id}
        />
      ) : (
        <div className="ppanel__note">Caricamento configurazione…</div>
      )}
      {hasParameterBindings && OPERATION_PANELS[transform_op] && (
        <div className="ppanel__hint">Questo passo ha input runtime collegati ai parametri: si usa il modulo completo.</div>
      )}
      <div className="transform-inspector__foot">
        <button type="button" {...READ_ONLY_SAFE} className="ppanel__btn" onClick={() => setIsRunHistoryOpen(true)} title={term('run_history').description}>{term('run_history').icon} {term('run_history').name}</button>
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
