/**
 * ParameterInput.jsx - Renderizza un singolo parametro in base al tipo
 * 
 * Tipi supportati:
 * - string: input testo
 * - number: input numerico con min/max/step
 * - boolean: checkbox
 * - select: dropdown
 * - column: selettore colonna
 * - columns: selettore colonne multiple
 * - geometry_column: selettore colonna geometria
 * - expression: textarea per formule
 * - string_list: input per lista stringhe
 * - aggregation_list: builder aggregazioni
 * - masking_list: builder mascheramento dati
 * - column_order: drag & drop per riordinamento colonne
 * 
 * Posizione: /components/transforms/ParameterInput.jsx
 */

import React, { useState, memo } from 'react';
import './ParameterInput.css';
import PivotMappingBuilder from './PivotMappingBuilder';
import './PivotMappingBuilder.css';

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

// Componente per selezionare colonne
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

// Componente per lista stringhe
const StringListInput = ({ value, onChange, placeholder = "Valore" }) => {
  const values = Array.isArray(value) ? value : (value ? value.split(',').map(s => s.trim()) : []);
  const [inputValue, setInputValue] = useState('');

  const addValue = () => {
    if (inputValue.trim()) {
      onChange([...values, inputValue.trim()]);
      setInputValue('');
    }
  };

  return (
    <div className="string-list-input">
      <div className="string-list-input__tags">
        {values.map((v, idx) => (
          <span key={idx} className="string-list-input__tag">
            {v}
            <button 
              type="button"
              onClick={() => onChange(values.filter((_, i) => i !== idx))}
              className="string-list-input__tag-remove"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="string-list-input__add">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addValue())}
          placeholder={placeholder}
        />
        <button type="button" onClick={addValue}>+</button>
      </div>
    </div>
  );
};

// Componente per aggregazioni
const AggregationListInput = ({ value, onChange, columns }) => {
  const aggregations = Array.isArray(value) ? value : [];
  const aggFunctions = ['count', 'sum', 'mean', 'min', 'max', 'first', 'last', 'concat'];

  const addAggregation = () => {
    onChange([...aggregations, { column: '', function: 'count' }]);
  };

  const updateAggregation = (idx, field, newValue) => {
    const updated = [...aggregations];
    updated[idx] = { ...updated[idx], [field]: newValue };
    // Se cambia funzione a concat, aggiungi default params
    if (field === 'function' && newValue === 'concat') {
      updated[idx] = { 
        ...updated[idx], 
        separator: updated[idx].separator || ', ',
        distinct: updated[idx].distinct ?? false,
        skip_null: updated[idx].skip_null ?? true
      };
    }
    onChange(updated);
  };

  const removeAggregation = (idx) => {
    onChange(aggregations.filter((_, i) => i !== idx));
  };

  return (
    <div className="aggregation-list-input">
      {aggregations.map((agg, idx) => (
        <div key={idx} className="aggregation-list-input__row">
          <select
            value={agg.column || ''}
            onChange={(e) => updateAggregation(idx, 'column', e.target.value)}
          >
            <option value="">Colonna</option>
            {columns.map((col, idx) => (
              <option key={`${col.name}-${idx}`} value={col.name}>{col.name}</option>
            ))}
          </select>
          <select
            value={agg.function || 'count'}
            onChange={(e) => updateAggregation(idx, 'function', e.target.value)}
          >
            {aggFunctions.map(fn => (
              <option key={fn} value={fn}>{fn}</option>
            ))}
          </select>
          {/* Parametri extra per concat */}
          {agg.function === 'concat' && (
            <div className="aggregation-list-input__concat-params">
              <input
                type="text"
                value={agg.separator ?? ', '}
                onChange={(e) => updateAggregation(idx, 'separator', e.target.value)}
                placeholder="Separatore"
                title="Separatore tra valori"
                style={{ width: '60px' }}
              />
              <label title="Rimuovi duplicati">
                <input
                  type="checkbox"
                  checked={agg.distinct ?? false}
                  onChange={(e) => updateAggregation(idx, 'distinct', e.target.checked)}
                />
                Unici
              </label>
              <label title="Ignora NULL">
                <input
                  type="checkbox"
                  checked={agg.skip_null ?? true}
                  onChange={(e) => updateAggregation(idx, 'skip_null', e.target.checked)}
                />
                No NULL
              </label>
            </div>
          )}
          <button 
            type="button" 
            onClick={() => removeAggregation(idx)}
            className="aggregation-list-input__remove"
          >
            ×
          </button>
        </div>
      ))}
      <button 
        type="button" 
        onClick={addAggregation}
        className="aggregation-list-input__add"
      >
        + Aggiungi aggregazione
      </button>
    </div>
  );
};

// Componente per riordinamento colonne con drag & drop
const ColumnOrderInput = ({ value, onChange, columns }) => {
  const [dragIdx, setDragIdx] = useState(null);
  const [dragOverIdx, setDragOverIdx] = useState(null);

  // Inizializza con tutte le colonne se vuoto
  const orderedCols = (() => {
    const current = Array.isArray(value) ? value : [];
    if (current.length > 0) return current;
    return columns.map(c => c.name);
  })();

  // Inizializza automaticamente se value è vuoto e ci sono colonne
  React.useEffect(() => {
    if ((!value || (Array.isArray(value) && value.length === 0)) && columns.length > 0) {
      onChange(columns.map(c => c.name));
    }
  }, [columns]);

  const handleDragStart = (idx) => {
    setDragIdx(idx);
  };

  const handleDragOver = (e, idx) => {
    e.preventDefault();
    if (idx !== dragOverIdx) setDragOverIdx(idx);
  };

  const handleDrop = (e, targetIdx) => {
    e.preventDefault();
    if (dragIdx === null || dragIdx === targetIdx) {
      setDragIdx(null);
      setDragOverIdx(null);
      return;
    }
    const updated = [...orderedCols];
    const [moved] = updated.splice(dragIdx, 1);
    updated.splice(targetIdx, 0, moved);
    onChange(updated);
    setDragIdx(null);
    setDragOverIdx(null);
  };

  const handleDragEnd = () => {
    setDragIdx(null);
    setDragOverIdx(null);
  };

  // Sposta su/giù con bottoni
  const moveUp = (idx) => {
    if (idx === 0) return;
    const updated = [...orderedCols];
    [updated[idx - 1], updated[idx]] = [updated[idx], updated[idx - 1]];
    onChange(updated);
  };

  const moveDown = (idx) => {
    if (idx >= orderedCols.length - 1) return;
    const updated = [...orderedCols];
    [updated[idx], updated[idx + 1]] = [updated[idx + 1], updated[idx]];
    onChange(updated);
  };

  // Trova tipo colonna
  const getColType = (name) => {
    const col = columns.find(c => c.name === name);
    return col?.type || 'str';
  };

  return (
    <div className="column-order-input">
      <div className="column-order-input__header">
        <span className="column-order-input__count">
          {orderedCols.length} colonne
        </span>
      </div>
      <div className="column-order-input__list">
        {orderedCols.map((colName, idx) => (
          <div
            key={colName}
            className={
              'column-order-input__item' +
              (dragIdx === idx ? ' column-order-input__item--dragging' : '') +
              (dragOverIdx === idx ? ' column-order-input__item--over' : '')
            }
            draggable
            onDragStart={() => handleDragStart(idx)}
            onDragOver={(e) => handleDragOver(e, idx)}
            onDrop={(e) => handleDrop(e, idx)}
            onDragEnd={handleDragEnd}
          >
            <span className="column-order-input__grip">⠿</span>
            <span className="column-order-input__pos">{idx + 1}</span>
            <span className="column-order-input__name">{colName}</span>
            <span className="column-order-input__type">{getColType(colName)}</span>
            <div className="column-order-input__buttons">
              <button
                type="button"
                className="column-order-input__btn"
                onClick={() => moveUp(idx)}
                disabled={idx === 0}
                title="Sposta su"
              >▲</button>
              <button
                type="button"
                className="column-order-input__btn"
                onClick={() => moveDown(idx)}
                disabled={idx >= orderedCols.length - 1}
                title="Sposta giù"
              >▼</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// Componente per rinominazione multipla colonne
const RenameListInput = ({ value, onChange, columns }) => {
  const renames = Array.isArray(value) ? value : [];
  const [showCsvPaste, setShowCsvPaste] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvError, setCsvError] = useState('');

  const addRename = () => {
    onChange([...renames, { old_name: '', new_name: '' }]);
  };

  const loadAllColumns = () => {
    // Colonne già presenti nella lista
    const existing = new Set(renames.map(r => r.old_name).filter(Boolean));
    // Aggiungi solo quelle mancanti
    const newEntries = columns
      .filter(col => !existing.has(col.name))
      .map(col => ({ old_name: col.name, new_name: '' }));
    if (newEntries.length === 0) return;
    onChange([...renames.filter(r => r.old_name), ...newEntries]);
  };

  const parseCsv = () => {
    setCsvError('');
    if (!csvText.trim()) return;

    const lines = csvText.trim().split('\n').filter(l => l.trim());
    const colNames = new Set(columns.map(c => c.name));
    const parsed = [];
    const errors = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      // Supporta: tab, punto e virgola, virgola
      let parts = null;
      if (line.includes('\t')) {
        parts = line.split('\t');
      } else if (line.includes(';')) {
        parts = line.split(';');
      } else if (line.includes(',')) {
        parts = line.split(',');
      }

      if (!parts || parts.length < 2) {
        errors.push(`Riga ${i + 1}: servono 2 colonne separate da , ; o tab`);
        continue;
      }

      const oldName = parts[0].trim();
      const newName = parts[1].trim();

      if (!oldName || !newName) {
        errors.push(`Riga ${i + 1}: nome vuoto`);
        continue;
      }

      if (colNames.size > 0 && !colNames.has(oldName)) {
        errors.push(`Riga ${i + 1}: "${oldName}" non trovata nelle colonne`);
      }

      parsed.push({ old_name: oldName, new_name: newName });
    }

    if (parsed.length > 0) {
      // Merge: sostituisci quelle con stesso old_name, aggiungi nuove
      const existingMap = new Map(renames.filter(r => r.old_name).map(r => [r.old_name, r]));
      parsed.forEach(p => existingMap.set(p.old_name, p));
      onChange(Array.from(existingMap.values()));
      setCsvText('');
      setShowCsvPaste(false);
    }

    if (errors.length > 0) {
      setCsvError(errors.join('\n'));
    }
  };

  const updateRename = (idx, field, newValue) => {
    const updated = [...renames];
    updated[idx] = { ...updated[idx], [field]: newValue };
    onChange(updated);
  };

  const removeRename = (idx) => {
    onChange(renames.filter((_, i) => i !== idx));
  };

  return (
    <div className="rename-list-input">
      {renames.map((rename, idx) => (
        <div key={idx} className="rename-list-input__row">
          <select
            value={rename.old_name || ''}
            onChange={(e) => updateRename(idx, 'old_name', e.target.value)}
            className="rename-list-input__select"
          >
            <option value="">Colonna...</option>
            {columns.map((col, cidx) => (
              <option key={`${col.name}-${cidx}`} value={col.name}>{col.name}</option>
            ))}
          </select>
          <span className="rename-list-input__arrow">→</span>
          <input
            type="text"
            value={rename.new_name || ''}
            onChange={(e) => updateRename(idx, 'new_name', e.target.value)}
            placeholder="Nuovo nome"
            className="rename-list-input__input"
          />
          <button 
            type="button" 
            onClick={() => removeRename(idx)}
            className="rename-list-input__remove"
          >
            ×
          </button>
        </div>
      ))}
      <div className="rename-list-input__buttons">
        <button 
          type="button" 
          onClick={addRename}
          className="rename-list-input__add"
        >
          + Aggiungi rinomina
        </button>
        {columns.length > 0 && (
          <button 
            type="button" 
            onClick={loadAllColumns}
            className="rename-list-input__add rename-list-input__add--load-all"
          >
            📋 Carica tutte ({columns.length})
          </button>
        )}
        <button 
          type="button" 
          onClick={() => { setShowCsvPaste(!showCsvPaste); setCsvError(''); }}
          className={`rename-list-input__add rename-list-input__add--csv ${showCsvPaste ? 'rename-list-input__add--csv-active' : ''}`}
        >
          📝 Incolla CSV
        </button>
      </div>

      {showCsvPaste && (
        <div className="rename-list-input__csv-panel">
          <div className="rename-list-input__csv-hint">
            Formato: <code>nome_originale, nuovo_nome</code> (una per riga)
            <br />Separatori accettati: virgola, punto e virgola, tab
          </div>
          <textarea
            className="rename-list-input__csv-textarea"
            value={csvText}
            onChange={(e) => setCsvText(e.target.value)}
            placeholder={"col_vecchia, col_nuova\naltro_campo, nuovo_campo\n..."}
            rows={6}
            onMouseDown={(e) => e.stopPropagation()}
          />
          {csvError && (
            <div className="rename-list-input__csv-error">
              {csvError}
            </div>
          )}
          <button 
            type="button" 
            onClick={parseCsv}
            className="rename-list-input__csv-apply"
            disabled={!csvText.trim()}
          >
            ✓ Applica rinominazioni
          </button>
        </div>
      )}
    </div>
  );
};

// Componente per mascheramento dati
const MaskingListInput = ({ value, onChange, columns }) => {
  const maskings = Array.isArray(value) ? value : [];
  const maskTypes = [
    { value: 'cf', label: '🪪 Codice Fiscale' },
    { value: 'email', label: '📧 Email' },
    { value: 'phone', label: '📞 Telefono' },
    { value: 'iban', label: '🏦 IBAN' },
    { value: 'custom', label: '⚙️ Personalizzato' }
  ];

  const addMasking = () => {
    onChange([...maskings, { column: '', mask_type: 'cf', chars_start: 3, chars_end: 3, mask_char: '*' }]);
  };

  const updateMasking = (idx, field, newValue) => {
    const updated = [...maskings];
    updated[idx] = { ...updated[idx], [field]: newValue };
    onChange(updated);
  };

  const removeMasking = (idx) => {
    onChange(maskings.filter((_, i) => i !== idx));
  };

  return (
    <div className="masking-list-input">
      {maskings.map((mask, idx) => (
        <div key={idx} className="masking-list-input__item">
          <div className="masking-list-input__row">
            <select
              value={mask.column || ''}
              onChange={(e) => updateMasking(idx, 'column', e.target.value)}
              className="masking-list-input__col-select"
            >
              <option value="">Colonna</option>
              {columns.map((col, idx) => (
                <option key={`${col.name}-${idx}`} value={col.name}>{col.name}</option>
              ))}
            </select>
            <select
              value={mask.mask_type || 'cf'}
              onChange={(e) => updateMasking(idx, 'mask_type', e.target.value)}
              className="masking-list-input__type-select"
            >
              {maskTypes.map(mt => (
                <option key={mt.value} value={mt.value}>{mt.label}</option>
              ))}
            </select>
            <button 
              type="button" 
              onClick={() => removeMasking(idx)}
              className="masking-list-input__remove"
            >
              ×
            </button>
          </div>
          {mask.mask_type === 'custom' && (
            <div className="masking-list-input__custom">
              <label>
                <span>Inizio</span>
                <input
                  type="number"
                  value={mask.chars_start ?? 3}
                  onChange={(e) => updateMasking(idx, 'chars_start', parseInt(e.target.value) || 0)}
                  min={0}
                  max={20}
                  className="masking-list-input__num"
                />
              </label>
              <label>
                <span>Fine</span>
                <input
                  type="number"
                  value={mask.chars_end ?? 3}
                  onChange={(e) => updateMasking(idx, 'chars_end', parseInt(e.target.value) || 0)}
                  min={0}
                  max={20}
                  className="masking-list-input__num"
                />
              </label>
              <label>
                <span>Char</span>
                <input
                  type="text"
                  value={mask.mask_char ?? '*'}
                  onChange={(e) => updateMasking(idx, 'mask_char', e.target.value.slice(0, 1) || '*')}
                  maxLength={1}
                  className="masking-list-input__char"
                />
              </label>
            </div>
          )}
        </div>
      ))}
      <button 
        type="button" 
        onClick={addMasking}
        className="masking-list-input__add"
      >
        + Aggiungi colonna da mascherare
      </button>
    </div>
  );
};

// Componente per condizioni (Case When)
const ConditionListInput = ({ value, onChange }) => {
  const conditions = Array.isArray(value) ? value : [];
  const operators = [
    { value: '==', label: 'Uguale (=)' },
    { value: '!=', label: 'Diverso (≠)' },
    { value: '>', label: 'Maggiore (>)' },
    { value: '>=', label: 'Maggiore o uguale (≥)' },
    { value: '<', label: 'Minore (<)' },
    { value: '<=', label: 'Minore o uguale (≤)' },
    { value: 'contains', label: 'Contiene' },
    { value: 'startswith', label: 'Inizia con' },
    { value: 'endswith', label: 'Finisce con' },
    { value: 'between', label: 'Tra (min,max)' },
    { value: 'isnull', label: 'È nullo' },
    { value: 'notnull', label: 'Non è nullo' }
  ];

  const addCondition = () => {
    onChange([...conditions, { operator: '==', value: '', result: '' }]);
  };

  const updateCondition = (idx, field, newValue) => {
    const updated = [...conditions];
    updated[idx] = { ...updated[idx], [field]: newValue };
    onChange(updated);
  };

  const removeCondition = (idx) => {
    onChange(conditions.filter((_, i) => i !== idx));
  };

  const needsValue = (op) => !['isnull', 'notnull'].includes(op);

  return (
    <div className="condition-list-input">
      {conditions.map((cond, idx) => (
        <div key={idx} className="condition-list-input__item">
          <div className="condition-list-input__row">
            <select
              value={cond.operator || '=='}
              onChange={(e) => updateCondition(idx, 'operator', e.target.value)}
              className="condition-list-input__op-select"
            >
              {operators.map(op => (
                <option key={op.value} value={op.value}>{op.label}</option>
              ))}
            </select>
            {needsValue(cond.operator) && (
              <input
                type="text"
                value={cond.value || ''}
                onChange={(e) => updateCondition(idx, 'value', e.target.value)}
                placeholder={cond.operator === 'between' ? 'min,max' : 'valore'}
                className="condition-list-input__value"
              />
            )}
            <button 
              type="button" 
              onClick={() => removeCondition(idx)}
              className="condition-list-input__remove"
            >
              ×
            </button>
          </div>
          <div className="condition-list-input__result-row">
            <span className="condition-list-input__arrow">→</span>
            <input
              type="text"
              value={cond.result || ''}
              onChange={(e) => updateCondition(idx, 'result', e.target.value)}
              placeholder="risultato"
              className="condition-list-input__result"
            />
          </div>
        </div>
      ))}
      <button 
        type="button" 
        onClick={addCondition}
        className="condition-list-input__add"
      >
        + Aggiungi condizione
      </button>
    </div>
  );
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================

const ParameterInput = ({ 
  param, 
  value, 
  onChange, 
  columns = [],
  sourceLabel = null  // "⬅ SX" o "➡ DX" per colonne join
}) => {
  const {
    id,
    type,
    label,
    description,
    required,
    default: defaultValue,
    placeholder,
    options,
    min,
    max,
    step,
    rows,
    filter,
    source  // "left" o "right" dal registry
  } = param;
  
  // Determina sourceLabel automaticamente se non passato
  const effectiveSourceLabel = sourceLabel || (
    source === 'left' ? '⬅' : 
    source === 'right' ? '➡' : 
    null
  );

  // Render input in base al tipo
  const renderInput = () => {
    switch (type) {
      
      case 'string':
        return (
          <input
            type="text"
            value={value ?? defaultValue ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder || `Inserisci ${label.toLowerCase()}`}
            className="parameter-input__field"
          />
        );
      
      case 'number':
        return (
          <input
            type="number"
            value={value ?? defaultValue ?? ''}
            onChange={(e) => {
              const val = e.target.value;
              onChange(val === '' ? null : parseFloat(val));
            }}
            min={min}
            max={max}
            step={step || 1}
            placeholder={placeholder}
            className="parameter-input__field"
          />
        );
      
      case 'boolean':
        return (
          <label className="parameter-input__checkbox">
            <input
              type="checkbox"
              checked={value ?? defaultValue ?? false}
              onChange={(e) => onChange(e.target.checked)}
            />
            <span className="parameter-input__checkbox-label">{label}</span>
          </label>
        );
      
      case 'select':
        return (
          <select
            value={value ?? defaultValue ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className="parameter-input__field"
          >
            {!required && <option value="">-- Seleziona --</option>}
            {(options || []).map(opt => {
              const optValue = typeof opt === 'object' ? opt.value : opt;
              const optLabel = typeof opt === 'object' ? opt.label : opt;
              return (
                <option key={optValue} value={optValue}>{optLabel}</option>
              );
            })}
          </select>
        );
      
      case 'column':
      case 'geometry_column':
        return (
          <ColumnSelector
            columns={columns}
            value={value}
            onChange={onChange}
            placeholder={placeholder || `Seleziona ${label.toLowerCase()}`}
            filter={type === 'geometry_column' ? 'geometry' : filter}
            sourceLabel={effectiveSourceLabel}
          />
        );
      
      case 'columns':
        return (
          <ColumnSelector
            columns={columns}
            value={value}
            onChange={onChange}
            multiple={true}
            filter={filter}
            sourceLabel={effectiveSourceLabel}
          />
        );
      
      case 'expression':
        return (
          <div className="parameter-input__expression">
            <textarea
              value={value ?? defaultValue ?? ''}
              onChange={(e) => onChange(e.target.value)}
              placeholder={placeholder || "Es: colonna1 * 2 + colonna2"}
              rows={rows || 3}
              className="parameter-input__field parameter-input__field--textarea"
            />
            {columns.length > 0 && (
              <div className="parameter-input__expression-help">
                Colonne: {columns.map(c => c.name).join(', ')}
              </div>
            )}
          </div>
        );
      
      case 'string_list':
        return (
          <StringListInput
            value={value}
            onChange={onChange}
            placeholder={placeholder}
          />
        );
      
      case 'aggregation_list':
        return (
          <AggregationListInput
            value={value}
            onChange={onChange}
            columns={columns}
          />
        );
      
      case 'rename_list':
        return (
          <RenameListInput
            value={value}
            onChange={onChange}
            columns={columns}
          />
        );
      
      case 'masking_list':
        return (
          <MaskingListInput
            value={value}
            onChange={onChange}
            columns={columns}
          />
        );
      
      case 'condition_list':
        return (
          <ConditionListInput
            value={value}
            onChange={onChange}
          />
        );
      
      case 'pivot_mapping':
        return (
          <PivotMappingBuilder
            value={value}
            onChange={onChange}
          />
        );
      
      case 'column_order':
        return (
          <ColumnOrderInput
            value={value}
            onChange={onChange}
            columns={columns}
          />
        );
      
      default:
        return (
          <input
            type="text"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className="parameter-input__field"
          />
        );
    }
  };

  // Non mostrare label per checkbox (è inline)
  const showLabel = type !== 'boolean';

  return (
    <div className="parameter-input">
      {showLabel && (
        <label className="parameter-input__label">
          {label}
          {required && <span className="parameter-input__required">*</span>}
          {description && (
            <span className="parameter-input__info" title={description}>
              ℹ️
            </span>
          )}
        </label>
      )}
      
      <div className="parameter-input__control">
        {renderInput()}
      </div>
    </div>
  );
};

export default memo(ParameterInput);