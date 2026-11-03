/**
 * FormulaPanel — più colonne calcolate in un passo, in ordine (Fase 4, M4;
 * backend B4): una formula puo' usare le colonne create da quelle sopra.
 * Legge anche la formula singola di prima.
 */
import React, { useMemo, useRef } from 'react';
import useDraftList from './useDraftList';
import {
  FORMULA_LEGACY_CLEAR, emptyFormula, formulasToSave, readFormulaItems, validateFormulas,
} from './panelConfig';
import './panels.css';

export default function FormulaPanel({ config, updateConfig, inputColumns = [], disabled }) {
  const saved = useMemo(() => readFormulaItems(config), [config]);
  const [rows, setRows, error] = useDraftList(saved, {
    validate: validateFormulas,
    toSaved: formulasToSave,
    write: (items) => updateConfig({ ...FORMULA_LEGACY_CLEAR, items }),
  });
  const list = rows.length ? rows : [emptyFormula()];
  const focused = useRef({ index: 0, el: null });
  const set = (i, patch) => setRows(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i) => {
    focused.current = { index: 0, el: null };
    setRows(list.filter((_, j) => j !== i));
  };

  // Clic su una colonna: la inserisce nella formula su cui si stava scrivendo.
  const insert = (name) => {
    const { index, el } = focused.current;
    const i = Math.min(index, list.length - 1);
    const current = list[i]?.formula || '';
    const at = el && el === document.activeElement ? el.selectionStart : current.length;
    set(i, { formula: `${current.slice(0, at)}${name}${current.slice(at)}` });
  };

  const earlier = (i) => list.slice(0, i).map(r => r.new_column).filter(Boolean);

  return (
    <div className="ppanel">
      {list.map((it, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div className="ppanel__card" key={i}>
          <div className="ppanel__row">
            <input className="ppanel__input" value={it.new_column ?? ''} disabled={disabled}
              onChange={e => set(i, { new_column: e.target.value })}
              placeholder="nome nuova colonna" aria-label="Nome nuova colonna" />
            <button type="button" className="ppanel__x" disabled={disabled} aria-label="Rimuovi formula"
              onClick={() => remove(i)}>×</button>
          </div>
          <textarea className="ppanel__input ppanel__mono" rows={2} value={it.formula ?? ''} disabled={disabled}
            onFocus={e => { focused.current = { index: i, el: e.target }; }}
            onChange={e => set(i, { formula: e.target.value })}
            placeholder="es. importo / 12" aria-label="Espressione" />
          {i > 0 && earlier(i).length > 0 && (
            <div className="ppanel__hint">Può usare anche: {earlier(i).join(', ')}</div>
          )}
        </div>
      ))}
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è completa.</div>}
      <button type="button" className="ppanel__link" disabled={disabled}
        onClick={() => setRows([...list, emptyFormula()])}>＋ Aggiungi colonna calcolata</button>
      {inputColumns.length > 0 && (
        <div className="ppanel__field">
          <span className="ppanel__label">Colonne disponibili (clic per inserire)</span>
          <div className="ppanel__chips">
            {inputColumns.map(c => (
              <button type="button" key={c.name} className="ppanel__chip" disabled={disabled}
                onMouseDown={e => e.preventDefault()} onClick={() => insert(c.name)}>{c.name}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
