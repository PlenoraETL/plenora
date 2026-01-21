/**
 * SpatialJoinConfig.jsx - Configurazione per Spatial Join
 */
import React from 'react';

const SpatialJoinConfig = ({ 
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
          Collega 2 sorgenti dati con colonne geometriche.
        </div>
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      {/* Colonna geometria sinistra */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria sinistra (1° input):</label>
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

      {/* Colonna geometria destra */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria destra (2° input):</label>
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

      {/* Predicato spaziale */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Predicato spaziale:</label>
        <select
          value={config.predicate || 'intersects'}
          onChange={(e) => updateConfig('predicate', e.target.value)}
          className="transform-node__select"
        >
          <option value="intersects">Intersects (si toccano/sovrappongono)</option>
          <option value="within">Within (A dentro B)</option>
          <option value="contains">Contains (A contiene B)</option>
          <option value="touches">Touches (si toccano al bordo)</option>
          <option value="crosses">Crosses (si incrociano)</option>
          <option value="overlaps">Overlaps (si sovrappongono parzialmente)</option>
        </select>
      </div>

      {/* Tipo join */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Tipo join:</label>
        <select
          value={config.how || 'inner'}
          onChange={(e) => updateConfig('how', e.target.value)}
          className="transform-node__select"
        >
          <option value="inner">Inner (solo match)</option>
          <option value="left">Left (tutti da sinistra)</option>
          <option value="right">Right (tutti da destra)</option>
        </select>
      </div>

      {/* Confirmation */}
      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">🌍 Configurazione Spatial Join:</div>
        <div className="transform-node__confirm-content">
          • Predicato: {config.predicate || 'intersects'}<br/>
          • Tipo: {config.how || 'inner'}
        </div>
      </div>
    </div>
  );
};

export default SpatialJoinConfig;
