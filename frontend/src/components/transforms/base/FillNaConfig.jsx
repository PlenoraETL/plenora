/**
 * FillNaConfig.jsx - Configurazione per operazione Fill NA (riempi nulli)
 */
import React from 'react';

const FillNaConfig = ({ 
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
        <label className="transform-node__label">Colonna (vuoto = tutte):</label>
        <select
          value={config.column || ''}
          onChange={(e) => updateConfig('column', e.target.value)}
          className="transform-node__select"
        >
          <option value="">-- Tutte le colonne --</option>
          {inputColumns.map((col, idx) => (
            <option key={idx} value={col.name}>{col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Metodo:</label>
        <select
          value={config.method || 'value'}
          onChange={(e) => updateConfig('method', e.target.value === 'value' ? null : e.target.value)}
          className="transform-node__select"
        >
          <option value="value">Valore fisso</option>
          <option value="ffill">Propaga avanti (ffill)</option>
          <option value="bfill">Propaga indietro (bfill)</option>
        </select>
      </div>

      {!config.method && (
        <div className="transform-node__config-group">
          <label className="transform-node__label">Valore sostitutivo:</label>
          <input
            type="text"
            value={config.value || ''}
            onChange={(e) => updateConfig('value', e.target.value)}
            placeholder="0 oppure N/A"
            className="transform-node__input"
          />
        </div>
      )}

      <div className="transform-node__confirm-box">
        <div className="transform-node__confirm-title">🔧 Riempi valori nulli</div>
        <div className="transform-node__confirm-content">
          Colonna: {config.column || 'tutte'}<br/>
          Metodo: {config.method || `valore "${config.value || ''}"`}
        </div>
      </div>
    </div>
  );
};

export default FillNaConfig;
