/**
 * DistinctConfig.jsx - Configurazione per operazione Distinct
 */
import React from 'react';

const DistinctConfig = ({ 
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
      {/* Modalità */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Considera duplicati su:</label>
        <select
          value={config.mode || 'all'}
          onChange={(e) => {
            updateConfig('mode', e.target.value);
            if (e.target.value === 'all') {
              updateConfig('subset', null);
            }
          }}
          className="transform-node__config-select"
        >
          <option value="all">Tutte le colonne</option>
          <option value="subset">Solo alcune colonne</option>
        </select>
      </div>

      {/* Selezione colonne subset */}
      {config.mode === 'subset' && (
        <div className="transform-node__config-group">
          <label className="transform-node__label">Colonne da considerare:</label>
          <div className="transform-node__checkbox-list">
            {inputColumns.map((col, idx) => (
              <label key={idx} className="transform-node__checkbox-item">
                <input
                  type="checkbox"
                  checked={(config.subset || []).includes(col.name)}
                  onChange={(e) => {
                    const currentSubset = config.subset || [];
                    let newSubset;
                    if (e.target.checked) {
                      newSubset = [...currentSubset, col.name];
                    } else {
                      newSubset = currentSubset.filter(c => c !== col.name);
                    }
                    updateConfig('subset', newSubset);
                  }}
                />
                <span>{col.name}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Keep mode */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">In caso di duplicati, mantieni:</label>
        <select
          value={config.keep || 'first'}
          onChange={(e) => updateConfig('keep', e.target.value)}
          className="transform-node__config-select"
        >
          <option value="first">Prima occorrenza</option>
          <option value="last">Ultima occorrenza</option>
          <option value="false">Nessuna (rimuovi tutti)</option>
        </select>
      </div>

      {/* Riepilogo */}
      <div className="transform-node__confirm-box">
        <div className="transform-node__confirm-title">🔹 Configurazione Distinct:</div>
        <div className="transform-node__confirm-content">
          Duplicati su: {config.mode === 'subset' ? (config.subset || []).join(', ') || 'nessuna colonna' : 'tutte le colonne'}<br/>
          Mantieni: {config.keep === 'first' ? 'prima' : config.keep === 'last' ? 'ultima' : 'nessuna'}
        </div>
      </div>
    </div>
  );
};

export default DistinctConfig;
