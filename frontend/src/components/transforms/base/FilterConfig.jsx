/**
 * FilterConfig.jsx - Configurazione per operazione Filter
 */
import React from 'react';
import { getColumnTypeIcon } from '../../../utils/columnUtils';

const FilterConfig = ({ 
  config, 
  updateConfig, 
  validation,
  inputColumns,
  nodeId
}) => {
  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        {validation.type === 'warning' ? '⚠️' : '❌'} {validation.message}
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      {/* Condizione */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Condizione filtro:</label>
        <textarea
          id={`filter-condition-${nodeId}`}
          value={config.condition || ''}
          onChange={(e) => updateConfig('condition', e.target.value)}
          placeholder='es: colonna > 100 & colonna2 == "valore"'
          className="transform-node__textarea"
          rows={3}
        />
      </div>

      {/* Colonne cliccabili */}
      <div className="transform-node__config-group">
        <div className="transform-node__columns-hint">Click per inserire:</div>
        <div className="transform-node__columns-list">
          {inputColumns.map((col, idx) => (
            <span
              key={idx}
              className="transform-node__column-chip"
              onClick={() => {
                const textarea = document.querySelector(`#filter-condition-${nodeId}`);
                if (textarea) {
                  const start = textarea.selectionStart;
                  const val = config.condition || '';
                  updateConfig('condition', val.substring(0, start) + col.name + val.substring(start));
                } else {
                  updateConfig('condition', (config.condition || '') + col.name);
                }
              }}
              title={col.type || col.dtype || 'str'}
            >
              {getColumnTypeIcon(col.type || col.dtype)} {col.name}
            </span>
          ))}
        </div>
      </div>

      {/* Help */}
      <div className="transform-node__help-box">
        <div className="transform-node__help-title">ℹ️ Operatori supportati:</div>
        <div className="transform-node__help-code">== != &gt; &lt; &gt;= &lt;= &amp; (AND) | (OR)</div>
        <div className="transform-node__help-hint">Stringhe: col.str.contains("x"), col.isna()</div>
      </div>

      {/* Confirmation */}
      {config.condition && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">✓ Configurazione Filtro:</div>
          <div className="transform-node__confirm-content">
            WHERE {config.condition}
          </div>
        </div>
      )}
    </div>
  );
};

export default FilterConfig;
