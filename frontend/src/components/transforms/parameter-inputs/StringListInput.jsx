/**
 * StringListInput.jsx — Lista di stringhe con tag chips
 * Usato dal dispatcher per il tipo `string_list`. Tiene state
 * locale `inputValue` per il textbox di append; il valore della
 * lista vive nel parent (controlled).
 *
 * Fase 5 T5 (R8.23): il testo scritto e non confermato con Invio si
 * aggiunge anche al blur (prima si perdeva uscendo dal campo).
 */
import React, { useState } from 'react';

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
          onBlur={addValue}
          placeholder={placeholder}
        />
        <button type="button" onClick={addValue}>+</button>
      </div>
    </div>
  );
};

export default StringListInput;
