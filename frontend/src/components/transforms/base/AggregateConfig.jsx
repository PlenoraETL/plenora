/**
 * AggregateConfig.jsx - Configurazione per operazione Aggregate (GroupBy)
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
    { value: 'count', label: 'COUNT' },
    { value: 'sum', label: 'SUM' },
    { value: 'mean', label: 'AVG (media)' },
    { value: 'min', label: 'MIN' },
    { value: 'max', label: 'MAX' },
    { value: 'std', label: 'STD (dev. std)' },
    { value: 'first', label: 'FIRST' },
    { value: 'last', label: 'LAST' }
  ];

  return (
    <div className="transform-node__config">
      {/* Colonne Group By */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Raggruppa per (Group By):</label>
        <div className="transform-node__checkbox-list">
          {inputColumns.map((col, idx) => (
            <label key={idx} className="transform-node__checkbox-item">
              <input
                type="checkbox"
                checked={(config.group_by || []).includes(col.name)}
                onChange={(e) => {
                  const current = config.group_by || [];
                  let updated;
                  if (e.target.checked) {
                    updated = [...current, col.name];
                  } else {
                    updated = current.filter(c => c !== col.name);
                  }
                  updateConfig('group_by', updated);
                }}
              />
              <span>{col.name}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Aggregazioni */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Aggregazioni:</label>
        
        {(config.aggregations || []).map((agg, idx) => (
          <div key={idx} className="transform-node__agg-row">
            <select
              value={agg.column || ''}
              onChange={(e) => {
                const newAggs = [...(config.aggregations || [])];
                newAggs[idx] = { ...newAggs[idx], column: e.target.value };
                updateConfig('aggregations', newAggs);
              }}
              className="transform-node__config-select"
              style={{ flex: 1 }}
            >
              <option value="">-- Colonna --</option>
              {inputColumns.map((col, cidx) => (
                <option key={cidx} value={col.name}>{col.name}</option>
              ))}
            </select>
            
            <select
              value={agg.function || 'count'}
              onChange={(e) => {
                const newAggs = [...(config.aggregations || [])];
                newAggs[idx] = { ...newAggs[idx], function: e.target.value };
                updateConfig('aggregations', newAggs);
              }}
              className="transform-node__config-select"
              style={{ width: '100px' }}
            >
              {aggFunctions.map(af => (
                <option key={af.value} value={af.value}>{af.label}</option>
              ))}
            </select>
            
            <input
              type="text"
              value={agg.alias || ''}
              onChange={(e) => {
                const newAggs = [...(config.aggregations || [])];
                newAggs[idx] = { ...newAggs[idx], alias: e.target.value };
                updateConfig('aggregations', newAggs);
              }}
              placeholder="alias"
              className="transform-node__input"
              style={{ width: '80px' }}
            />
            
            <button
              onClick={() => {
                const newAggs = (config.aggregations || []).filter((_, i) => i !== idx);
                updateConfig('aggregations', newAggs);
              }}
              className="transform-node__btn-remove"
            >
              ✕
            </button>
          </div>
        ))}
        
        <button
          onClick={() => {
            const newAggs = [...(config.aggregations || []), { column: '', function: 'count', alias: '' }];
            updateConfig('aggregations', newAggs);
          }}
          className="transform-node__btn-add"
        >
          + Aggiungi aggregazione
        </button>
      </div>

      {/* Riepilogo */}
      {((config.group_by || []).length > 0 || (config.aggregations || []).length > 0) && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">📊 Configurazione Aggregate:</div>
          <div className="transform-node__confirm-content">
            Group By: {(config.group_by || []).join(', ') || 'nessuno'}<br/>
            Aggregazioni: {(config.aggregations || []).length}
          </div>
        </div>
      )}
    </div>
  );
};

export default AggregateConfig;
