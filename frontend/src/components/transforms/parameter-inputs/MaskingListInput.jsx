/**
 * MaskingListInput.jsx — Builder mascheramento PII per colonna
 *
 * Estratto da `ParameterInput.jsx` in `FE-PHASE2-SPLIT-002A`.
 * Usato dal dispatcher per il tipo `masking_list`. Tipi mask:
 * cf, email, phone, iban, custom (con chars_start/end + mask_char).
 *
 * Pure presentational: nessun useState locale.
 */
import React from 'react';

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
              {columns.map((col, cidx) => (
                <option key={`${col.name}-${cidx}`} value={col.name}>{col.name}</option>
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

export default MaskingListInput;
