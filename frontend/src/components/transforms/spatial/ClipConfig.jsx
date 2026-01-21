/**
 * ClipConfig.jsx - Configurazione per operazione Clip
 */
import React from 'react';

const ClipConfig = ({ 
  config, 
  updateConfig, 
  validation,
  leftGeomColumns,
  rightGeomColumns
}) => {
  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
        <div className="transform-node__message-hint">
          1° input: layer da ritagliare<br/>
          2° input: maschera di ritaglio
        </div>
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria da ritagliare (1° input):</label>
        <select
          value={config.input_geom || leftGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('input_geom', e.target.value)}
          className="transform-node__select"
        >
          {leftGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Maschera (2° input):</label>
        <select
          value={config.mask_geom || rightGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('mask_geom', e.target.value)}
          className="transform-node__select"
        >
          {rightGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">✂️ Configurazione Clip:</div>
        <div className="transform-node__confirm-content">
          Ritaglia geometrie usando la maschera
        </div>
      </div>
    </div>
  );
};

export default ClipConfig;
