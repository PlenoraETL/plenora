/**
 * ParameterInput.jsx — Dispatcher di un singolo parametro per tipo
 *
 * Tipi supportati:
 * - string: input testo
 * - number: campo numerico (FieldInput) con min/max applicati
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
 * Questo file resta dispatcher su `param.type`.
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
import RuntimeInputBindingControl from './RuntimeInputBindingControl';
import FieldInput from '../common/FieldInput';
import { decimal } from '../../utils/fieldCodecs';
import ValueMappingInput from './parameter-inputs/ValueMappingInput';
import FilterPanel from './panels/FilterPanel';
import FormulaPanel from './panels/FormulaPanel';
import SelectColumnsPanel from './panels/SelectColumnsPanel';

/**
 * Tipi di parametro del registry con un editor dedicato. Un test confronta
 * questo elenco con il registry vero: un tipo nuovo nel backend non puo'
 * finire nel campo di testo generico (che scriverebbe il formato sbagliato).
 * Le liste di Filtra, Formula e Seleziona e rinomina usano gli stessi
 * pannelli del pannello laterale: un solo editor, un solo formato.
 */
export const HANDLED_PARAM_TYPES = [
  'string', 'number', 'boolean', 'select', 'column', 'geometry_column', 'columns', 'expression',
  'string_list', 'aggregation_list', 'rename_list', 'masking_list', 'condition_list', 'pivot_mapping',
  'column_order', 'filter_conditions', 'formula_items', 'select_columns_list', 'value_mapping',
];

const ParameterInput = ({
  param,
  value,
  onChange,
  columns = [],
  sourceLabel = null,
  targetNodeId = null,
  transformDefinition = null,
  // Configurazione dell'operazione: altri parametri che un editor legge
  // (Mappa valori: la colonna da mappare).
  config = null,
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

  // Fase 5 T5 (R8.14): si mostra il default SOLO se il parametro manca
  // (undefined): e' il caso in cui il runtime lo applica (manipola/
  // node_validator._runtime_default). Un valore presente, anche vuoto o
  // null, si mostra com'e': quello che si vede e' quello che gira.
  const shown = value === undefined ? defaultValue : value;
  const numberCodec = decimal({ min, max, optional: true, empty: null });

  // Render input in base al tipo
  const renderInput = () => {
    switch (type) {

      case 'string':
        return (
          <input
            type="text"
            value={shown ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder || `Inserisci ${label.toLowerCase()}`}
            className="parameter-input__field"
          />
        );

      case 'number':
        return (
          // Un convertitore per tipo (utils/fieldCodecs): min/max applicati,
          // un testo non valido e' l'errore del campo e non si scrive.
          <FieldInput
            codec={numberCodec}
            value={shown ?? null}
            onCommit={onChange}
            placeholder={placeholder}
            aria-label={label}
            className="parameter-input__field"
          />
        );

      case 'boolean':
        return (
          <label className="parameter-input__checkbox">
            <input
              type="checkbox"
              checked={Boolean(shown ?? false)}
              onChange={(e) => onChange(e.target.checked)}
            />
            <span className="parameter-input__checkbox-label">{label}</span>
          </label>
        );

      case 'select':
        return (
          <select
            value={shown ?? ''}
            // "-- Seleziona --" = parametro assente, mai la stringa vuota
            // (il backend la rifiuterebbe come valore della scelta).
            onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
            className="parameter-input__field"
          >
            {/* Obbligatorio senza valore: nessuna opzione preselezionata in
                silenzio (prima si vedeva la prima, non salvata). */}
            {(!required || shown === undefined || shown === null || shown === '') && (
              <option value="" disabled={Boolean(required)}>-- Seleziona --</option>
            )}
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
              value={shown ?? ''}
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

      case 'filter_conditions':
        return (
          <FilterPanel
            config={{ conditions: Array.isArray(value) ? value : [] }}
            updateConfig={(patch) => onChange(patch.conditions)}
            inputColumns={columns}
          />
        );

      case 'formula_items':
        return (
          <FormulaPanel
            config={{ items: Array.isArray(value) ? value : [] }}
            updateConfig={(patch) => onChange(patch.items)}
            inputColumns={columns}
          />
        );

      case 'select_columns_list':
        return (
          <SelectColumnsPanel
            config={{ columns: value }}
            updateConfig={(patch) => onChange(patch.columns)}
            inputColumns={columns}
          />
        );

      case 'value_mapping': {
        // «Riempi con i valori presenti»: la colonna e' il parametro colonna
        // della stessa operazione (Lookup: `column`, con il suo id).
        const columnParam = (transformDefinition?.parameters || []).find(p => p.type === 'column');
        return (
          <ValueMappingInput
            value={value}
            onChange={onChange}
            nodeId={targetNodeId}
            column={columnParam ? config?.[columnParam.id] : null}
            columnId={columnParam ? config?.[`${columnParam.id}_column_id`] : null}
          />
        );
      }

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
          {param.schema_contract && (
            <span className="parameter-input__schema-badge" title="Schema previsto nel canvas: non cambia l'esecuzione.">
              Schema
            </span>
          )}
          {description && (
            <span className="parameter-input__info" title={description}>
              ℹ️
            </span>
          )}
        </label>
      )}

      <RuntimeInputBindingControl
        targetNodeId={targetNodeId}
        param={param}
        transformDefinition={transformDefinition}
      />

      <div className="parameter-input__control">
        {renderInput()}
      </div>
    </div>
  );
};

export default memo(ParameterInput);
