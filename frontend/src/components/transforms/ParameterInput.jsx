/**
 * ParameterInput.jsx — Dispatcher di un singolo parametro per tipo
 *
 * Tipi supportati:
 * - string: input testo
 * - number: input numerico con min/max/step
 * - boolean: checkbox
 * - select: dropdown
 * - column / geometry_column: selettore colonna (singola)
 * - columns: selettore colonne multiple
 * - expression: textarea per formule
 * - string_list: lista stringhe con tag
 * - aggregation_list: builder aggregazioni
 * - rename_list: rinominazioni colonne (con CSV paste)
 * - masking_list: mascheramento PII per colonna
 * - condition_list: condizioni case-when
 * - column_order: drag & drop riordinamento colonne
 * - pivot_mapping: builder pivot
 *
 * I sub-component sono stati estratti in `parameter-inputs/` da
 * `FE-PHASE2-SPLIT-002A`. Questo file resta dispatcher su
 * `param.type`. Behavior e props pubbliche invariate.
 *
 * Posizione: /components/transforms/ParameterInput.jsx
 */

import React, { memo } from 'react';
import './ParameterInput.css';
import PivotMappingBuilder from './PivotMappingBuilder';
import './PivotMappingBuilder.css';
import ColumnSelector from './parameter-inputs/ColumnSelector';
import StringListInput from './parameter-inputs/StringListInput';
import AggregationListInput from './parameter-inputs/AggregationListInput';
import ColumnOrderInput from './parameter-inputs/ColumnOrderInput';
import RenameListInput from './parameter-inputs/RenameListInput';
import MaskingListInput from './parameter-inputs/MaskingListInput';
import ConditionListInput from './parameter-inputs/ConditionListInput';

// ============================================================================
// MAIN COMPONENT (dispatcher)
// ============================================================================

const ParameterInput = ({
  param,
  value,
  onChange,
  columns = [],
  sourceLabel = null  // "⬅ SX" o "➡ DX" per colonne join
}) => {
  const {
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
