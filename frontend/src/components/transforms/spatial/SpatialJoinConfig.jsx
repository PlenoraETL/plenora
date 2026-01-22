/**
 * SpatialJoinConfig.jsx - Configurazione per Spatial Join
 * 
 * AGGIORNATO 2026-01-22: Aggiunto SourceSwapper per controllare ordine LEFT/RIGHT
 * Posizione: /components/transforms/spatial/SpatialJoinConfig.jsx
 */
import React from 'react';
import SourceSwapper from '../SourceSwapper';

const SpatialJoinConfig = ({ 
  config, 
  updateConfig, 
  validation,
  leftNode,
  rightNode,
  leftColumns,
  rightColumns,
  leftGeomColumns,
  rightGeomColumns,
  getNodeLabel
}) => {
  // Gestione swap
  const swapped = config.swapped || false;
  
  // Se swapped, invertiamo le colonne per i dropdown
  const displayLeftGeomColumns = swapped ? rightGeomColumns : leftGeomColumns;
  const displayRightGeomColumns = swapped ? leftGeomColumns : rightGeomColumns;
  const displayLeftCount = swapped 
    ? (rightColumns?.length || rightGeomColumns?.length || 0) 
    : (leftColumns?.length || leftGeomColumns?.length || 0);
  const displayRightCount = swapped 
    ? (leftColumns?.length || leftGeomColumns?.length || 0) 
    : (rightColumns?.length || rightGeomColumns?.length || 0);

  const handleSwap = () => {
    updateConfig('swapped', !swapped);
  };

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
      {/* Source Swapper */}
      <SourceSwapper
        leftNode={leftNode}
        rightNode={rightNode}
        leftLabel="L"
        rightLabel="R"
        leftCount={displayLeftCount}
        rightCount={displayRightCount}
        swapped={swapped}
        onSwap={handleSwap}
        getNodeLabel={getNodeLabel}
      />

      {/* Colonna geometria sinistra */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria LEFT:</label>
        <select
          value={config.left_geom || displayLeftGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('left_geom', e.target.value)}
          className="transform-node__select"
        >
          {displayLeftGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      {/* Colonna geometria destra */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria RIGHT:</label>
        <select
          value={config.right_geom || displayRightGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('right_geom', e.target.value)}
          className="transform-node__select"
        >
          {displayRightGeomColumns.map((col, idx) => (
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
          <option value="within">Within (LEFT dentro RIGHT)</option>
          <option value="contains">Contains (LEFT contiene RIGHT)</option>
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
          <option value="left">Left (tutti da LEFT)</option>
          <option value="right">Right (tutti da RIGHT)</option>
        </select>
      </div>

      {/* Confirmation */}
      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">🌐 Configurazione Spatial Join:</div>
        <div className="transform-node__confirm-content">
          • Predicato: {config.predicate || 'intersects'}<br/>
          • Tipo: {config.how || 'inner'}
          {swapped && <><br/>• ↔️ Sorgenti invertite</>}
        </div>
      </div>
    </div>
  );
};

export default SpatialJoinConfig;