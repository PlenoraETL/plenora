/**
 * ConcatConfig.jsx - Configurazione per operazione Concat
 */
import React from 'react';

const ConcatConfig = ({ 
  config, 
  updateConfig, 
  validation,
  incomingEdges,
  nodeId
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
      {/* Info */}
      <div className="transform-node__info-box">
        <div className="transform-node__info-row">
          🔗 {incomingEdges.length} dataset collegati
        </div>
      </div>

      {/* Modalità colonne */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Modalità colonne:</label>
        <div className="transform-node__options">
          <label className="transform-node__option-label">
            <input
              type="radio"
              name={`concat-join-${nodeId}`}
              value="inner"
              checked={config.join === 'inner'}
              onChange={(e) => updateConfig('join', e.target.value)}
            />
            <div className="transform-node__option-content">
              <div className="transform-node__option-title">Solo colonne comuni</div>
              <div className="transform-node__option-desc">
                Mantiene solo le colonne presenti in tutti i dataset
              </div>
            </div>
          </label>
          
          <label className="transform-node__option-label">
            <input
              type="radio"
              name={`concat-join-${nodeId}`}
              value="outer"
              checked={config.join !== 'inner'}
              onChange={(e) => updateConfig('join', e.target.value)}
            />
            <div className="transform-node__option-content">
              <div className="transform-node__option-title">Tutte le colonne</div>
              <div className="transform-node__option-desc">
                Mantiene tutte le colonne (NULL dove mancanti)
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* Reset indice */}
      <label className="transform-node__checkbox-option">
        <input
          type="checkbox"
          checked={config.ignore_index !== false}
          onChange={(e) => updateConfig('ignore_index', e.target.checked)}
        />
        <div className="transform-node__option-content">
          <div className="transform-node__option-title">Reset indice righe</div>
          <div className="transform-node__option-desc">
            Rinumera le righe da 0 (consigliato)
          </div>
        </div>
      </label>

      {/* Confirmation */}
      <div className="transform-node__confirm-box">
        <div className="transform-node__confirm-title">✓ Configurazione Concat:</div>
        <div className="transform-node__confirm-content">
          • {incomingEdges.length} dataset in input<br/>
          • Colonne: {config.join === 'inner' ? 'solo comuni' : 'tutte (outer)'}<br/>
          • Reset indice: {config.ignore_index !== false ? 'sì' : 'no'}
        </div>
      </div>
    </div>
  );
};

export default ConcatConfig;
