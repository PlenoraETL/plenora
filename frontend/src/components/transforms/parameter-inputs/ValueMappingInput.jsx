/**
 * ValueMappingInput — "Mappa valori" (Lookup): righe valore → nuovo valore,
 * scritte a mano (non sono nomi di colonna). Legge la mappatura in ogni
 * formato salvato (dizionario, {from,to}, {old_name,new_name}) e scrive
 * sempre [{from, to}] (backend shared/execution/lookup_mapping.py).
 * Le righe senza valore di partenza restano qui finche' non sono complete.
 */
import React, { useMemo } from 'react';
import useDraftList from '../panels/useDraftList';
import { readValueMapping } from '../../../utils/lookupMapping';
import '../panels/panels.css';

const isEmpty = (r) => !String(r.from ?? '').trim() && !String(r.to ?? '').trim();

export default function ValueMappingInput({ value, onChange, disabled }) {
  const saved = useMemo(() => readValueMapping(value), [value]);
  const [rows, setRows, error] = useDraftList(saved, {
    validate: (next) => {
      const live = next.filter(r => !isEmpty(r));
      if (live.some(r => r.from === '')) return 'Ogni riga ha bisogno del valore di partenza';
      const dup = live.map(r => r.from).find((f, i, all) => all.indexOf(f) !== i);
      return dup !== undefined ? `Il valore "${dup}" compare due volte` : null;
    },
    toSaved: (next) => next.filter(r => !isEmpty(r)).map(r => ({ from: r.from, to: r.to })),
    write: onChange,
  });
  const list = rows.length ? rows : [{ from: '', to: '' }];
  const set = (i, patch) => setRows(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <div className="ppanel">
      {list.map((r, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div className="ppanel__row" key={i}>
          <input className="ppanel__input" value={r.from} disabled={disabled} placeholder="valore"
            onChange={e => set(i, { from: e.target.value })} aria-label="Valore di partenza" />
          <span aria-hidden="true">→</span>
          <input className="ppanel__input" value={r.to} disabled={disabled} placeholder="diventa"
            onChange={e => set(i, { to: e.target.value })} aria-label="Nuovo valore" />
          <button type="button" className="ppanel__x" disabled={disabled} aria-label="Rimuovi riga"
            onClick={() => setRows(list.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è risolto.</div>}
      <button type="button" className="ppanel__link" disabled={disabled}
        onClick={() => setRows([...list, { from: '', to: '' }])}>＋ Aggiungi riga</button>
    </div>
  );
}
