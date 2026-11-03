/**
 * FilterPanel — Filtra con più condizioni in E e "è tra / non è tra"
 * (Fase 4, M4; backend B3). Legge anche il filtro singolo di prima.
 * Regole di scrittura in panelConfig (lista valida, id delle colonne).
 */
import React, { useMemo } from 'react';
import useDraftList from './useDraftList';
import {
  FILTER_LEGACY_CLEAR, FILTER_OPERATORS, LIST_OPERATORS, NO_VALUE_OPERATORS,
  conditionsToSave, emptyCondition, readFilterConditions, validateConditions,
} from './panelConfig';
import './panels.css';

export default function FilterPanel({ config, updateConfig, inputColumns = [], disabled }) {
  const saved = useMemo(() => readFilterConditions(config, inputColumns), [config, inputColumns]);
  const [rows, setRows, error] = useDraftList(saved, {
    validate: validateConditions,
    toSaved: (next) => conditionsToSave(next, inputColumns),
    write: (conditions) => updateConfig({ ...FILTER_LEGACY_CLEAR, conditions }),
  });
  const list = rows.length ? rows : [emptyCondition()];
  const set = (i, patch) => setRows(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <div className="ppanel">
      {list.map((c, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div className="ppanel__card" key={i}>
          <div className="ppanel__row">
            <select className="ppanel__input" value={c.column || ''} disabled={disabled}
              onChange={e => set(i, { column: e.target.value, column_id: undefined })} aria-label="Colonna">
              <option value="">— colonna —</option>
              {inputColumns.map(col => <option key={col.name} value={col.name}>{col.name}{col.type ? ` (${col.type})` : ''}</option>)}
              {c.column && !inputColumns.some(col => col.name === c.column) && <option value={c.column}>{c.column} (non trovata)</option>}
            </select>
            <button type="button" className="ppanel__x" disabled={disabled} aria-label="Rimuovi condizione"
              onClick={() => setRows(list.filter((_, j) => j !== i))}>×</button>
          </div>
          <div className="ppanel__row">
            <select className="ppanel__input ppanel__input--op" value={c.operator || '=='} disabled={disabled}
              onChange={e => set(i, { operator: e.target.value })} aria-label="Operatore">
              {FILTER_OPERATORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {!NO_VALUE_OPERATORS.includes(c.operator) && (
              <input className="ppanel__input" value={Array.isArray(c.value) ? c.value.join(', ') : (c.value ?? '')} disabled={disabled}
                onChange={e => set(i, { value: e.target.value })}
                placeholder={LIST_OPERATORS.includes(c.operator) ? 'valori separati da virgola' : 'valore (vuoto = testo vuoto)'}
                aria-label="Valore" />
            )}
          </div>
        </div>
      ))}
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è completa.</div>}
      <button type="button" className="ppanel__link" disabled={disabled}
        onClick={() => setRows([...list, emptyCondition()])}>＋ Aggiungi condizione</button>
      <div className="ppanel__note">
        Tutte le condizioni devono essere vere (E). Con «è tra» / «non è tra» un solo filtro sostituisce più nodi Filtra in fila.
      </div>
    </div>
  );
}
