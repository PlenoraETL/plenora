/**
 * MaskingListInput.jsx — Builder mascheramento PII per colonna
 * Usato dal dispatcher per il tipo `masking_list`. Tipi mask:
 * cf, email, phone, iban, custom (con chars_start/end + mask_char).
 *
 * Pure presentational: nessun useState locale.
 *
 * Fase 5 T5 (N12): i numeri e il carattere passano dal campo unico
 * (FieldInput + utils/fieldCodecs): svuotando il campo non torna "0" o "*"
 * in silenzio, un valore fuori 0-20 e' l'errore del campo.
 */
import React from 'react';
import FieldInput from '../../common/FieldInput';
import { integer, text } from '../../../utils/fieldCodecs';

const CHARS = integer({ min: 0, max: 20 });
const MASK_CHAR = text({ optional: false, trim: false, minLength: 1, maxLength: 1 });

const MaskingListInput = ({ value, onChange, columns }) => {
  const maskings = Array.isArray(value) ? value : [];
  const maskTypes = [
    { value: 'cf', label: '🪪 Codice Fiscale' },
    { value: 'email', label: '📧 Email' },
    { value: 'phone', label: '📞 Telefono' },
    { value: 'iban', label: '🏦 IBAN' },
    { value: 'custom', label: '⚙️ Personalizzato' }
  ];

  const columnKey = (col) => col?.id || col?.column_id || col?.name;

  const addMasking = () => {
    onChange([...maskings, { column: '', column_id: null, mask_type: 'cf', chars_start: 3, chars_end: 3, mask_char: '*' }]);
  };

  const updateMasking = (idx, field, newValue) => {
    const updated = [...maskings];
    updated[idx] = { ...updated[idx], [field]: newValue };
    if (field === 'column') {
      const selectedColumn = columns.find(col => col.name === newValue);
      updated[idx].column_id = columnKey(selectedColumn) || null;
    }
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
                <option key={`${columnKey(col)}-${cidx}`} value={col.name}>{col.name}</option>
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
                <FieldInput
                  codec={CHARS}
                  value={mask.chars_start ?? 3}
                  onCommit={(v) => updateMasking(idx, 'chars_start', v)}
                  aria-label="Caratteri visibili all'inizio"
                  className="masking-list-input__num"
                />
              </label>
              <label>
                <span>Fine</span>
                <FieldInput
                  codec={CHARS}
                  value={mask.chars_end ?? 3}
                  onCommit={(v) => updateMasking(idx, 'chars_end', v)}
                  aria-label="Caratteri visibili alla fine"
                  className="masking-list-input__num"
                />
              </label>
              <label>
                <span>Char</span>
                <FieldInput
                  codec={MASK_CHAR}
                  value={mask.mask_char ?? '*'}
                  onCommit={(v) => updateMasking(idx, 'mask_char', v)}
                  maxLength={1}
                  aria-label="Carattere di mascheramento"
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
