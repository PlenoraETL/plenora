/**
 * RenameConfig.jsx - Configurazione per operazione Rename
 */
import React from 'react';

const RenameConfig = ({ 
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
        <label className="transform-node__label">Colonna da rinominare:</label>
        <select
          value={config.old_name || ''}
          onChange={(e) => updateConfig('old_name', e.target.value)}
          className="transform-node__select"
        >
          <option value="">-- Seleziona --</option>
          {inputColumns.map((col, idx) => (
            <option key={idx} value={col.name}>{col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Nuovo nome:</label>
        <input
          type="text"
          value={config.new_name || ''}
          onChange={(e) => updateConfig('new_name', e.target.value)}
          placeholder="nuovo_nome"
          className="transform-node__input"
        />
      </div>

      {config.old_name && config.new_name && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">✏️ Rinomina:</div>
          <div className="transform-node__confirm-content">
            {config.old_name} → {config.new_name}
          </div>
        </div>
      )}
    </div>
  );
};

export default RenameConfig;
