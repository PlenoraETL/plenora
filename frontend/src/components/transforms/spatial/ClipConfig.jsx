/**
 * ClipConfig.jsx - Configurazione per operazione Clip
 * 
 * AGGIORNATO 2026-01-22: Aggiunto SourceSwapper per controllare ordine INPUT/MASK
 * Posizione: /components/transforms/spatial/ClipConfig.jsx
 */
import React from 'react';
import SourceSwapper from '../SourceSwapper';

const ClipConfig = ({ 
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
  const displayInputGeomColumns = swapped ? rightGeomColumns : leftGeomColumns;
  const displayMaskGeomColumns = swapped ? leftGeomColumns : rightGeomColumns;
  const displayInputCount = swapped 
    ? (rightColumns?.length || rightGeomColumns?.length || 0) 
    : (leftColumns?.length || leftGeomColumns?.length || 0);
  const displayMaskCount = swapped 
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
          INPUT: layer da ritagliare<br/>
          MASK: maschera di ritaglio
        </div>
      </div>
    );
  }

  return (
    <div className="transform-node__config">
      {/* Source Swapper con etichette specifiche per Clip */}
      <SourceSwapper
        leftNode={leftNode}
        rightNode={rightNode}
        leftLabel="INPUT"
        rightLabel="MASK"
        leftCount={displayInputCount}
        rightCount={displayMaskCount}
        swapped={swapped}
        onSwap={handleSwap}
        getNodeLabel={getNodeLabel}
      />

      {/* Geometria da ritagliare */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Geometria da ritagliare:</label>
        <select
          value={config.input_geom || displayInputGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('input_geom', e.target.value)}
          className="transform-node__select"
        >
          {displayInputGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      {/* Maschera */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Maschera di ritaglio:</label>
        <select
          value={config.mask_geom || displayMaskGeomColumns[0]?.name || ''}
          onChange={(e) => updateConfig('mask_geom', e.target.value)}
          className="transform-node__select"
        >
          {displayMaskGeomColumns.map((col, idx) => (
            <option key={idx} value={col.name}>🌐 {col.name}</option>
          ))}
        </select>
      </div>

      {/* Confirmation */}
      <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
        <div className="transform-node__confirm-title">✂️ Configurazione Clip:</div>
        <div className="transform-node__confirm-content">
          Ritaglia geometrie INPUT usando MASK
          {swapped && <><br/>↔️ Sorgenti invertite</>}
        </div>
      </div>
    </div>
  );
};

export default ClipConfig;