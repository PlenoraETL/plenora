/**
 * PivotMappingBuilder.jsx
 * 
 * Builder per il mapping pivot: associa valori sorgente a nomi colonne output.
 * L'utente aggiunge righe con coppie { valore_nella_colonna_pivot → nome_colonna_output }.
 * 
 * Output (onChange): oggetto { "canone": "val_canone", "rendita": "val_rendita" }
 * Se vuoto → {} → il backend crea una colonna per ogni valore unico (auto).
 * 
 * Uso in ParameterInput.jsx:
 *   case 'pivot_mapping':
 *     return <PivotMappingBuilder value={value} onChange={onChange} />;
 */
import React, { useState, useCallback } from 'react';
import './PivotMappingBuilder.css';

// Converte dict → array di righe per l'editing interno
const dictToRows = (dict) => {
  if (!dict || typeof dict !== 'object' || Object.keys(dict).length === 0) {
    return [];
  }
  return Object.entries(dict).map(([key, val]) => ({ key, val }));
};

// Converte array di righe → dict per l'output
const rowsToDict = (rows) => {
  const dict = {};
  rows.forEach(row => {
    const k = (row.key || '').trim();
    if (k) {
      dict[k] = (row.val || '').trim() || k; // se output vuoto, usa il valore sorgente
    }
  });
  return dict;
};

const PivotMappingBuilder = ({ value, onChange }) => {
  // Stato interno: array di { key, val }
  const [rows, setRows] = useState(() => dictToRows(value));

  // Sync output verso il parent
  const emitChange = useCallback((newRows) => {
    setRows(newRows);
    onChange(rowsToDict(newRows));
  }, [onChange]);

  // Aggiungi riga
  const addRow = useCallback(() => {
    emitChange([...rows, { key: '', val: '' }]);
  }, [rows, emitChange]);

  // Rimuovi riga
  const removeRow = useCallback((idx) => {
    emitChange(rows.filter((_, i) => i !== idx));
  }, [rows, emitChange]);

  // Aggiorna campo di una riga
  const updateRow = useCallback((idx, field, newValue) => {
    const updated = [...rows];
    updated[idx] = { ...updated[idx], [field]: newValue };
    
    // Se l'utente scrive solo il valore sorgente, pre-popola l'output con lo stesso nome
    if (field === 'key' && !updated[idx].val) {
      updated[idx].val = newValue;
    }
    
    emitChange(updated);
  }, [rows, emitChange]);

  const isEmpty = rows.length === 0;

  return (
    <div className="pivot-mapping-builder">
      {/* Header */}
      {!isEmpty && (
        <div className="pivot-mapping-builder__header">
          <span className="pivot-mapping-builder__header-col">Valore sorgente</span>
          <span className="pivot-mapping-builder__header-arrow"></span>
          <span className="pivot-mapping-builder__header-col">Nome colonna output</span>
          <span className="pivot-mapping-builder__header-action"></span>
        </div>
      )}

      {/* Righe */}
      {rows.map((row, idx) => (
        <div key={idx} className="pivot-mapping-builder__row">
          <input
            type="text"
            value={row.key}
            onChange={(e) => updateRow(idx, 'key', e.target.value)}
            placeholder="es: canone"
            className="pivot-mapping-builder__input"
          />
          <span className="pivot-mapping-builder__arrow">→</span>
          <input
            type="text"
            value={row.val}
            onChange={(e) => updateRow(idx, 'val', e.target.value)}
            placeholder="es: val_canone"
            className="pivot-mapping-builder__input"
          />
          <button
            type="button"
            onClick={() => removeRow(idx)}
            className="pivot-mapping-builder__remove"
            title="Rimuovi"
          >
            ×
          </button>
        </div>
      ))}

      {/* Bottone aggiungi */}
      <button
        type="button"
        onClick={addRow}
        className="pivot-mapping-builder__add"
      >
        + Aggiungi mapping
      </button>

      {/* Nota se vuoto */}
      {isEmpty && (
        <div className="pivot-mapping-builder__hint">
          Nessun mapping: verrà creata una colonna per ogni valore unico
        </div>
      )}
    </div>
  );
};

export default PivotMappingBuilder;
