/**
 * FilterModal.jsx — Configurazione filtri WHERE per una tabella
 *
 * Permette di aggiungere condizioni WHERE su una sorgente dati.
 * Gli operatori disponibili cambiano in base al tipo colonna:
 * stringa (LIKE, =, IS NULL...), numero (>, <, BETWEEN...),
 * data (>, <, BETWEEN con datepicker), geometry (ST_IsValid...).
 */
import React, { useState, useEffect } from 'react';
import { getColumnTypeIcon, getColumnTypeLabel } from '../../utils/columnUtils';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './FilterModal.css';

/**
 * Modal per configurare filtri WHERE su una tabella
 * Supporta operatori intelligenti basati sul tipo colonna
 * 
 * REFACTORED: Usa ModalWrapper per struttura base
 * - Rimosso: gestione ESC manuale (ModalWrapper lo fa)
 * - Rimosso: overlay/stopPropagation (ModalWrapper lo fa)
 * - Rimosso: if (!isOpen) return null (ModalWrapper lo fa)
 */
function FilterModal({ isOpen, onClose, filters, columns, tableName, onSave }) {
  const [localFilters, setLocalFilters] = useState([]);

  // Normalizza colonne per supportare sia vecchio formato (stringhe) che nuovo ({name, type})
  const normalizedColumns = columns.map(col => {
    if (typeof col === 'string') {
      return { name: col, type: 'str' };
    }
    return { 
      name: col.name, 
      type: col.type || col.dtype || 'str' 
    };
  });

  useEffect(() => {
    if (isOpen) {
      setLocalFilters(filters.length > 0 ? [...filters] : [{ column: '', operator: '=', value: '' }]);
    }
  }, [isOpen, filters]);

  // RIMOSSO: useEffect per ESC - ModalWrapper lo gestisce automaticamente

  // Definizione operatori con tipi supportati
  const allOperators = [
    { value: '=', label: '=', description: 'Uguale a', types: ['all'] },
    { value: '!=', label: '!=', description: 'Diverso da', types: ['all'] },
    { value: '>', label: '>', description: 'Maggiore di', types: ['int', 'real', 'date'] },
    { value: '<', label: '<', description: 'Minore di', types: ['int', 'real', 'date'] },
    { value: '>=', label: '>=', description: 'Maggiore o uguale', types: ['int', 'real', 'date'] },
    { value: '<=', label: '<=', description: 'Minore o uguale', types: ['int', 'real', 'date'] },
    { value: 'LIKE', label: 'LIKE', description: 'Contiene testo', types: ['str'] },
    { value: 'NOT LIKE', label: 'NOT LIKE', description: 'Non contiene', types: ['str'] },
    { value: 'IN', label: 'IN', description: 'In lista valori', types: ['all'] },
    { value: 'NOT IN', label: 'NOT IN', description: 'Non in lista', types: ['all'] },
    { value: 'IS NULL', label: 'IS NULL', description: 'È nullo', types: ['all'] },
    { value: 'IS NOT NULL', label: 'IS NOT NULL', description: 'Non è nullo', types: ['all'] },
    { value: 'BETWEEN', label: 'BETWEEN', description: 'Tra due valori', types: ['int', 'real', 'date'] }
  ];

  const getOperatorsForType = (columnType) => {
    const type = (columnType || 'str').toLowerCase();
    
    return allOperators.filter(op => {
      if (op.types.includes('all')) return true;
      if (op.types.includes(type)) return true;
      if (type.startsWith('geometry')) {
        return ['=', '!=', 'IS NULL', 'IS NOT NULL'].includes(op.value);
      }
      return false;
    });
  };

  const getColumnType = (columnName) => {
    const col = normalizedColumns.find(c => c.name === columnName);
    return col?.type || 'str';
  };

  const getValuePlaceholder = (columnType, operator) => {
    if (operator === 'IN' || operator === 'NOT IN') {
      return "valore1, valore2, valore3";
    }
    if (operator === 'BETWEEN') {
      return "valore1 AND valore2";
    }
    if (operator === 'LIKE' || operator === 'NOT LIKE') {
      return "%testo%";
    }
    
    const type = (columnType || 'str').toLowerCase();
    switch (type) {
      case 'int':
        return "es: 42";
      case 'real':
        return "es: 3.14";
      case 'date':
        return "es: 2024-01-15";
      case 'bool':
        return "true o false";
      default:
        return "Inserisci valore...";
    }
  };

  const getInputType = (columnType) => {
    const type = (columnType || 'str').toLowerCase();
    switch (type) {
      case 'int':
      case 'real':
        return 'text';
      case 'date':
        return 'date';
      default:
        return 'text';
    }
  };

  const addFilter = () => {
    setLocalFilters([...localFilters, { column: '', operator: '=', value: '' }]);
  };

  const removeFilter = (index) => {
    if (localFilters.length > 1) {
      setLocalFilters(localFilters.filter((_, i) => i !== index));
    } else {
      setLocalFilters([{ column: '', operator: '=', value: '' }]);
    }
  };

  const updateFilter = (index, field, value) => {
    const updated = [...localFilters];
    updated[index][field] = value;
    
    if (field === 'column') {
      const newColType = getColumnType(value);
      const validOperators = getOperatorsForType(newColType);
      const currentOp = updated[index].operator;
      
      if (!validOperators.find(op => op.value === currentOp)) {
        updated[index].operator = '=';
      }
    }
    
    setLocalFilters(updated);
  };

  const handleSave = () => {
    const validFilters = localFilters.filter(f => f.column);
    onSave(validFilters);
    onClose();
  };

  // Footer con pulsanti
  const footer = (
    <>
      <ModalButton variant="secondary" onClick={onClose}>
        Annulla
      </ModalButton>
      <ModalButton variant="primary" onClick={handleSave}>
        ✓ Salva filtri
      </ModalButton>
    </>
  );

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title="Filtri WHERE"
      icon="🔍"
      size="md"
      variant="estrai"
      footer={footer}
    >
      {/* Info tabella */}
      <div className="filter-modal__info-box">
        <span className="filter-modal__info-label">Tabella:</span> {tableName}
        <span className="filter-modal__info-count">
          ({normalizedColumns.length} colonne)
        </span>
      </div>

      {/* Lista filtri */}
      <div className="filter-modal__filters-list">
        {localFilters.map((filter, index) => {
          const colType = getColumnType(filter.column);
          const availableOperators = getOperatorsForType(colType);
          const needsValue = !['IS NULL', 'IS NOT NULL'].includes(filter.operator);
          
          return (
            <div key={index} className="filter-modal__filter-item">
              {/* Colonna con icona tipo */}
              <div className="form-group">
                <label>Colonna</label>
                <select
                  value={filter.column}
                  onChange={(e) => updateFilter(index, 'column', e.target.value)}
                >
                  <option value="">-- Seleziona colonna --</option>
                  {normalizedColumns.map((col, idx) => (
                    <option key={idx} value={col.name}>
                      {getColumnTypeIcon(col.type)} {col.name} ({getColumnTypeLabel(col.type)})
                    </option>
                  ))}
                </select>
              </div>

              {/* Operatore (filtrato per tipo) */}
              <div className="form-group">
                <label>
                  Operatore
                  {filter.column && (
                    <span className="filter-modal__type-hint">
                      {' '}(per {getColumnTypeLabel(colType)})
                    </span>
                  )}
                </label>
                <select
                  value={filter.operator}
                  onChange={(e) => updateFilter(index, 'operator', e.target.value)}
                >
                  {availableOperators.map(op => (
                    <option key={op.value} value={op.value}>
                      {op.label} - {op.description}
                    </option>
                  ))}
                </select>
              </div>

              {/* Valore - Hide for IS NULL/IS NOT NULL */}
              {needsValue && (
                <div className="form-group">
                  <label>Valore</label>
                  <input
                    type={getInputType(colType)}
                    value={filter.value}
                    onChange={(e) => updateFilter(index, 'value', e.target.value)}
                    placeholder={getValuePlaceholder(colType, filter.operator)}
                  />
                  {filter.operator === 'LIKE' && (
                    <div className="filter-modal__value-hint">
                      💡 Usa % come jolly: %testo% trova "questo testo qui"
                    </div>
                  )}
                  {(filter.operator === 'IN' || filter.operator === 'NOT IN') && (
                    <div className="filter-modal__value-hint">
                      💡 Separa i valori con virgole: valore1, valore2
                    </div>
                  )}
                  {filter.operator === 'BETWEEN' && (
                    <div className="filter-modal__value-hint">
                      💡 Formato: valore_min AND valore_max
                    </div>
                  )}
                </div>
              )}

              {/* Bottone rimuovi filtro */}
              {localFilters.length > 1 && (
                <button type="button"
                  className="filter-modal__remove-btn"
                  onClick={() => removeFilter(index)}
                >
                  🗑️ Rimuovi filtro
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottone aggiungi filtro */}
      <button type="button" className="filter-modal__add-btn" onClick={addFilter}>
        + Aggiungi filtro (AND)
      </button>

      {/* Preview SQL */}
      {localFilters.some(f => f.column) && (
        <div className="filter-modal__preview">
          <div className="filter-modal__preview-label">Preview SQL:</div>
          <div className="filter-modal__preview-sql">
            <span className="filter-modal__sql-keyword">WHERE </span>
            {localFilters
              .filter(f => f.column)
              .map((f, idx) => {
                const needsValue = !['IS NULL', 'IS NOT NULL'].includes(f.operator);
                const colType = getColumnType(f.column);
                const isNumeric = ['int', 'real'].includes(colType);
                
                let valueDisplay = '';
                if (needsValue) {
                  if (f.operator === 'IN' || f.operator === 'NOT IN') {
                    const values = f.value ? f.value.split(',').map(v => 
                      isNumeric ? v.trim() : `'${v.trim()}'`
                    ).join(', ') : '?';
                    valueDisplay = `(${values})`;
                  } else if (f.operator === 'BETWEEN') {
                    valueDisplay = f.value || '? AND ?';
                  } else {
                    valueDisplay = f.value 
                      ? (isNumeric ? f.value : `'${f.value}'`)
                      : '?';
                  }
                }
                
                return (
                  <span key={idx}>
                    {idx > 0 && <span className="filter-modal__sql-keyword"> AND </span>}
                    {tableName}.{f.column}
                    <span className="filter-modal__sql-operator"> {f.operator} </span>
                    {valueDisplay}
                  </span>
                );
              })
            }
          </div>
        </div>
      )}
    </ModalWrapper>
  );
}

export default FilterModal;