/**
 * FilterPanel — Filtra con più condizioni e "è uno di / non è uno di"
 * (Fase 4, M4; backend B3). Legge anche il filtro singolo di prima.
 * Regole di scrittura in panelConfig (lista valida, id delle colonne).
 *
 * UI-3 (U20): gruppi di condizioni. Dentro un gruppo tutte vere (E), tra i
 * gruppi almeno uno (O); stessa regola e stesso formato del filtro di
 * Estrai (utils/filterConditions: `group` sulla condizione, assente con un
 * solo gruppo, quindi i filtri esistenti restano tutti in E).
 */
import React, { useMemo } from 'react';
import useDraftList from './useDraftList';
import {
  FILTER_LEGACY_CLEAR, FILTER_OPERATORS, LIST_OPERATORS, NO_VALUE_OPERATORS,
  conditionsToSave, emptyCondition, readFilterConditions, validateConditions,
} from './panelConfig';
import { addConditionGroup, addConditionToGroup, conditionGroups } from '../../../utils/filterConditions';
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
  const groups = conditionGroups(list);

  return (
    <div className="ppanel">
      {groups.map((g, gi) => (
        <div key={g.group} className="ppanel__group" role="group" aria-label={`Gruppo ${gi + 1}: tutte le condizioni vere (E)`}>
          {gi > 0 && <div className="ppanel__or" aria-hidden="true">oppure (O)</div>}
          {groups.length > 1 && <div className="ppanel__group-title">Gruppo {gi + 1} · tutte vere (E)</div>}
          {g.items.map(({ row: c, index: i }) => (
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
          <button type="button" className="ppanel__link" disabled={disabled}
            onClick={() => setRows(addConditionToGroup(list, g.group, emptyCondition()))}>＋ Aggiungi una condizione (E)</button>
        </div>
      ))}
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è completa.</div>}
      <button type="button" className="ppanel__link" disabled={disabled}
        onClick={() => setRows(addConditionGroup(list, emptyCondition()))}>＋ Aggiungi un gruppo O</button>
      <div className="ppanel__note">
        Le condizioni di un gruppo devono essere tutte vere (E); con più gruppi basta che ne sia vero uno (O).
        Con «è uno di» / «non è uno di» un solo filtro sostituisce più nodi Filtra in fila.
      </div>
    </div>
  );
}
