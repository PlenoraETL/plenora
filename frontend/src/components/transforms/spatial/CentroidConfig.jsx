/**
 * CentroidConfig.jsx - Configurazione per operazione Centroide
 */
import React from 'react';

const CentroidConfig = ({ 
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
        <label className="transform-node__label">Nome colonna output:</label>
        <input
          type="text"
          value={config.output_column || 'centroid'}
          onChange={(e) => updateConfig('output_column', e.target.value)}
          placeholder="centroid"
          className="transform-node__input"
        />
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">📍 Configurazione Centroide:</div>
        <div className="transform-node__confirm-content">
          Colonna: {config.geom_column || geometryColumns[0]?.name || 'auto'}<br/>
          Output: {config.output_column || 'centroid'}
        </div>
      </div>
    </div>
  );
};

export default CentroidConfig;
