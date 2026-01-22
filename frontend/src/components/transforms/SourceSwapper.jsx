/**
 * SourceSwapper.jsx - Componente per visualizzare e invertire sorgenti LEFT/RIGHT
 * 
 * NUOVO FILE - 2026-01-22
 * Posizione: /components/transforms/SourceSwapper.jsx
 * 
 * Usato da: JoinConfig, SpatialJoinConfig, ClipConfig
 */
import React from 'react';

const SourceSwapper = ({
  leftNode,
  rightNode,
  leftLabel = "L",
  rightLabel = "R",
  leftCount = 0,
  rightCount = 0,
  swapped = false,
  onSwap,
  getNodeLabel
}) => {
  // Se swapped, invertiamo la visualizzazione
  const displayLeft = swapped ? rightNode : leftNode;
  const displayRight = swapped ? leftNode : rightNode;
  const displayLeftCount = swapped ? rightCount : leftCount;
  const displayRightCount = swapped ? leftCount : rightCount;

  return (
    <div className="transform-node__source-swapper">
      <div className="transform-node__source-swapper-header">
        <span className="transform-node__label">Sorgenti collegate:</span>
        <button
          type="button"
          className="transform-node__swap-btn"
          onClick={onSwap}
          title="Inverti LEFT e RIGHT"
        >
          🔄 Inverti
        </button>
      </div>
      
      <div className="transform-node__source-list">
        <div className="transform-node__source-item">
          <span className={`transform-node__source-badge transform-node__source-badge--${leftLabel.toLowerCase()}`}>
            {leftLabel}
          </span>
          <span className="transform-node__source-name">
            {getNodeLabel ? getNodeLabel(displayLeft) : displayLeft?.id || 'N/A'}
          </span>
          <span className="transform-node__source-count">
            ({displayLeftCount} col)
          </span>
        </div>
        
        <div className="transform-node__source-item">
          <span className={`transform-node__source-badge transform-node__source-badge--${rightLabel.toLowerCase()}`}>
            {rightLabel}
          </span>
          <span className="transform-node__source-name">
            {getNodeLabel ? getNodeLabel(displayRight) : displayRight?.id || 'N/A'}
          </span>
          <span className="transform-node__source-count">
            ({displayRightCount} col)
          </span>
        </div>
      </div>

      {swapped && (
        <div className="transform-node__source-swapped-hint">
          ↔️ Ordine invertito rispetto ai collegamenti
        </div>
      )}
    </div>
  );
};

export default SourceSwapper;