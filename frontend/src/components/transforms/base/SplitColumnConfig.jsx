/**
 * SplitColumnConfig.jsx - Configurazione per operazione Split Column
 */
import React from 'react';

const SplitColumnConfig = ({ 
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
        <label className="transform-node__label">Colonna da dividere:</label>
        <select
          value={config.column || ''}
          onChange={(e) => updateConfig('column', e.target.value)}
          className="transform-node__select"
        >
          <option value="">-- Seleziona --</option>
          {inputColumns.map((col, idx) => (
            <option key={idx} value={col.name}>{col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Delimitatore:</label>
        <input
          type="text"
          value={config.delimiter || ','}
          onChange={(e) => updateConfig('delimiter', e.target.value)}
          placeholder=","
          className="transform-node__input"
        />
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Nomi nuove colonne (separati da virgola):</label>
        <input
          type="text"
          value={(config.new_columns || []).join(', ')}
          onChange={(e) => updateConfig('new_columns', e.target.value.split(',').map(s => s.trim()).filter(s => s))}
          placeholder="parte1, parte2, parte3"
          className="transform-node__input"
        />
      </div>

      {config.column && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">✂️ Dividi Colonna:</div>
          <div className="transform-node__confirm-content">
            {config.column} → separato da "{config.delimiter || ','}"
          </div>
        </div>
      )}
    </div>
  );
};

export default SplitColumnConfig;
