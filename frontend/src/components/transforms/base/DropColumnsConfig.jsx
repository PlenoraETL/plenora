/**
 * DropColumnsConfig.jsx - Configurazione per operazione Drop Columns
 */
import React from 'react';

const DropColumnsConfig = ({ 
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

  return (
    <div className="transform-node__config">
      <div className="transform-node__config-group">
        <label className="transform-node__label">Colonne da eliminare:</label>
        <div className="transform-node__checkbox-list">
          {inputColumns.map((col, idx) => (
            <label key={idx} className="transform-node__checkbox-item">
              <input
                type="checkbox"
                checked={(config.columns || []).includes(col.name)}
                onChange={(e) => {
                  const current = config.columns || [];
                  let updated;
                  if (e.target.checked) {
                    updated = [...current, col.name];
                  } else {
                    updated = current.filter(c => c !== col.name);
                  }
                  updateConfig('columns', updated);
                }}
              />
              <span>{col.name}</span>
            </label>
          ))}
        </div>
      </div>

      {(config.columns || []).length > 0 && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">🗑️ Elimina colonne:</div>
          <div className="transform-node__confirm-content">
            {(config.columns || []).join(', ')}<br/>
            Output: {inputColumns.length - (config.columns || []).length} colonne
          </div>
        </div>
      )}
    </div>
  );
};

export default DropColumnsConfig;
