/**
 * ReplaceConfig.jsx - Configurazione per operazione Replace
 */
import React from 'react';

const ReplaceConfig = ({ 
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
        <label className="transform-node__label">Colonna:</label>
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
        <label className="transform-node__label">Valore da cercare:</label>
        <input
          type="text"
          value={config.old_value || ''}
          onChange={(e) => updateConfig('old_value', e.target.value)}
          placeholder="vecchio valore"
          className="transform-node__input"
        />
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Sostituisci con:</label>
        <input
          type="text"
          value={config.new_value || ''}
          onChange={(e) => updateConfig('new_value', e.target.value)}
          placeholder="nuovo valore"
          className="transform-node__input"
        />
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__checkbox-inline">
          <input
            type="checkbox"
            checked={config.regex || false}
            onChange={(e) => updateConfig('regex', e.target.checked)}
          />
          Usa espressione regolare
        </label>
      </div>

      {config.column && config.old_value && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">🔄 Sostituzione:</div>
          <div className="transform-node__confirm-content">
            {config.column}: "{config.old_value}" → "{config.new_value || ''}"
            {config.regex && ' (regex)'}
          </div>
        </div>
      )}
    </div>
  );
};

export default ReplaceConfig;
