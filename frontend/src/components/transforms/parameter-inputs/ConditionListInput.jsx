/**
 * ConditionListInput.jsx — Builder condizioni Case-When
 *
 * Estratto da `ParameterInput.jsx` in `FE-PHASE2-SPLIT-002A`.
 * Usato dal dispatcher per il tipo `condition_list`. Operatori:
 * == != > >= < <= contains startswith endswith between isnull notnull.
 * Per `between` il valore e' "min,max"; per `isnull/notnull` il
 * valore non viene mostrato.
 *
 * Pure presentational: nessun useState locale.
 */
import React from 'react';

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

export default ConditionListInput;
