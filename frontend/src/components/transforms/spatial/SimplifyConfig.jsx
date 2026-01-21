/**
 * SimplifyConfig.jsx - Configurazione per operazione Simplify
 */
import React from 'react';

const SimplifyConfig = ({ 
  config, 
  updateConfig, 
  validation,
  geometryColumns
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
        <label className="transform-node__label">Colonna geometria:</label>
        <select
          value={config.geom_column || geometryColumns[0]?.name || ''}
          onChange={(e) => updateConfig('geom_column', e.target.value)}
          className="transform-node__select"
        >
          {geometryColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Tolleranza (gradi o metri):</label>
        <input
          type="number"
          value={config.tolerance || 0.001}
          onChange={(e) => updateConfig('tolerance', parseFloat(e.target.value) || 0.001)}
          min="0"
          step="0.0001"
          className="transform-node__input"
        />
        <div className="transform-node__help-hint">
          Per WGS84: ~0.00001 = ~1m
        </div>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__checkbox-inline">
          <input
            type="checkbox"
            checked={config.preserve_topology !== false}
            onChange={(e) => updateConfig('preserve_topology', e.target.checked)}
          />
          Preserva topologia
        </label>
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">〰️ Configurazione Simplify:</div>
        <div className="transform-node__confirm-content">
          Tolleranza: {config.tolerance || 0.001}
        </div>
      </div>
    </div>
  );
};

export default SimplifyConfig;
