/**
 * ResultNodeStandardView — view del ResultNode per il ramo NON-IMDF
 * (upstream qualsiasi diverso da api_enrich con output_kind='imdf').
 *
 * Estratto da ResultNode.jsx (Batch 2 step 3 split FE). Il componente si
 * monta solo quando isImdfMode=false (ramo ternario del parent); non ha
 * una prop isImdfMode perche' la sua presenza nel tree lo implica.
 *
 * Owna:
 *   - dispatch a `OutputSettingsPanel` (filename + format + geo + distinct + limit)
 *     dopo l'estrazione `FE-SPLIT-005A`;
 *   - preview colonne output con expand (columnsExpanded), passata come children
 *     al panel per preservare l'ordine visivo;
 *   - GROUP BY / ORDER BY / HAVING: tutti gli handler su updateNodeData;
 *   - modalita' esecuzione cache|recalculate (MANIPOLA/ARRICCHISCI);
 *   - bottoni Valida / Preview / Esegui / Cronologia;
 *   - RunHistoryModal (trigger + stato open/close);
 *   - deriva localmente nodeVariant/executeLabel da data.isManipulate/isArricchisci.
 *
 * NON owna:
 *   - editor filename + scelta formato + DISTINCT + LIMIT: estratti in
 *     `OutputSettingsPanel` (FE-SPLIT-005A); il parent passa i flag derivati
 *     `isGeoFormat`, `availableGeomColumns`, `hasGeometryColumns` come props;
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
import RunHistoryModal from '../../modals/RunHistoryModal';
import OutputSettingsPanel from './standard/OutputSettingsPanel';

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

  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const [executionMode, setExecutionMode] = useState('cache');
  const [showGroupBySection, setShowGroupBySection] = useState(false);
  const [showOrderBySection, setShowOrderBySection] = useState(false);
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

  // GROUP BY state (data-driven)
  const groupByEnabled = data.group_by_enabled || false;
  const groupByColumns = data.group_by_columns || [];
  const aggregations = data.aggregations || [];

  // ORDER BY state
  const orderByEnabled = data.order_by_enabled || false;
  const orderByColumns = data.order_by_columns || [];

  // HAVING state
  const havingEnabled = data.having_enabled || false;
  const havingConditions = data.having_conditions || [];

  // Formato geospaziale?
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Colonne geometriche candidate (MANIPOLA/ARRICCHISCI: upstream; ESTRAI: merge columns+upstream)
  const geomSourceColumns = (() => {
    if (isManipulate || isArricchisci) {
      return upstreamColumns.length > 0 ? upstreamColumns : (data.availableColumns || []);
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

  const groupByAvailableColumns = isManipulate ? manipolaColumns : availableColumns;

  // Variante/label derivate localmente
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  const config = {
    estrai: { executeLabel: 'Esegui Query' },
    manipola: { executeLabel: 'Esegui Pipeline' },
    arricchisci: { executeLabel: 'Esegui Arricchimento' },
  };
  const { executeLabel } = config[nodeVariant];

  const aggregateFunctions = [
    { value: 'COUNT', label: 'COUNT', desc: 'Conta' },
    { value: 'SUM', label: 'SUM', desc: 'Somma' },
    { value: 'AVG', label: 'AVG', desc: 'Media' },
    { value: 'MIN', label: 'MIN', desc: 'Minimo' },
    { value: 'MAX', label: 'MAX', desc: 'Massimo' },
  ];

  // ── Handlers ─────────────────────────────────────────────────────────
  // I 5 handler output-settings (handleSaveFileName / handleChangeFormat /
  // handleChangeGeomColumn / handleToggleDistinct / handleUpdateLimit)
  // sono migrati dentro `OutputSettingsPanel` con `FE-SPLIT-005A`.
  const handleToggleGroupBy = () => {
    updateNodeData(id, {
      group_by_enabled: !groupByEnabled,
      group_by_columns: groupByEnabled ? [] : groupByColumns,
      aggregations: groupByEnabled ? [] : aggregations,
    });
  };
  const handleAddGroupByColumn = (columnName) => {
    if (!columnName || groupByColumns.includes(columnName)) return;
    updateNodeData(id, {
      group_by_enabled: true,
      group_by_columns: [...groupByColumns, columnName],
      aggregations,
    });
  };
  const handleRemoveGroupByColumn = (columnName) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns.filter(c => c !== columnName),
      aggregations,
    });
  };
  const handleAddAggregation = () => {
    const newAgg = {
      column: '*',
      function: 'COUNT',
      alias: `count_${aggregations.length + 1}`,
    };
    updateNodeData(id, {
      group_by_enabled: true,
      group_by_columns: groupByColumns,
      aggregations: [...aggregations, newAgg],
    });
  };
  const handleUpdateAggregation = (index, field, value) => {
    const newAggregations = [...aggregations];
    newAggregations[index] = { ...newAggregations[index], [field]: value };
    if (field === 'column' || field === 'function') {
      const agg = newAggregations[index];
      const colName = agg.column === '*' ? 'all' : agg.column;
      newAggregations[index].alias = `${agg.function.toLowerCase()}_${colName}`;
    }
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns,
      aggregations: newAggregations,
    });
  };
  const handleRemoveAggregation = (index) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns,
      aggregations: aggregations.filter((_, i) => i !== index),
    });
  };

  const handleToggleOrderBy = () => {
    updateNodeData(id, {
      order_by_enabled: !orderByEnabled,
      order_by_columns: orderByEnabled ? [] : orderByColumns,
    });
  };
  const handleAddOrderByColumn = (columnName) => {
    if (!columnName || orderByColumns.some(c => c.column === columnName)) return;
    updateNodeData(id, {
      order_by_enabled: true,
      order_by_columns: [...orderByColumns, { column: columnName, direction: 'ASC' }],
    });
  };
  const handleRemoveOrderByColumn = (index) => {
    updateNodeData(id, {
      order_by_columns: orderByColumns.filter((_, i) => i !== index),
    });
  };
  const handleToggleOrderByDirection = (index) => {
    const newColumns = [...orderByColumns];
    newColumns[index] = {
      ...newColumns[index],
      direction: newColumns[index].direction === 'ASC' ? 'DESC' : 'ASC',
    };
    updateNodeData(id, { order_by_columns: newColumns });
  };

  const handleToggleHaving = () => {
    updateNodeData(id, {
      having_enabled: !havingEnabled,
      having_conditions: havingEnabled ? [] : havingConditions,
    });
  };
  const handleAddHavingCondition = () => {
    updateNodeData(id, {
      having_enabled: true,
      having_conditions: [...havingConditions, {
        function: 'COUNT', column: '*', operator: '>', value: '0',
      }],
    });
  };
  const handleUpdateHavingCondition = (index, field, value) => {
    const newConditions = [...havingConditions];
    newConditions[index] = { ...newConditions[index], [field]: value };
    updateNodeData(id, { having_conditions: newConditions });
  };
  const handleRemoveHavingCondition = (index) => {
    updateNodeData(id, {
      having_conditions: havingConditions.filter((_, i) => i !== index),
    });
  };

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

      {/* GROUP BY — ESTRAI e MANIPOLA */}
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
                  <div className="result-node__groupby-section">
                    <div className="result-node__label">Raggruppa per:</div>

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

                  <div className="result-node__groupby-section">
                    <div className="result-node__label">Aggregazioni:</div>

                    {aggregations.map((agg, idx) => (
                      <div key={idx} className="result-node__aggregation-row">
                        <select
                          value={agg.function}
                          onChange={(e) => handleUpdateAggregation(idx, 'function', e.target.value)}
                          className="result-node__agg-select result-node__agg-select--func"
                        >
                          {aggregateFunctions.map(f => (
                            <option key={f.value} value={f.value}>{f.label}</option>
                          ))}
                        </select>

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

                        <input
                          type="text"
                          value={agg.alias || ''}
                          onChange={(e) => handleUpdateAggregation(idx, 'alias', e.target.value)}
                          placeholder="alias"
                          className="result-node__agg-alias"
                        />

                        <button type="button"
                          onClick={() => handleRemoveAggregation(idx)}
                          className="result-node__agg-remove"
                          title="Rimuovi aggregazione"
                        >
                          ×
                        </button>
                      </div>
                    ))}

                    <button type="button"
                      onClick={handleAddAggregation}
                      className="result-node__add-agg-btn"
                    >
                      + Aggiungi aggregazione
                    </button>
                  </div>

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

      {/* ORDER BY — ESTRAI e MANIPOLA */}
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

      {/* HAVING — solo quando GROUP BY attivo e ci sono aggregazioni */}
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
                {havingConditions.map((cond, idx) => (
                  <div key={idx} className="result-node__aggregation-row">
                    <select
                      value={cond.function}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'function', e.target.value)}
                      className="result-node__agg-select result-node__agg-select--func"
                    >
                      {aggregateFunctions.map(f => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>

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

                    <input
                      type="text"
                      value={cond.value}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'value', e.target.value)}
                      placeholder="valore"
                      className="result-node__agg-alias"
                    />

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

      {/* Modalita' Esecuzione (Manipola/Arricchisci) */}
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
        <button type="button"
          onClick={onValidate}
          className="result-node__validate-btn"
          title="Valida il canvas prima di eseguire"
        >
          <span>🔍</span>
          <span>Valida</span>
        </button>

        {hasOnPreview && (
          <button type="button"
            onClick={onPreview}
            className={`result-node__preview-btn result-node__preview-btn--${nodeVariant}`}
          >
            <span className="result-node__preview-icon">👁️</span>
            <span>Preview</span>
          </button>
        )}

        {hasOnExecute && (
          <button type="button"
            onClick={() => onExecute({ force_recalculate: executionMode === 'recalculate' })}
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
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }}
          onMouseDown={(e) => e.stopPropagation()}
          className="result-node__history-btn"
          title="Cronologia esecuzioni"
        >
          <span>🕐</span>
          <span>Cronologia run</span>
        </button>
      </div>

      {/* Modal cronologia run */}
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={data.project_id || data.canvas_id}
        nodeId={id}
        nodeLabel="Output"
      />
    </>
  );
});

ResultNodeStandardView.displayName = 'ResultNodeStandardView';

export default ResultNodeStandardView;
