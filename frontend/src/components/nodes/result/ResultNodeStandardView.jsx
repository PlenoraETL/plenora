/**
 * ResultNodeStandardView — unica view del ResultNode (per ogni upstream).
 *
 * (Post-collasso IMDF a una-run non esiste piu' una vista IMDF dedicata:
 * il bundle e' un artefatto JSON come gli altri.)
 *
 * Owna:
 *   - dispatch a `OutputSettingsPanel` (filename + format + geo + distinct + limit);
 *   - preview colonne output con expand (columnsExpanded), passata come children
 *     al panel per preservare l'ordine visivo;
 *   - dispatch a `GroupOrderHavingPanel` (GROUP BY + aggregazioni + ORDER BY + HAVING);
 *   - dispatch a `ExecuteActionsPanel` (execution mode + bottoni Valida/Preview/
 *     Esegui + footer cronologia + RunHistoryModal);
 *   - deriva localmente nodeVariant/executeLabel da data.isManipulate/isArricchisci.
 *
 * NON owna:
 *   - editor filename + scelta formato + DISTINCT + LIMIT: gestiti da
 *     `OutputSettingsPanel`; il parent passa i flag derivati
 *     `isGeoFormat`, `availableGeomColumns`, `hasGeometryColumns` come props;
 *   - GROUP BY / ORDER BY / HAVING + relativi handler su updateNodeData:
 *     gestiti da `GroupOrderHavingPanel`; il parent
 *     passa `availableColumns`, `manipolaColumns`, `isEstrai`, `isManipulate`
 *     come props;
 *   - executionMode + isRunHistoryOpen useState + RunHistoryModal: gestiti da
 *     `ExecuteActionsPanel`; il parent passa `nodeVariant`,
 *     `executeLabel`, `hasExecutionOptions`, `isArricchisci` + le 3 callback;
 *   - ValidationModal: stato del parent, triggerata sia da qui sia da IMDF view;
 *   - logica runValidation/onExecute/onPreview: ricevute come callback;
 *   - upstreamColumns: computata dal parent (serve useReactFlow).
 *
 * Props (8):
 *   data                         — bag dati del nodo (fileName, format, columns, ...)
 *   id                           — node id (per updateNodeData + RunHistoryModal)
 *   upstreamColumns              — colonne dai nodi collegati (calcolate nel parent)
 *   hasOnPreview, hasOnExecute   — sottoscrizioni reattive dal parent (gating bottoni)
 *   onValidate()                 — handler Valida (parent apre ValidationModal)
 *   onExecute(options?)          — handler Esegui; options={force_recalculate: bool}
 *   onPreview()                  — handler Preview (parent fa validate + run)
 *
 * Il default di onExecute senza options e' gestito dal parent (force_recalculate=false),
 * cosi' ImdfView puo' continuare a chiamare onExecute() senza conoscere executionMode.
 */
import React, { useState, useEffect, memo } from 'react';
import useCanvasStore from '../../../store/useCanvasStore';
import {
  getColumnTypeIcon,
  getColumnTypeLabel,
  getColumnSRID,
  isGeometryType,
} from '../../../utils/columnUtils';
import OutputSettingsPanel from './standard/OutputSettingsPanel';
import GroupOrderHavingPanel from './standard/GroupOrderHavingPanel';
import ExecuteActionsPanel from './standard/ExecuteActionsPanel';

const COLUMNS_PREVIEW = 5;

const ResultNodeStandardView = memo(({
  data,
  id,
  upstreamColumns,
  hasOnPreview,
  hasOnExecute,
  onValidate,
  onExecute,
  onPreview,
}) => {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);

  const [columnsExpanded, setColumnsExpanded] = useState(false);

  // Tipi progetto (derivati da data)
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;
  const isEstrai = !isManipulate && !isArricchisci;
  const hasExecutionOptions = isManipulate || isArricchisci;

  // `format` resta nel parent perche' serve a derivare `isGeoFormat`
  // sia per il useEffect di auto-reset (CSV se non ci sono piu' colonne
  // geometriche) sia come prop al panel.
  const format = data.format || 'csv';

  // `groupByEnabled` resta nel parent perche' serve sia all'`OutputSettingsPanel`
  // (gating disabling DISTINCT) sia al gate della preview "Colonne Output".
  const groupByEnabled = data.group_by_enabled || false;

  // Formato geospaziale?
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Colonne geometriche candidate (MANIPOLA/ARRICCHISCI: upstream; ESTRAI: merge columns+upstream)
  // Per Manipola/Arricchisci si fa fallback a `data.columns` (autoritativo
  // dal canvas persistito post schema-inference) quando `upstreamColumns`
  // e' ancora vuoto al primo render — altrimenti l'auto-reset format -> csv
  // (sotto) scattava su un transient e azzerava la scelta utente di geojson.
  const geomSourceColumns = (() => {
    if (isManipulate || isArricchisci) {
      if (upstreamColumns.length > 0) return upstreamColumns;
      if ((data.columns || []).length > 0) return data.columns;
      return data.availableColumns || [];
    }
    const cols = data.columns || [];
    if (upstreamColumns.length > 0) {
      const existingNames = new Set(cols.map(c => c.name || c.alias));
      const extraCols = upstreamColumns.filter(c => !existingNames.has(c.name));
      return [...cols, ...extraCols];
    }
    return cols;
  })();

  const availableGeomColumns = geomSourceColumns
    .filter(col => isGeometryType(col.type))
    .map(col => {
      const displayName = col.alias || col.name || col;
      return col.source_table ? `${col.source_table}.${displayName}` : displayName;
    });

  const hasGeometryColumns = availableGeomColumns.length > 0;

  // Auto-reset: se formato e' geo ma non ci sono piu' colonne geometry → CSV
  useEffect(() => {
    if (!hasGeometryColumns && isGeoFormat) {
      updateNodeData(id, { format: 'csv' });
    }
  }, [hasGeometryColumns, isGeoFormat, id]);

  // Colonne disponibili per GROUP BY / output (SQL) — deduplicate per nome
  const availableColumnsRaw = (data.columns || []).map(col => ({
    name: col.alias || col.name,
    type: col.type || 'str',
    fullName: col.full_name || `${col.source_table}.${col.alias || col.name}`,
  }));
  const availableColumns = [...new Map(availableColumnsRaw.map(col => [col.name, col])).values()];

  // Colonne disponibili MANIPOLA (upstream o fallback availableColumns) — deduplicate
  const manipolaSourceCols = upstreamColumns.length > 0 ? upstreamColumns : (data.availableColumns || []);
  const manipolaColumnsRaw = manipolaSourceCols.map(col => ({
    name: col.name || col,
    type: col.type || 'str',
    fullName: col.name || col,
  }));
  const manipolaColumns = [...new Map(manipolaColumnsRaw.map(col => [col.name, col])).values()];

  // `groupByAvailableColumns` ora derivato dentro `GroupOrderHavingPanel`
  // dai props `availableColumns` / `manipolaColumns` / `isManipulate`.

  // Variante/label derivate localmente
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  const config = {
    estrai: { executeLabel: 'Esegui Query' },
    manipola: { executeLabel: 'Esegui Pipeline' },
    arricchisci: { executeLabel: 'Esegui Arricchimento' },
  };
  const { executeLabel } = config[nodeVariant];

  return (
    <>
      <OutputSettingsPanel
        data={data}
        id={id}
        updateNodeData={updateNodeData}
        isEstrai={isEstrai}
        groupByEnabled={groupByEnabled}
        availableGeomColumns={availableGeomColumns}
        hasGeometryColumns={hasGeometryColumns}
        isGeoFormat={isGeoFormat}
      >
        {/* Colonne Output (solo SQL/Estrai, senza GROUP BY attivo) — preview
            non output-setting; renderizzata come children del panel per
            preservare l'ordine visivo originale (filename → format →
            colonne preview → DISTINCT/LIMIT). */}
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
      </OutputSettingsPanel>

      <GroupOrderHavingPanel
        data={data}
        id={id}
        updateNodeData={updateNodeData}
        isEstrai={isEstrai}
        isManipulate={isManipulate}
        availableColumns={availableColumns}
        manipolaColumns={manipolaColumns}
      />

      <ExecuteActionsPanel
        data={data}
        id={id}
        nodeVariant={nodeVariant}
        executeLabel={executeLabel}
        hasExecutionOptions={hasExecutionOptions}
        isArricchisci={isArricchisci}
        hasOnPreview={hasOnPreview}
        hasOnExecute={hasOnExecute}
        onValidate={onValidate}
        onPreview={onPreview}
        onExecute={onExecute}
      />
    </>
  );
});

ResultNodeStandardView.displayName = 'ResultNodeStandardView';

export default ResultNodeStandardView;
