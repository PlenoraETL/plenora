/**
 * DistinctConfig.jsx - Configurazione per operazione Distinct
 * Versione con UI migliorata
 */
import React from 'react';

const DistinctConfig = ({ 
  config, 
  updateConfig, 
  validation,
  inputColumns
}) => {
  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
      </div>
    );
  }

  const subset = config.subset || [];
  const mode = config.mode || 'all';

  const toggleColumn = (colName) => {
    const current = [...subset];
    const idx = current.indexOf(colName);
    if (idx >= 0) {
      current.splice(idx, 1);
    } else {
      current.push(colName);
    }
    updateConfig('subset', current);
  };

  return (
    <div className="transform-node__config">
      {/* Modalità */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">🔍 Considera duplicati su:</label>
        <div className="transform-node__toggle-btns">
          <button
            type="button"
            className={`transform-node__toggle-btn ${mode === 'all' ? 'transform-node__toggle-btn--active' : ''}`}
            onClick={() => {
              updateConfig('mode', 'all');
              updateConfig('subset', null);
            }}
          >
            Tutte le colonne
          </button>
          <button
            type="button"
            className={`transform-node__toggle-btn ${mode === 'subset' ? 'transform-node__toggle-btn--active' : ''}`}
            onClick={() => updateConfig('mode', 'subset')}
          >
            Solo alcune
          </button>
        </div>
      </div>

      {/* Selezione colonne subset con chips */}
      {mode === 'subset' && (
        <div className="transform-node__config-group">
          <label className="transform-node__label">Colonne da considerare:</label>
          <div className="transform-node__chips-container">
            {inputColumns.map((col, idx) => {
              const isSelected = subset.includes(col.name);
              return (
                <button
                  key={idx}
                  type="button"
                  className={`transform-node__chip ${isSelected ? 'transform-node__chip--selected' : ''}`}
                  onClick={() => toggleColumn(col.name)}
                >
                  {isSelected && <span className="transform-node__chip-check">✓</span>}
                  {col.name}
                </button>
              );
            })}
          </div>
          {subset.length > 0 && (
            <div className="transform-node__selected-info">
              Selezionate: {subset.length} colonne
            </div>
          )}
        </div>
      )}

      {/* Keep mode */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">In caso di duplicati:</label>
        <div className="transform-node__toggle-btns transform-node__toggle-btns--vertical">
          <button
            type="button"
            className={`transform-node__toggle-btn ${config.keep === 'first' || !config.keep ? 'transform-node__toggle-btn--active' : ''}`}
            onClick={() => updateConfig('keep', 'first')}
          >
            🥇 Mantieni prima occorrenza
          </button>
          <button
            type="button"
            className={`transform-node__toggle-btn ${config.keep === 'last' ? 'transform-node__toggle-btn--active' : ''}`}
            onClick={() => updateConfig('keep', 'last')}
          >
            🏁 Mantieni ultima occorrenza
          </button>
          <button
            type="button"
            className={`transform-node__toggle-btn ${config.keep === 'false' ? 'transform-node__toggle-btn--active' : ''}`}
            onClick={() => updateConfig('keep', 'false')}
          >
            ❌ Rimuovi tutti i duplicati
          </button>
        </div>
      </div>

      {/* Riepilogo */}
      <div className="transform-node__confirm-box">
        <div className="transform-node__confirm-title">🔹 Configurazione:</div>
        <div className="transform-node__confirm-content">
          {mode === 'all' ? (
            <div>Duplicati su: tutte le colonne</div>
          ) : (
            <div>Duplicati su: {subset.length > 0 ? subset.join(', ') : 'nessuna colonna'}</div>
          )}
          <div>
            Azione: {config.keep === 'first' || !config.keep ? 'mantieni prima' : 
                     config.keep === 'last' ? 'mantieni ultima' : 'rimuovi tutti'}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DistinctConfig;