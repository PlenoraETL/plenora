/**
 * ColumnsGrid — "Seleziona e rinomina" (Fase 4, M5): tieni, rinomina e
 * ordina le colonne in un'unica griglia. La usano l'operazione
 * select_columns e l'Output (Manipola, Arricchisci; Estrai senza ordine).
 *
 * Righe da panelConfig.gridRows: le colonne nuove a monte compaiono da
 * sole in fondo, tenute; quelle sparite restano segnalate. Il nome in
 * uscita vuoto significa "stesso nome" (si vede come segnaposto).
 * `onChange(rows)` riceve le righe solo quando la griglia e' valida
 * (gridError): nomi ripetuti a meta' di uno scambio o con spazi restano
 * qui finche' non sono a posto, la configurazione salvata non cambia.
 * `row.display` (facoltativo) e' il nome mostrato quando la chiave `name`
 * non e' leggibile (Estrai: tabella.colonna di un nodo preciso).
 * `identifier` (Estrai): i nomi finiscono in SQL, vedi gridError.
 */
import React, { useMemo, useState } from 'react';
import useDraftList from './useDraftList';
import { gridError, prettyName, stripJoinSuffix } from './panelConfig';
import './panels.css';
import { READ_ONLY_SAFE } from '../../common/readOnlySafe';

const sourceName = (r) => (r.display ? r.display.split('.').pop() : r.name);
// Confronto delle righe senza i campi solo di presentazione.
// eslint-disable-next-line no-unused-vars
const comparable = (rows) => rows.map(({ type, optional, ...r }) => r);

export default function ColumnsGrid({
  rows: savedRows, onChange, allowReorder = true, identifier = false, disabled = false, note, savedError = null,
}) {
  const [rows, update, error] = useDraftList(savedRows, {
    validate: (next) => gridError(next, { identifier }),
    toSaved: comparable,
    write: () => {},
  });
  // useDraftList decide se la griglia e' valida; la scrittura vera usa le righe.
  const change = (next) => {
    update(next);
    if (!gridError(next, { identifier })) onChange(next);
  };
  const [query, setQuery] = useState('');
  const [dragName, setDragName] = useState(null);
  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () => rows.filter(r => !q || (r.display || r.name).toLowerCase().includes(q) || (r.label || '').toLowerCase().includes(q)),
    [rows, q],
  );
  const kept = rows.filter(r => r.keep && !r.missing).length;
  const total = rows.filter(r => !r.missing).length;

  const setRow = (name, patch) => change(rows.map(r => (r.name === name ? { ...r, ...patch } : r)));
  const setAll = (fn) => change(rows.map(r => (visible.includes(r) && !r.missing && !r.fixed ? fn(r) : r)));
  const move = (name, toIndex) => {
    const from = rows.findIndex(r => r.name === name);
    if (from < 0 || toIndex < 0 || toIndex >= rows.length || from === toIndex) return;
    const next = rows.slice();
    const [row] = next.splice(from, 1);
    next.splice(toIndex, 0, row);
    change(next);
  };
  const relabel = (fn) => setAll((r) => {
    const label = fn(r.label || sourceName(r));
    return { ...r, label: label === sourceName(r) ? '' : label };
  });

  return (
    <div className={`pgrid${allowReorder ? '' : ' pgrid--fixed'}`}>
      <div className="pgrid__head">
        <span className="ppanel__label">Colonne in uscita · {kept} di {total}</span>
      </div>
      <div className="pgrid__tools">
        <input
          {...READ_ONLY_SAFE} className="ppanel__input pgrid__search"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Cerca colonna…"
          aria-label="Cerca colonna"
        />
        <button type="button" className="ppanel__btn" disabled={disabled} onClick={() => setAll(r => ({ ...r, keep: true }))}>Tutte</button>
        <button type="button" className="ppanel__btn" disabled={disabled} onClick={() => setAll(r => ({ ...r, keep: false }))}>Nessuna</button>
        {!identifier && (
          <button type="button" className="ppanel__btn" disabled={disabled} title="nome_colonna → Nome colonna"
            onClick={() => relabel(prettyName)}>Aa nomi leggibili</button>
        )}
        <button type="button" className="ppanel__btn" disabled={disabled} title="Toglie _L, _R, _dx"
          onClick={() => relabel(stripJoinSuffix)}>⌫ togli suffissi</button>
      </div>
      <div className="pgrid__rows" role="list">
        {visible.map((r) => {
          const index = rows.indexOf(r);
          const locked = disabled || r.missing || r.fixed;
          return (
            <div
              key={r.name}
              role="listitem"
              className={`pgrid__row${r.keep ? '' : ' pgrid__row--off'}${r.missing ? ' pgrid__row--missing' : ''}`}
              draggable={allowReorder && !disabled && !q}
              onDragStart={() => setDragName(r.name)}
              onDragOver={e => { if (dragName) e.preventDefault(); }}
              onDrop={() => { if (dragName) move(dragName, index); setDragName(null); }}
              onDragEnd={() => setDragName(null)}
            >
              {allowReorder && <span className="pgrid__grip" title="Trascina per riordinare" aria-hidden="true">⋮⋮</span>}
              <input
                type="checkbox"
                checked={r.keep}
                disabled={locked}
                onChange={e => setRow(r.name, { keep: e.target.checked })}
                aria-label={`Tieni ${r.display || r.name}`}
              />
              <span className="pgrid__src" title={`${r.display || r.name}${r.type ? ` · ${r.type}` : ''}${r.optional ? ' · facoltativa: può mancare in esecuzione' : ''}${r.missing ? ' · non più a monte' : ''}${r.fixed ? ' · colonna calcolata: si modifica nella tabella' : ''}`}>
                {r.display || r.name}
                {r.optional && <span className="ppanel__tag" title="Può mancare in esecuzione (dipende dalla risposta)"> facoltativa</span>}
              </span>
              <input
                className="ppanel__input pgrid__label"
                value={r.label || ''}
                placeholder={sourceName(r)}
                disabled={locked || !r.keep}
                onChange={e => setRow(r.name, { label: e.target.value })}
                aria-label={`Nome in uscita di ${r.display || r.name}`}
              />
              {r.missing
                ? <button type="button" className="ppanel__x" disabled={disabled} title="Non esiste più a monte: rimuovi" aria-label={`Rimuovi ${r.name}`}
                    onClick={() => change(rows.filter(x => x !== r))}>×</button>
                : allowReorder && (
                  <span className="pgrid__move">
                    <button type="button" className="ppanel__mini" disabled={disabled || index === 0 || Boolean(q)}
                      onClick={() => move(r.name, index - 1)} aria-label={`Sposta su ${r.name}`}>↑</button>
                    <button type="button" className="ppanel__mini" disabled={disabled || index === rows.length - 1 || Boolean(q)}
                      onClick={() => move(r.name, index + 1)} aria-label={`Sposta giù ${r.name}`}>↓</button>
                  </span>
                )}
            </div>
          );
        })}
        {visible.length === 0 && <div className="ppanel__note">Nessuna colonna{q ? ' trovata' : ' a monte: collega un passo in ingresso'}.</div>}
      </div>
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è risolto.</div>}
      {!error && savedError && (
        <div className="ppanel__error" role="alert">
          La configurazione salvata non è valida ({savedError}): l&apos;esecuzione si fermerà. Una modifica qui la riscrive valida.
        </div>
      )}
      {note && <div className="ppanel__hint">{note}</div>}
    </div>
  );
}
