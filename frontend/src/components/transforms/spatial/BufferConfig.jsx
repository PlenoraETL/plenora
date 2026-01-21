/**
 * BufferConfig.jsx - Configurazione per operazione Buffer
 */
import React from 'react';

const BufferConfig = ({ 
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
      {/* Colonna geometria */}
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

      {/* Distanza */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Distanza (metri):</label>
        <input
          type="number"
          value={config.distance || 100}
          onChange={(e) => updateConfig('distance', parseFloat(e.target.value) || 0)}
          min="0"
          step="10"
          className="transform-node__input"
        />
      </div>

      {/* Nome colonna output */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Nome colonna output:</label>
        <input
          type="text"
          value={config.output_column || 'buffer_geom'}
          onChange={(e) => updateConfig('output_column', e.target.value)}
          placeholder="buffer_geom"
          className="transform-node__input"
        />
      </div>

      {/* Stile */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Stile bordo:</label>
        <select
          value={config.cap_style || 'round'}
          onChange={(e) => updateConfig('cap_style', e.target.value)}
          className="transform-node__select"
        >
          <option value="round">Arrotondato</option>
          <option value="flat">Piatto</option>
          <option value="square">Quadrato</option>
        </select>
      </div>

      {/* Confirmation */}
      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">🔵 Configurazione Buffer:</div>
        <div className="transform-node__confirm-content">
          • Distanza: {config.distance || 100}m<br/>
          • Output: {config.output_column || 'buffer_geom'}
        </div>
      </div>
    </div>
  );
};

export default BufferConfig;
