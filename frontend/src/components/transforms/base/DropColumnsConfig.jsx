/**
 * DropColumnsConfig.jsx - Configurazione per operazione Drop Columns
 * 
 * AGGIORNATO 2026-01-22: UI migliorata con chips invece di checkbox
 * Posizione: /components/transforms/base/DropColumnsConfig.jsx
 */
import React from 'react';

const DropColumnsConfig = ({ 
  config, 
  updateConfig, 
  validation,
  inputColumns
}) => {
  const selectedColumns = config.columns || [];

  // Toggle selezione colonna
  const toggleColumn = (colName) => {
    const current = [...selectedColumns];
    const idx = current.indexOf(colName);
    if (idx >= 0) {
      current.splice(idx, 1);
    } else {
      current.push(colName);
    }
    updateConfig('columns', current);
  };

  // Seleziona/Deseleziona tutte
  const selectAll = () => {
    updateConfig('columns', inputColumns.map(c => c.name));
  };

  const selectNone = () => {
    updateConfig('columns', []);
  };

  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        ⚠️ {validation.message}
      </div>
    );
  }

  const remainingCount = inputColumns.length - selectedColumns.length;

  return (
    <div className="transform-node__config">
      {/* Header con azioni rapide */}
      <div className="transform-node__config-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
          <label className="transform-node__label">Colonne da eliminare:</label>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button
              type="button"
              onClick={selectAll}
              style={{
                padding: '2px 6px',
                fontSize: '9px',
                background: '#fff',
                border: '1px solid #ddd',
                borderRadius: '3px',
                cursor: 'pointer'
              }}
              title="Seleziona tutte"
            >
              Tutte
            </button>
            <button
              type="button"
              onClick={selectNone}
              style={{
                padding: '2px 6px',
                fontSize: '9px',
                background: '#fff',
                border: '1px solid #ddd',
                borderRadius: '3px',
                cursor: 'pointer'
              }}
              title="Deseleziona tutte"
            >
              Nessuna
            </button>
          </div>
        </div>

        {/* Chips container */}
        <div className="transform-node__chips-container">
          {inputColumns.map((col, idx) => {
            const isSelected = selectedColumns.includes(col.name);
            return (
              <div
                key={idx}
                className={`transform-node__chip ${isSelected ? 'transform-node__chip--selected' : ''}`}
                onClick={() => toggleColumn(col.name)}
                title={isSelected ? 'Clicca per mantenere' : 'Clicca per eliminare'}
              >
                <span className="transform-node__chip-check">
                  {isSelected ? '🗑️' : ''}
                </span>
                <span>{col.name}</span>
              </div>
            );
          })}
        </div>

        {/* Info selezione */}
        <div className="transform-node__selected-info">
          {selectedColumns.length === 0 
            ? 'Clicca sulle colonne da eliminare'
            : `${selectedColumns.length} da eliminare, ${remainingCount} rimanenti`
          }
        </div>
      </div>

      {/* Riepilogo */}
      {selectedColumns.length > 0 && (
        <div className="transform-node__confirm-box" style={{ background: '#FFEBEE', borderColor: '#EF9A9A' }}>
          <div className="transform-node__confirm-title" style={{ color: '#C62828' }}>
            🗑️ Colonne da eliminare ({selectedColumns.length}):
          </div>
          <div className="transform-node__confirm-content">
            {selectedColumns.join(', ')}
          </div>
          <div style={{ marginTop: '6px', fontSize: '10px', color: '#666' }}>
            ✓ Output: {remainingCount} colonne
          </div>
        </div>
      )}
    </div>
  );
};

export default DropColumnsConfig;
