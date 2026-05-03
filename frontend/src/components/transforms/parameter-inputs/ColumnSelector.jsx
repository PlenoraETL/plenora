/**
 * ColumnSelector.jsx — Selettore colonna (singola o multipla)
 *
 * Estratto da `ParameterInput.jsx` in `FE-PHASE2-SPLIT-002A`.
 * Usato dal dispatcher per i tipi `column`, `geometry_column`,
 * `columns`. Supporta filtro per tipo (geometry/numeric/text) e
 * label di sorgente (es. "⬅ SX" / "➡ DX" per join).
 *
 * Stili in `ParameterInput.css` (classi `column-selector*` +
 * `parameter-input__field`).
 */
import React from 'react';

const ColumnSelector = ({
  columns,
  value,
  onChange,
  multiple = false,
  placeholder = "Seleziona colonna",
  filter = null,
  sourceLabel = null  // "⬅ SX" o "➡ DX" per indicare provenienza
}) => {
  // Filtra colonne se necessario
  const filteredColumns = filter
    ? columns.filter(col => {
        const colType = (col.type || '').toLowerCase();
        if (filter === 'geometry') return colType.includes('geometry');
        if (filter === 'numeric') return ['int', 'real', 'float', 'number'].some(t => colType.includes(t));
        if (filter === 'text') return ['str', 'text', 'varchar'].some(t => colType.includes(t));
        return true;
      })
    : columns;

  if (multiple) {
    const selectedValues = Array.isArray(value) ? value : [];

    return (
      <div className="column-selector column-selector--multiple">
        <div className="column-selector__tags">
          {selectedValues.map(v => (
            <span key={v} className="column-selector__tag">
              {v}
              <button
                type="button"
                onClick={() => onChange(selectedValues.filter(x => x !== v))}
                className="column-selector__tag-remove"
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <select
          value=""
          onChange={(e) => {
            if (e.target.value && !selectedValues.includes(e.target.value)) {
              onChange([...selectedValues, e.target.value]);
            }
          }}
          className="parameter-input__field"
        >
          <option value="">+ Aggiungi colonna</option>
          {filteredColumns
            .filter(col => !selectedValues.includes(col.name))
            .map((col, idx) => (
              <option key={`${col.name}-${idx}`} value={col.name}>
                {sourceLabel ? `${sourceLabel} ` : ''}{col.name} ({col.type || 'str'})
              </option>
            ))
          }
        </select>
      </div>
    );
  }

  return (
    <select
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      className="parameter-input__field"
    >
      <option value="">{placeholder}</option>
      {filteredColumns.map((col, idx) => (
        <option key={`${col.name}-${idx}`} value={col.name}>
          {sourceLabel ? `${sourceLabel} ` : ''}{col.name} ({col.type || 'str'})
        </option>
      ))}
    </select>
  );
};

export default ColumnSelector;
