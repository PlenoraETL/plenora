/**
 * AggregateConfig.jsx - Configurazione per operazione Aggregate (GroupBy)
 * Versione migliorata con UI più pulita
 */
import React from 'react';

const AggregateConfig = ({ 
  config, 
  updateConfig, 
  validation,
  inputColumns
}) => {
  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
      </div>
    );
  }

  const aggFunctions = [
    { value: 'count', label: 'COUNT', icon: '#️⃣' },
    { value: 'sum', label: 'SUM', icon: '➕' },
    { value: 'mean', label: 'AVG', icon: '📊' },
    { value: 'min', label: 'MIN', icon: '⬇️' },
    { value: 'max', label: 'MAX', icon: '⬆️' },
    { value: 'first', label: 'FIRST', icon: '1️⃣' },
    { value: 'last', label: 'LAST', icon: '🔚' }
  ];

  const groupByColumns = config.group_by || [];
  const aggregations = config.aggregations || [];

  const toggleGroupBy = (colName) => {
    const current = [...groupByColumns];
    const idx = current.indexOf(colName);
    if (idx >= 0) {
      current.splice(idx, 1);
    } else {
      current.push(colName);
    }
    updateConfig('group_by', current);
  };

  const updateAggregation = (idx, field, value) => {
    const newAggs = [...aggregations];
    newAggs[idx] = { ...newAggs[idx], [field]: value };
    updateConfig('aggregations', newAggs);
  };

  const removeAggregation = (idx) => {
    const newAggs = aggregations.filter((_, i) => i !== idx);
    updateConfig('aggregations', newAggs);
  };

  const addAggregation = () => {
    const newAggs = [...aggregations, { column: '', function: 'count' }];
    updateConfig('aggregations', newAggs);
  };

  return (
    <div className="transform-node__config">
      {/* Colonne Group By - come chips selezionabili */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">📁 Raggruppa per:</label>
        <div className="transform-node__chips-container">
          {inputColumns.map((col, idx) => {
            const isSelected = groupByColumns.includes(col.name);
            return (
              <button
                key={idx}
                type="button"
                className={`transform-node__chip ${isSelected ? 'transform-node__chip--selected' : ''}`}
                onClick={() => toggleGroupBy(col.name)}
              >
                {isSelected && <span className="transform-node__chip-check">✓</span>}
                {col.name}
              </button>
            );
          })}
        </div>
        {groupByColumns.length > 0 && (
          <div className="transform-node__selected-info">
            Selezionati: {groupByColumns.join(', ')}
          </div>
        )}
      </div>

      {/* Aggregazioni */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">📈 Aggregazioni:</label>
        
        <div className="transform-node__agg-list">
          {aggregations.map((agg, idx) => (
            <div key={idx} className="transform-node__agg-card">
              <div className="transform-node__agg-card-header">
                <span className="transform-node__agg-card-num">#{idx + 1}</span>
                <button
                  type="button"
                  onClick={() => removeAggregation(idx)}
                  className="transform-node__agg-card-remove"
                  title="Rimuovi"
                >
                  ✕
                </button>
              </div>
              
              <div className="transform-node__agg-card-body">
                <div className="transform-node__agg-field">
                  <label>Colonna:</label>
                  <select
                    value={agg.column || ''}
                    onChange={(e) => updateAggregation(idx, 'column', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Seleziona --</option>
                    {inputColumns.map((col, cidx) => (
                      <option key={cidx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>
                
                <div className="transform-node__agg-field">
                  <label>Funzione:</label>
                  <select
                    value={agg.function || 'count'}
                    onChange={(e) => updateAggregation(idx, 'function', e.target.value)}
                    className="transform-node__select"
                  >
                    {aggFunctions.map(af => (
                      <option key={af.value} value={af.value}>
                        {af.icon} {af.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          ))}
        </div>
        
        <button
          type="button"
          onClick={addAggregation}
          className="transform-node__add-btn"
        >
          + Aggiungi aggregazione
        </button>
      </div>

      {/* Riepilogo */}
      {(groupByColumns.length > 0 || aggregations.length > 0) && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">📊 Riepilogo:</div>
          <div className="transform-node__confirm-content">
            {groupByColumns.length > 0 && (
              <div>GROUP BY: {groupByColumns.join(', ')}</div>
            )}
            {aggregations.filter(a => a.column).map((agg, idx) => (
              <div key={idx}>
                {agg.function?.toUpperCase()}({agg.column})
              </div>
            ))}
            {aggregations.length === 0 && groupByColumns.length > 0 && (
              <div>COUNT(*) automatico</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default AggregateConfig;