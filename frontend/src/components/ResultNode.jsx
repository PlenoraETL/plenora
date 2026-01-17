import React, { useState, memo } from 'react';
import { Handle, Position } from 'reactflow';
import { getColumnTypeIcon, getColumnTypeLabel, getColumnSRID, isGeometryType } from '../utils/columnUtils';
import '../styles/ResultNode.css';

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
 * - data.onUpdateFileName: callback modifica nome
 * - data.onUpdateFormat: callback modifica formato
 * - data.onToggleDistinct: callback toggle DISTINCT
 * - data.onUpdateLimit: callback modifica LIMIT
 * - data.onUpdateGroupBy: callback modifica GROUP BY config
 * - data.onExecute: callback esecuzione
 * - data.onDelete: callback eliminazione
 */
const ResultNode = memo(({ data, id }) => {
  const [isEditingFileName, setIsEditingFileName] = useState(false);
  const [tempFileName, setTempFileName] = useState(data.fileName || 'output');
  const [executionMode, setExecutionMode] = useState('cache');
  const [showGroupBySection, setShowGroupBySection] = useState(false);

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

  // Verifica se il formato selezionato è geospaziale
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Estrai colonne geometriche disponibili
  const availableGeomColumns = (data.availableColumns || [])
    .filter(col => isGeometryType(col.type))
    .map(col => col.name || col.alias || col);

  // Estrai tutte le colonne disponibili per GROUP BY (da data.columns per ESTRAI)
  const availableColumns = (data.columns || []).map(col => ({
    name: col.alias || col.name,
    type: col.type || 'str',
    fullName: col.full_name || `${col.source_table}.${col.alias || col.name}`
  }));

  // Colonne disponibili per MANIPOLA (da data.availableColumns)
  const manipolaColumns = (data.availableColumns || []).map(col => ({
    name: col.name || col,
    type: col.type || 'str',
    fullName: col.name || col
  }));

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
    if (data.onUpdateFileName) {
      data.onUpdateFileName(id, tempFileName.trim() || 'output');
    }
    setIsEditingFileName(false);
  };

  const handleChangeFormat = (newFormat) => {
    if (data.onUpdateFormat) {
      data.onUpdateFormat(id, newFormat);
    }
  };

  const handleChangeGeomColumn = (columnName) => {
    if (data.onUpdateGeomColumn) {
      data.onUpdateGeomColumn(id, columnName);
    }
  };

  const handleToggleDistinct = () => {
    if (data.onToggleDistinct) {
      data.onToggleDistinct(id, !distinct);
    }
  };

  const handleUpdateLimit = (newLimit) => {
    if (data.onUpdateLimit) {
      const limitValue = newLimit === '' ? null : parseInt(newLimit) || null;
      data.onUpdateLimit(id, limitValue);
    }
  };

  // GROUP BY Handlers
  const handleToggleGroupBy = () => {
    if (data.onUpdateGroupBy) {
      data.onUpdateGroupBy(id, {
        group_by_enabled: !groupByEnabled,
        group_by_columns: groupByEnabled ? [] : groupByColumns,
        aggregations: groupByEnabled ? [] : aggregations
      });
    }
  };

  const handleAddGroupByColumn = (columnName) => {
    if (!columnName || groupByColumns.includes(columnName)) return;
    if (data.onUpdateGroupBy) {
      data.onUpdateGroupBy(id, {
        group_by_enabled: true,
        group_by_columns: [...groupByColumns, columnName],
        aggregations
      });
    }
  };

  const handleRemoveGroupByColumn = (columnName) => {
    if (data.onUpdateGroupBy) {
      data.onUpdateGroupBy(id, {
        group_by_enabled: groupByEnabled,
        group_by_columns: groupByColumns.filter(c => c !== columnName),
        aggregations
      });
    }
  };

  const handleAddAggregation = () => {
    if (data.onUpdateGroupBy) {
      const newAgg = {
        column: '*',
        function: 'COUNT',
        alias: `count_${aggregations.length + 1}`
      };
      data.onUpdateGroupBy(id, {
        group_by_enabled: true,
        group_by_columns: groupByColumns,
        aggregations: [...aggregations, newAgg]
      });
    }
  };

  const handleUpdateAggregation = (index, field, value) => {
    if (data.onUpdateGroupBy) {
      const newAggregations = [...aggregations];
      newAggregations[index] = { ...newAggregations[index], [field]: value };
      
      // Auto-genera alias se cambiano colonna o funzione
      if (field === 'column' || field === 'function') {
        const agg = newAggregations[index];
        const colName = agg.column === '*' ? 'all' : agg.column;
        newAggregations[index].alias = `${agg.function.toLowerCase()}_${colName}`;
      }
      
      data.onUpdateGroupBy(id, {
        group_by_enabled: groupByEnabled,
        group_by_columns: groupByColumns,
        aggregations: newAggregations
      });
    }
  };

  const handleRemoveAggregation = (index) => {
    if (data.onUpdateGroupBy) {
      data.onUpdateGroupBy(id, {
        group_by_enabled: groupByEnabled,
        group_by_columns: groupByColumns,
        aggregations: aggregations.filter((_, i) => i !== index)
      });
    }
  };

  const handleDelete = () => {
    if (data.onDelete && confirm('Eliminare il nodo risultato?')) {
      data.onDelete(id);
    }
  };

  const handleExecute = () => {
    if (data.onExecute) {
      const options = hasExecutionOptions 
        ? { force_recalculate: executionMode === 'recalculate' }
        : {};
      data.onExecute(id, options);
    }
  };

  return (
    <div className={`result-node result-node--${nodeVariant}`}>
      {/* Handle input */}
      <Handle
        type="target"
        position={Position.Left}
        className="result-node__handle"
      />

      {/* Header */}
      <div className="result-node__header">
        <div className="result-node__title">
          <span className="result-node__title-icon">{icon}</span>
          <span>{title}</span>
        </div>
        <button
          onClick={handleDelete}
          className="result-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="result-node__body">
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
              <button
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
          {/* Formati geospaziali - solo per Manipola */}
          {isManipulate && (
            <>
              <div className="result-node__label result-node__label--geo">🌐 Formati geospaziali:</div>
              <div className="result-node__format-group">
                {geoFormats.map(fmt => (
                  <button
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
            <div className="result-node__label result-node__label--with-badge">
              <span>📋 Colonne output:</span>
              <span className="result-node__columns-badge">
                {data.columns.length}
              </span>
            </div>
            <div className="result-node__columns-list">
              {data.columns.map((col, idx) => (
                <div key={idx} className="result-node__column-item">
                  <span 
                    className={`result-node__column-type-icon ${isGeometryType(col.type) ? 'result-node__column-type-icon--geo' : ''}`}
                    title={getColumnTypeLabel(col.type)}
                  >
                    {getColumnTypeIcon(col.type)}
                  </span>
                  <span className="result-node__column-table">
                    {col.source_table}
                  </span>
                  <span className="result-node__column-separator">.</span>
                  <span className="result-node__column-name">
                    {col.alias || col.name}
                  </span>
                  {isGeometryType(col.type) && getColumnSRID(col.type) && (
                    <span className="result-node__column-srid-badge" title={`EPSG:${getColumnSRID(col.type)}`}>
                      {getColumnSRID(col.type)}
                    </span>
                  )}
                </div>
              ))}
            </div>
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
                              <button 
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
                          <button
                            onClick={() => handleRemoveAggregation(idx)}
                            className="result-node__agg-remove"
                            title="Rimuovi aggregazione"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                      
                      {/* Bottone aggiungi aggregazione */}
                      <button
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
          {/* Bottone Preview (ESTRAI e MANIPOLA) */}
          {(isEstrai || isManipulate) && data.onPreview && (
            <button
              onClick={() => data.onPreview(id)}
              className={`result-node__preview-btn result-node__preview-btn--${nodeVariant}`}
            >
              <span className="result-node__preview-icon">👁️</span>
              <span>Preview</span>
            </button>
          )}

          {/* Bottone Esegui */}
          {data.onExecute && (
            <button
              onClick={handleExecute}
              className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
            >
              <span className="result-node__execute-icon">▶️</span>
              <span>{executeLabel}</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

ResultNode.displayName = 'ResultNode';

export default ResultNode;