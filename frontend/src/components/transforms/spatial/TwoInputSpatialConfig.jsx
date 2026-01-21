/**
 * TwoInputSpatialConfig.jsx - Configurazione per operazioni spaziali a 2 input
 * Usato per: Intersection, Union, Difference
 */
import React from 'react';

const TwoInputSpatialConfig = ({ 
  config, 
  updateConfig, 
  validation,
  leftGeomColumns,
  rightGeomColumns,
  operationType // 'intersection' | 'union' | 'difference'
}) => {
  const titles = {
    'geo_intersection': { icon: '∩', title: 'Intersezione', desc: 'Calcola intersezione geometrica' },
    'geo_union': { icon: '∪', title: 'Unione Geometrica', desc: 'Unisce le geometrie' },
    'geo_difference': { icon: '−', title: 'Differenza', desc: 'Calcola A - B' }
  };
  
  const opInfo = titles[operationType] || titles['geo_intersection'];

  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
        <div className="transform-node__message-hint">
          Collega 2 sorgenti con colonne geometriche
        </div>
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria 1° input:</label>
        <select
          value={config.left_geom || leftGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('left_geom', e.target.value)}
          className="transform-node__select"
        >
          {leftGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria 2° input:</label>
        <select
          value={config.right_geom || rightGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('right_geom', e.target.value)}
          className="transform-node__select"
        >
          {rightGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">{opInfo.icon} {opInfo.title}:</div>
        <div className="transform-node__confirm-content">
          {opInfo.desc}
        </div>
      </div>
    </div>
  );
};

export default TwoInputSpatialConfig;
