/**
 * DissolveConfig.jsx - Configurazione per operazione Dissolve
 */
import React from 'react';

const DissolveConfig = ({ 
  config, 
  updateConfig, 
  validation,
  geometryColumns,
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
        <label className="transform-node__label">Raggruppa per (opzionale):</label>
        <select
          value={config.by_column || ''}
          onChange={(e) => updateConfig('by_column', e.target.value)}
          className="transform-node__select"
        >
          <option value="">-- Dissolvi tutto --</option>
          {inputColumns.filter(c => !c.type?.startsWith('geometry')).map((col, idx) => (
            <option key={idx} value={col.name}>{col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">🔗 Configurazione Dissolve:</div>
        <div className="transform-node__confirm-content">
          {config.by_column ? `Raggruppa per: ${config.by_column}` : 'Dissolvi tutte le geometrie'}
        </div>
      </div>
    </div>
  );
};

export default DissolveConfig;
