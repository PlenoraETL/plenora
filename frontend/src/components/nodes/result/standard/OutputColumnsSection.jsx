/**
 * OutputColumnsSection — "Colonne in uscita" nell'Output (Fase 4, M5).
 *
 *  - Manipola e Arricchisci: la griglia scrive `data.output_columns`
 *    ({name, keep, label}, per nome); il backend la applica prima di
 *    GROUP BY con le stesse regole di "Seleziona e rinomina". Le altre
 *    impostazioni dell'Output che nominano colonne (GROUP BY, ORDER BY,
 *    HAVING, geometria) seguono nella stessa scrittura
 *    (columnPlans.outputReferencesPatch).
 *  - Estrai: le colonne escono dalle tabelle e dai layer (SELECT). La
 *    griglia modifica direttamente le loro colonne (visible / alias): stessa
 *    fonte della query. I nomi finiscono in SQL (identificatori), l'ordine
 *    segue le tabelle; le colonne calcolate si vedono ma si modificano
 *    nella tabella.
 */
import React, { useMemo } from 'react';
import useCanvasStore from '../../../../store/useCanvasStore';
import { dataFlowEdges } from '../../../../utils/runtimeBindingEdges';
import { isColumnVisible } from '../../../../utils/columnUtils';
import { outputReferencesPatch } from '../../../../utils/columnPlans';
import ColumnsGrid from '../../../transforms/panels/ColumnsGrid';
import { gridConfig, gridRows, savedSelectionError } from '../../../transforms/panels/panelConfig';

export function PipelineOutputColumns({ id, data, upstreamColumns, disabled }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const rows = useMemo(() => gridRows(upstreamColumns, data.output_columns), [upstreamColumns, data.output_columns]);
  const onChange = (next) => {
    // Per nome: l'esecuzione dell'Output non riceve gli id delle colonne.
    const outputColumns = gridConfig(next, upstreamColumns, { withIds: false });
    updateNodeData(id, {
      ...outputReferencesPatch(data, upstreamColumns, outputColumns),
      output_columns: outputColumns,
    });
  };
  return (
    <div className="result-node__section result-node__section--bordered">
      <ColumnsGrid
        rows={rows}
        disabled={disabled}
        savedError={savedSelectionError(upstreamColumns, data.output_columns)}
        onChange={onChange}
        note="Tieni, rinomina e ordina qui: niente più nodi Rinomina o Elimina colonne prima dell'Output. Le colonne nuove a monte compaiono da sole."
      />
    </div>
  );
}

const SEP = '::';
const NONE = [];
const SOURCE_TYPES = ['tableNode', 'featureLayerNode'];
const sourceLabel = (n) => (n.type === 'featureLayerNode'
  ? (n.data.layer_name || 'feature')
  : (n.data.alias || n.data.table_name));

/** Tabelle e layer collegati (a monte) all'Output di Estrai, in ordine di visita. */
function upstreamSources(nodes, edges, resultId) {
  const flow = dataFlowEdges(edges);
  const seen = new Set();
  const out = [];
  const visit = (nodeId) => {
    if (seen.has(nodeId)) return;
    seen.add(nodeId);
    const node = nodes.find(n => n.id === nodeId);
    if (SOURCE_TYPES.includes(node?.type)) out.push(node);
    flow.filter(e => e.target === nodeId).forEach(e => visit(e.source));
  };
  visit(resultId);
  return out;
}

export function EstraiOutputColumns({ id, disabled }) {
  const nodes = useCanvasStore(s => s.nodes || NONE);
  const edges = useCanvasStore(s => s.edges || NONE);
  const editNodes = useCanvasStore(s => s.editNodes);
  const sources = useMemo(() => upstreamSources(nodes, edges, id), [nodes, edges, id]);

  const rows = useMemo(() => sources.flatMap((s) => {
    const label = sourceLabel(s);
    const columns = (s.data.columns || []).map(col => ({
      name: `${s.id}${SEP}${col.name}`,
      display: `${label}.${col.name}`,
      keep: isColumnVisible(col),
      label: col.alias && col.alias !== col.name ? col.alias : '',
      type: col.type || '',
      missing: false,
    }));
    const calculated = (s.data.calculated_columns || []).map(col => ({
      name: `${s.id}${SEP}calc${SEP}${col.name}`,
      display: `${label}.${col.name}`,
      keep: true, label: '', type: col.output_type || '', missing: false, fixed: true,
    }));
    return [...columns, ...calculated];
  }), [sources]);

  const onChange = (next) => {
    const byKey = new Map(next.map(r => [r.name, r]));
    editNodes(all => all.map((n) => {
      if (!sources.some(s => s.id === n.id)) return n;
      let changed = false;
      const columns = (n.data.columns || []).map((col) => {
        const r = byKey.get(`${n.id}${SEP}${col.name}`);
        if (!r) return col;
        const alias = r.label && r.label !== col.name ? r.label : null;
        if (isColumnVisible(col) === r.keep && (col.alias || null) === alias) return col;
        changed = true;
        return { ...col, visible: r.keep, alias };
      });
      return changed ? { ...n, data: { ...n.data, columns } } : n;
    }));
  };

  if (sources.length === 0) return null;
  return (
    <div className="result-node__section result-node__section--bordered">
      <ColumnsGrid
        rows={rows}
        disabled={disabled}
        allowReorder={false}
        identifier
        onChange={onChange}
        note="Modifica le colonne delle tabelle e dei layer collegati (le stesse scelte del loro pannello). L'ordine segue le tabelle; le colonne calcolate si modificano nella tabella."
      />
    </div>
  );
}
