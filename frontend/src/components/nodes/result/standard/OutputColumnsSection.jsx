/**
 * OutputColumnsSection — "Colonne in uscita" nell'Output (Fase 4, M5).
 *
 *  - Manipola e Arricchisci: la griglia scrive `data.output_columns`
 *    ({name, name_column_id?, keep, label}); il backend la applica prima di
 *    GROUP BY con le stesse regole di "Seleziona e rinomina". Dove il
 *    backend del tipo di progetto risolve per id (Manipola; Arricchisci per
 *    ora no: columnSource.PROJECT_COLUMN_CAPABILITIES) le righe seguono
 *    l'id della colonna a monte: una colonna rinominata a monte conserva
 *    nome in uscita, scelta e posizione. Altrove si legge e si scrive per
 *    nome, come il file che uscira'. Le altre
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
import { useShallow } from 'zustand/react/shallow';
import useCanvasStore from '../../../../store/useCanvasStore';
import { estraiSourceLabel, outputColumnsAsRead, outputResolvesById, upstreamSourceNodes } from '../../../../utils/columnSource';
import { isColumnVisible } from '../../../../utils/columnUtils';
import { outputReferencesPatch } from '../../../../utils/columnPlans';
import ColumnsGrid from '../../../transforms/panels/ColumnsGrid';
import { gridConfig, gridRows, savedSelectionError } from '../../../transforms/panels/panelConfig';

/**
 * Undo per azione (R1.6): scrivere l'etichetta della stessa colonna e' una
 * digitazione (un solo Ctrl+Z); ogni altro cambio della griglia e' un'azione.
 */
export function labelEditGroup(nodeId, before, next) {
  if (!Array.isArray(before) || !Array.isArray(next) || before.length !== next.length) return null;
  const same = (x, y) => x === y || JSON.stringify(x) === JSON.stringify(y);
  const changed = next.map((row, i) => i).filter(i => !same(next[i], before[i]));
  if (changed.length !== 1) return null;
  const a = before[changed[0]];
  const b = next[changed[0]];
  if (a.name !== b.name) return null;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  const diff = [...keys].filter(k => !same(a[k], b[k]));
  return diff.length === 1 && diff[0] === 'label' ? `output:${nodeId}:label:${b.name}` : null;
}

export function PipelineOutputColumns({ id, data, mode = 'manipola', upstreamColumns, disabled }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  // Per id solo dove il backend di questo tipo di progetto risolve per id
  // (columnSource.PROJECT_COLUMN_CAPABILITIES); altrimenti per nome, in
  // scrittura E in lettura, come il file che uscira'.
  const byId = outputResolvesById(mode);
  const saved = useMemo(() => outputColumnsAsRead(data.output_columns, mode), [data.output_columns, mode]);
  const rows = useMemo(() => gridRows(upstreamColumns, saved), [upstreamColumns, saved]);
  const onChange = (next) => {
    const outputColumns = gridConfig(next, upstreamColumns, { withIds: byId });
    // Dai dati CORRENTI dello store (T6, regola degli aggiornamenti funzionali).
    updateNodeData(id, (d) => ({
      ...outputReferencesPatch({ ...d, output_columns: outputColumnsAsRead(d.output_columns, mode) }, upstreamColumns, outputColumns),
      output_columns: outputColumns,
    }), { group: labelEditGroup(id, rows, next) });
  };
  return (
    <div className="result-node__section result-node__section--bordered">
      <ColumnsGrid
        rows={rows}
        disabled={disabled}
        savedError={savedSelectionError(upstreamColumns, saved)}
        onChange={onChange}
        note="Tieni, rinomina e ordina qui: niente più nodi Rinomina o Elimina colonne prima dell'Output. Le colonne nuove a monte compaiono da sole."
      />
    </div>
  );
}

const SEP = '::';
const NONE = [];

export function EstraiOutputColumns({ id, disabled }) {
  const editNodes = useCanvasStore(s => s.editNodes);
  // Le stesse tabelle e layer che la fonte unica da' in ingresso all'Output.
  // Selettore per nodo: id, tipo e dati delle sorgenti (non le posizioni),
  // quindi trascinare un nodo non ridisegna la griglia.
  const flat = useCanvasStore(useShallow(s => upstreamSourceNodes(s.nodes || NONE, s.edges || NONE, id)
    .flatMap(n => [n.id, n.type, n.data])));
  const sources = useMemo(() => {
    const out = [];
    for (let i = 0; i < flat.length; i += 3) out.push({ id: flat[i], type: flat[i + 1], data: flat[i + 2] || {} });
    return out;
  }, [flat]);

  const rows = useMemo(() => sources.flatMap((s) => {
    const label = estraiSourceLabel(s);
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
