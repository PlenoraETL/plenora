/**
 * SortConfig.jsx - Configurazione per operazione Sort
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

  return (
    <div className="transform-node__config">
      {/* Colonne ordinamento */}
      <div className="transform-node__config-group">
        <label className="transform-node__label">Colonne ordinamento:</label>
        
        {/* Lista colonne configurate */}
        {(config.sort_columns || []).map((sortCol, idx) => (
          <div key={idx} className="transform-node__sort-row">
            <select
              value={sortCol.column || ''}
              onChange={(e) => {
                const newSortCols = [...(config.sort_columns || [])];
                newSortCols[idx] = { ...newSortCols[idx], column: e.target.value };
                updateConfig('sort_columns', newSortCols);
              }}
              className="transform-node__config-select"
              style={{ flex: 1 }}
            >
              <option value="">-- Seleziona --</option>
              {inputColumns.map((col, cidx) => (
                <option key={cidx} value={col.name}>{col.name}</option>
              ))}
            </select>
            
            <select
              value={sortCol.ascending ? 'asc' : 'desc'}
              onChange={(e) => {
                const newSortCols = [...(config.sort_columns || [])];
                newSortCols[idx] = { ...newSortCols[idx], ascending: e.target.value === 'asc' };
                updateConfig('sort_columns', newSortCols);
              }}
              className="transform-node__config-select"
              style={{ width: '80px' }}
            >
              <option value="asc">↑ ASC</option>
              <option value="desc">↓ DESC</option>
            </select>
            
            <button
              onClick={() => {
                const newSortCols = (config.sort_columns || []).filter((_, i) => i !== idx);
                updateConfig('sort_columns', newSortCols);
              }}
              className="transform-node__btn-remove"
            >
              ✕
            </button>
          </div>
        ))}
        
        {/* Bottone aggiungi colonna */}
        <button
          onClick={() => {
            const newSortCols = [...(config.sort_columns || []), { column: '', ascending: true }];
            updateConfig('sort_columns', newSortCols);
          }}
          className="transform-node__btn-add"
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
          className="transform-node__config-select"
        >
          <option value="last">Alla fine</option>
          <option value="first">All'inizio</option>
        </select>
      </div>

      {/* Riepilogo */}
      {(config.sort_columns || []).length > 0 && (
        <div className="transform-node__confirm-box">
          <div className="transform-node__confirm-title">↕️ Configurazione Sort:</div>
          <div className="transform-node__confirm-content">
            {(config.sort_columns || []).map((sc, i) => (
              <div key={i}>
                {i + 1}. {sc.column || '?'} ({sc.ascending ? 'ASC ↑' : 'DESC ↓'})
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default SortConfig;
