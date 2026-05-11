/**
 * AggregationListInput.jsx — Builder lista aggregazioni
 * Usato dal dispatcher per il tipo `aggregation_list`. Selezione
 * colonna + funzione (count/sum/mean/min/max/first/last/concat) +
 * parametri extra per `concat` (separator/distinct/skip_null).
 *
 * Pure presentational: nessun useState locale, valore controllato
 * dal parent.
 */
import React from 'react';

const AggregationListInput = ({ value, onChange, columns }) => {
  const aggregations = Array.isArray(value) ? value : [];
  const aggFunctions = ['count', 'sum', 'mean', 'min', 'max', 'first', 'last', 'concat'];

  const addAggregation = () => {
    onChange([...aggregations, { column: '', function: 'count', alias: '' }]);
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
            {columns.map((col, cidx) => (
              <option key={`${col.name}-${cidx}`} value={col.name}>{col.name}</option>
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
          <input
            type="text"
            value={agg.alias || ''}
            onChange={(e) => updateAggregation(idx, 'alias', e.target.value)}
            placeholder="Nome output (opzionale)"
            title="Alias colonna output (es. n_customers, total_revenue)"
          />
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

export default AggregationListInput;
