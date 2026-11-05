/**
 * OptionSelect — <select> che mostra SEMPRE il valore salvato (Fase 5, T6,
 * R3.18, R3.21).
 *
 * Regola unica per le select su un elenco che cambia (colonne, fogli, layer):
 *  - valore vuoto: si vede il segnaposto dichiarato, mai la prima opzione
 *    (prima `value={v || options[0]}` mostrava [0] e salvava null);
 *  - valore che non e' piu' tra le opzioni (colonna sparita o rinominata a
 *    monte): si vede come «⚠️ nome (non più disponibile)», selezionato,
 *    invece di un segnaposto che lo fa sembrare vuoto o della prima opzione.
 *
 * `options`: stringhe o { value, label }. Il test
 * tests/shared/canvasT6Rules.contract.test.jsx vieta `value={x || lista[0]}`.
 */
import React from 'react';

const normalize = (o) => (typeof o === 'object' && o !== null ? o : { value: o, label: String(o) });

export function missingValue(value, options) {
  if (value === null || value === undefined || value === '') return null;
  return (options || []).some(o => normalize(o).value === value) ? null : value;
}

export default function OptionSelect({ value, options, onChange, placeholder = '— scegli —', missingSuffix = 'non più disponibile', ...rest }) {
  const list = (options || []).map(normalize);
  const missing = missingValue(value, list);
  return (
    <select value={value ?? ''} onChange={onChange} {...rest}>
      {(value === null || value === undefined || value === '') && <option value="">{placeholder}</option>}
      {missing !== null && <option value={missing}>⚠️ {missing} ({missingSuffix})</option>}
      {list.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
