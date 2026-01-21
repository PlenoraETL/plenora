/**
 * SortConfig.jsx - Configurazione per operazione Sort
 * Versione con UI migliorata
 */
import React from 'react';

const SortConfig = ({ 
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

  const sortColumns = config.sort_columns || [];

  const updateSortColumn = (idx, field, value) => {
    const newSortCols = [...sortColumns];
    newSortCols[idx] = { ...newSortCols[idx], [field]: value };
    updateConfig('sort_columns', newSortCols);
  };

  const removeSortColumn = (idx) => {
    const newSortCols = sortColumns.filter((_, i) => i !== idx);
    updateConfig('sort_columns', newSortCols);
  };

  const addSortColumn = () => {
    const newSortCols = [...sortColumns, { column: '', ascending: true }];
    updateConfig('sort_columns', newSortCols);
  };

  return (
    <div className="transform-node__config">
      {/* Colonne ordinamento */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">📊 Colonne ordinamento:</label>
        
        <div className="transform-node__sort-list">
          {sortColumns.map((sortCol, idx) => (
            <div key={idx} className="transform-node__sort-card">
              <div className="transform-node__sort-card-header">
                <span className="transform-node__sort-card-num">#{idx + 1}</span>
                <button
                  type="button"
                  onClick={() => removeSortColumn(idx)}
                  className="transform-node__sort-card-remove"
                  title="Rimuovi"
                >
                  ✕
                </button>
              </div>
              
              <div className="transform-node__sort-card-body">
                <select
                  value={sortCol.column || ''}
                  onChange={(e) => updateSortColumn(idx, 'column', e.target.value)}
                  className="transform-node__select"
                >
                  <option value="">-- Seleziona colonna --</option>
                  {inputColumns.map((col, cidx) => (
                    <option key={cidx} value={col.name}>{col.name}</option>
                  ))}
                </select>
                
                <div className="transform-node__sort-direction-btns">
                  <button
                    type="button"
                    className={`transform-node__sort-dir-btn ${sortCol.ascending ? 'transform-node__sort-dir-btn--active' : ''}`}
                    onClick={() => updateSortColumn(idx, 'ascending', true)}
                  >
                    ↑ ASC
                  </button>
                  <button
                    type="button"
                    className={`transform-node__sort-dir-btn ${!sortCol.ascending ? 'transform-node__sort-dir-btn--active' : ''}`}
                    onClick={() => updateSortColumn(idx, 'ascending', false)}
                  >
                    ↓ DESC
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
        
        <button
          type="button"
          onClick={addSortColumn}
          className="transform-node__add-btn"
        >
          + Aggiungi colonna
        </button>
      </div>

      {/* Posizione NULL */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Posizione valori NULL:</label>
        <select
          value={config.na_position || 'last'}
          onChange={(e) => updateConfig('na_position', e.target.value)}
          className="transform-node__select"
        >
          <option value="last">Alla fine</option>
          <option value="first">All'inizio</option>
        </select>
      </div>

      {/* Riepilogo */}
      {sortColumns.filter(sc => sc.column).length > 0 && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">↕️ Ordinamento:</div>
          <div className="transform-node__confirm-content">
            {sortColumns.filter(sc => sc.column).map((sc, i) => (
              <div key={i}>
                {i + 1}. {sc.column} ({sc.ascending ? 'ASC ↑' : 'DESC ↓'})
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default SortConfig;