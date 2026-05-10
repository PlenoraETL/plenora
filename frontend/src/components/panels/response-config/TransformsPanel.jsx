/**
 * TransformsPanel.jsx — Tab "Transforms" del ResponseConfigBuilder
 * Componente presentational: niente fetch, niente API, niente
 * useState (l'unico state UI residuo e' il `defaultValue=""` del
 * select "add transform source", letto via `getElementById` —
 * comportamento preservato byte-per-byte rispetto all'originale).
 *
 * Riceve `transforms`, `mappings` + 3 callback CRUD.
 *
 * La costante `TRANSFORM_OPERATIONS` vive co-locata qui perche'
 * usata solo da questo panel (le 11 operations + `none` sono lato
 * UI; il dispatch effettivo dei transform vive in
 * `responseConfigEngine.js::applyTransforms`).
 */
import React from 'react';

const TRANSFORM_OPERATIONS = [
  { id: 'none', label: 'Nessuna', icon: '−' },
  { id: 'kelvin_to_celsius', label: 'Kelvin → Celsius', icon: '🌡️' },
  { id: 'celsius_to_kelvin', label: 'Celsius → Kelvin', icon: '🌡️' },
  { id: 'round', label: 'Arrotonda', icon: '🔢', hasValue: true, valueLabel: 'Decimali' },
  { id: 'multiply', label: 'Moltiplica', icon: '✖️', hasValue: true, valueLabel: 'Fattore' },
  { id: 'divide', label: 'Dividi', icon: '➗', hasValue: true, valueLabel: 'Divisore' },
  { id: 'add', label: 'Aggiungi', icon: '➕', hasValue: true, valueLabel: 'Valore' },
  { id: 'subtract', label: 'Sottrai', icon: '➖', hasValue: true, valueLabel: 'Valore' },
  { id: 'uppercase', label: 'MAIUSCOLO', icon: 'Aa' },
  { id: 'lowercase', label: 'minuscolo', icon: 'aa' },
  { id: 'prefix', label: 'Prefisso', icon: '←', hasValue: true, valueLabel: 'Prefisso' },
  { id: 'suffix', label: 'Suffisso', icon: '→', hasValue: true, valueLabel: 'Suffisso' },
];

export default function TransformsPanel({
  transforms,
  mappings,
  updateTransform,
  removeTransform,
  addTransform,
}) {
  return (
    <div className="response-config-builder__panel">
      <div className="response-config-builder__section-header">
        <h4>⚙️ Trasformazioni</h4>
        <p>Crea nuove colonne con valori calcolati</p>
      </div>

      {/* Lista trasformazioni */}
      {transforms.length === 0 ? (
        <div className="response-config-builder__empty-section">
          <p>Nessuna trasformazione definita.</p>
        </div>
      ) : (
        <div className="response-config-builder__transforms-list">
          {transforms.map((t, idx) => {
            const op = TRANSFORM_OPERATIONS.find(o => o.id === t.operation);
            return (
              <div key={idx} className="response-config-builder__transform-item">
                <div className="response-config-builder__transform-row">
                  <label>Colonna output:</label>
                  <input
                    type="text"
                    value={t.column}
                    onChange={e => updateTransform(idx, 'column', e.target.value)}
                    className="response-config-builder__input"
                  />
                </div>
                <div className="response-config-builder__transform-row">
                  <label>Colonna sorgente:</label>
                  <select
                    value={t.source}
                    onChange={e => updateTransform(idx, 'source', e.target.value)}
                    className="response-config-builder__select"
                  >
                    <option value="">-- seleziona --</option>
                    {mappings.map(m => (
                      <option key={m.column} value={m.column}>{m.column}</option>
                    ))}
                  </select>
                </div>
                <div className="response-config-builder__transform-row">
                  <label>Operazione:</label>
                  <select
                    value={t.operation}
                    onChange={e => updateTransform(idx, 'operation', e.target.value)}
                    className="response-config-builder__select"
                  >
                    {TRANSFORM_OPERATIONS.map(o => (
                      <option key={o.id} value={o.id}>{o.icon} {o.label}</option>
                    ))}
                  </select>
                </div>
                {op?.hasValue && (
                  <div className="response-config-builder__transform-row">
                    <label>{op.valueLabel}:</label>
                    <input
                      type="text"
                      value={t.value || ''}
                      onChange={e => updateTransform(idx, 'value', e.target.value)}
                      className="response-config-builder__input response-config-builder__input--small"
                    />
                  </div>
                )}
                <button type="button"
                  onClick={() => removeTransform(idx)}
                  className="response-config-builder__btn response-config-builder__btn--danger"
                >
                  🗑️ Rimuovi
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Aggiungi trasformazione */}
      {mappings.length > 0 && (
        <div className="response-config-builder__add-transform">
          <select
            id="add-transform-source"
            className="response-config-builder__select"
            defaultValue=""
          >
            <option value="">-- Scegli colonna sorgente --</option>
            {mappings.map(m => (
              <option key={m.column} value={m.column}>{m.column}</option>
            ))}
          </select>
          <button type="button"
            onClick={() => {
              const select = document.getElementById('add-transform-source');
              if (select.value) {
                addTransform(select.value);
                select.value = '';
              }
            }}
            className="response-config-builder__btn response-config-builder__btn--primary"
          >
            + Aggiungi trasformazione
          </button>
        </div>
      )}
    </div>
  );
}
