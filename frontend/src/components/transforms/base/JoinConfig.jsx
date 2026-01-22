/**
 * JoinConfig.jsx - Configurazione per operazione Join
 * 
 * AGGIORNATO 2026-01-22: Aggiunto SourceSwapper per controllare ordine LEFT/RIGHT
 * Posizione: /components/transforms/base/JoinConfig.jsx
 */
import React from 'react';
import { getColumnTypeIcon } from '../../../utils/columnUtils';
import SourceSwapper from '../SourceSwapper';

const JoinConfig = ({ 
  config, 
  updateConfig, 
  validation,
  leftNode,
  rightNode,
  leftColumns,
  rightColumns,
  getNodeLabel,
  nodeId
}) => {
  // Gestione swap
  const swapped = config.swapped || false;
  
  // Se swapped, invertiamo le colonne per i dropdown
  const displayLeftColumns = swapped ? rightColumns : leftColumns;
  const displayRightColumns = swapped ? leftColumns : rightColumns;

  const handleSwap = () => {
    updateConfig('swapped', !swapped);
  };

  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        {validation.type === 'warning' ? '⚠️' : '❌'} {validation.message}
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
        leftCount={leftColumns.length}
        rightCount={rightColumns.length}
        swapped={swapped}
        onSwap={handleSwap}
        getNodeLabel={getNodeLabel}
      />

      {/* Tipo JOIN */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Tipo JOIN:</label>
        <div className="transform-node__join-types">
          {['left', 'inner', 'right', 'outer'].map(jt => (
            <label 
              key={jt}
              className={`transform-node__join-type ${config.how === jt ? 'transform-node__join-type--selected' : ''}`}
            >
              <input
                type="radio"
                name={`join-type-${nodeId}`}
                value={jt}
                checked={config.how === jt}
                onChange={(e) => updateConfig('how', e.target.value)}
              />
              {jt.toUpperCase()}
            </label>
          ))}
        </div>
      </div>

      {/* Colonna LEFT */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Colonna LEFT:</label>
        <select
          value={config.left_on || ''}
          onChange={(e) => updateConfig('left_on', e.target.value)}
          className="transform-node__config-select"
        >
          <option value="">-- Seleziona --</option>
          {displayLeftColumns.map((col, idx) => (
            <option key={idx} value={col.name}>
              {getColumnTypeIcon(col.type || col.dtype)} {col.name}
            </option>
          ))}
        </select>
      </div>

      {/* Colonna RIGHT */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Colonna RIGHT:</label>
        <select
          value={config.right_on || ''}
          onChange={(e) => updateConfig('right_on', e.target.value)}
          className="transform-node__config-select"
        >
          <option value="">-- Seleziona --</option>
          {displayRightColumns.map((col, idx) => (
            <option key={idx} value={col.name}>
              {getColumnTypeIcon(col.type || col.dtype)} {col.name}
            </option>
          ))}
        </select>
      </div>

      {/* Confirmation */}
      {config.left_on && config.right_on && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">✓ Configurazione:</div>
          <div className="transform-node__confirm-content">
            {config.how?.toUpperCase() || 'LEFT'} JOIN ON {config.left_on} = {config.right_on}
            {swapped && <><br/>↔️ Sorgenti invertite</>}
          </div>
        </div>
      )}
    </div>
  );
};

export default JoinConfig;