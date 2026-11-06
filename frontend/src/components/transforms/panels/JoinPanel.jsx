/**
 * JoinPanel — Join senza suffissi _L/_R ovunque (Fase 4, M4; backend B2):
 * tipo, chiavi (anche piu' coppie), colonne da tenere per lato e cosa fare
 * dei nomi uguali. Il risultato mostrato e' calcolato con lo stesso piano
 * del backend (utils/columnPlans).
 *
 * Un join gia' salvato senza `name_conflict` resta com'e' (tutto _L/_R)
 * finche' l'utente non sceglie qui; da allora usa il nuovo percorso.
 * Ogni scrittura porta con se' le chiavi nel formato nuovo (left_keys, con
 * gli id): un join vecchio con left_on/right_on non perde le chiavi.
 */
import React, { useId, useMemo } from 'react';
import { joinColumnPlan, usesJoinColumnPlan } from '../../../utils/columnPlans';
import { readJoinType } from '../../../utils/joinTypes';
import useDraftList from './useDraftList';
import {
  JOIN_TYPES, NAME_CONFLICTS, joinKeysPatch, keptColumnsPatch, readJoinKeys, readKeptColumns, validateJoinKeys,
} from './panelConfig';
import './panels.css';

function SideColumns({ title, columns, kept, keys, otherNames, onToggle, onAll, disabled }) {
  const shown = columns.filter(c => keys.includes(c.name) || kept.includes(c.name)).length;
  return (
    <div className="ppanel__field">
      <div className="ppanel__row ppanel__row--between">
        <span className="ppanel__label">{title} · {shown} di {columns.length}</span>
        <button type="button" className="ppanel__mini" disabled={disabled} onClick={() => onAll(true)}>tutte</button>
        <button type="button" className="ppanel__mini" disabled={disabled} onClick={() => onAll(false)}>solo chiavi</button>
      </div>
      <div className="ppanel__checks">
        {columns.map(c => {
          const isKey = keys.includes(c.name);
          return (
            <label key={c.name} className="ppanel__check">
              <input type="checkbox" checked={isKey || kept.includes(c.name)} disabled={disabled || isKey}
                onChange={e => onToggle(c.name, e.target.checked)} />
              <span>{c.name}</span>
              {isKey && <span className="ppanel__tag">chiave</span>}
              {!isKey && otherNames.has(c.name) && <span className="ppanel__tag ppanel__tag--warn">nome uguale</span>}
            </label>
          );
        })}
        {columns.length === 0 && <div className="ppanel__note">Nessun ingresso collegato da questo lato.</div>}
      </div>
    </div>
  );
}

export default function JoinPanel({
  config, updateConfig, leftColumns = [], rightColumns = [], leftSourceName, rightSourceName, disabled,
}) {
  const uid = useId();
  const leftNames = useMemo(() => leftColumns.map(c => c.name), [leftColumns]);
  const rightNames = useMemo(() => rightColumns.map(c => c.name), [rightColumns]);
  const savedPairs = useMemo(() => readJoinKeys(config, leftColumns, rightColumns), [config, leftColumns, rightColumns]);
  const keysPatch = (pairs) => joinKeysPatch(pairs, leftColumns, rightColumns);
  const [pairs, setPairs, keyError] = useDraftList(savedPairs, {
    validate: validateJoinKeys,
    toSaved: (next) => next.filter(([l, r]) => l && r),
    write: (complete) => updateConfig(keysPatch(complete)),
  });
  const list = pairs.length ? pairs : [['', '']];
  const setPair = (i, side, value) => setPairs(list.map((p, j) => (j === i ? (side === 0 ? [value, p[1]] : [p[0], value]) : p)));

  const complete = savedPairs.filter(([l, r]) => l && r);
  const leftKeys = complete.map(([l]) => l);
  const rightKeys = complete.map(([, r]) => r);
  const leftSel = readKeptColumns(config, 'left', leftColumns);
  const rightSel = readKeptColumns(config, 'right', rightColumns);
  const leftKept = leftSel ?? leftNames;
  const rightKept = rightSel ?? rightNames;
  const legacy = !usesJoinColumnPlan(config);
  const conflict = config.name_conflict ?? 'suffix';

  // Ogni scelta qui: nuovo percorso (name_conflict esplicito) e chiavi nel
  // formato nuovo, anche se arrivavano da left_on/right_on.
  const write = (patch) => updateConfig({ ...keysPatch(complete), name_conflict: conflict, ...patch });
  const toggle = (side, name, on) => {
    const columns = side === 'left' ? leftColumns : rightColumns;
    const current = side === 'left' ? leftKept : rightKept;
    const next = on ? [...current, name] : current.filter(n => n !== name);
    write(keptColumnsPatch(side, columns, next));
  };
  const all = (side, on) => write(keptColumnsPatch(side, side === 'left' ? leftColumns : rightColumns,
    on ? (side === 'left' ? leftNames : rightNames) : []));

  const plan = joinColumnPlan(leftNames, rightNames, leftKeys, rightKeys, leftSel, rightSel, conflict);
  const leftSet = new Set(leftNames);
  const rightSet = new Set(rightNames);

  return (
    <div className="ppanel">
      <div className="ppanel__field">
        <label className="ppanel__label" htmlFor={`${uid}-how`}>Tipo</label>
        <select id={`${uid}-how`} className="ppanel__input" value={readJoinType('manipola', config.how)} disabled={disabled}
          onChange={e => updateConfig({ ...keysPatch(complete), how: e.target.value })}>
          {JOIN_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>

      <div className="ppanel__field">
        <span className="ppanel__label">Collega quando</span>
        {list.map(([l, r], i) => (
          // eslint-disable-next-line react/no-array-index-key
          <div className="ppanel__row" key={i}>
            <select className="ppanel__input" value={l} disabled={disabled} onChange={e => setPair(i, 0, e.target.value)} aria-label="Chiave sinistra">
              <option value="">— sinistra —</option>
              {leftNames.map(n => <option key={n} value={n}>{n}</option>)}
              {l && !leftSet.has(l) && <option value={l}>{l} (non trovata)</option>}
            </select>
            <span aria-hidden="true">=</span>
            <select className="ppanel__input" value={r} disabled={disabled} onChange={e => setPair(i, 1, e.target.value)} aria-label="Chiave destra">
              <option value="">— destra —</option>
              {rightNames.map(n => <option key={n} value={n}>{n}</option>)}
              {r && !rightSet.has(r) && <option value={r}>{r} (non trovata)</option>}
            </select>
            {list.length > 1 && (
              <button type="button" className="ppanel__x" disabled={disabled} aria-label="Rimuovi chiave"
                onClick={() => setPairs(list.filter((_, j) => j !== i))}>×</button>
            )}
          </div>
        ))}
        {keyError && <div className="ppanel__error" role="alert">{keyError}. Le chiavi non sono salvate finché non è completa.</div>}
        <button type="button" className="ppanel__link" disabled={disabled}
          onClick={() => setPairs([...list, ['', '']])}>＋ Aggiungi chiave</button>
        <div className="ppanel__hint">
          sinistra: <b>{leftSourceName || '—'}</b> · destra: <b>{rightSourceName || '—'}</b>
        </div>
      </div>

      <SideColumns title="Colonne da tenere · sinistra" columns={leftColumns} kept={leftKept} keys={leftKeys}
        otherNames={rightSet} disabled={disabled}
        onToggle={(n, on) => toggle('left', n, on)} onAll={on => all('left', on)} />
      <SideColumns title="Colonne da tenere · destra" columns={rightColumns} kept={rightKept} keys={rightKeys}
        otherNames={leftSet} disabled={disabled}
        onToggle={(n, on) => toggle('right', n, on)} onAll={on => all('right', on)} />

      <div className="ppanel__field">
        <span className="ppanel__label">Colonne con lo stesso nome</span>
        <div className="ppanel__radios">
          {NAME_CONFLICTS.map(([v, l]) => (
            <label key={v} className="ppanel__check">
              <input type="radio" name={`${uid}-conflict`} value={v} checked={!legacy && conflict === v} disabled={disabled}
                onChange={() => updateConfig({ ...keysPatch(complete), name_conflict: v })} />
              <span>{l}</span>
            </label>
          ))}
        </div>
      </div>

      {legacy && (
        <div className="ppanel__note">
          Questo Join usa il comportamento di prima: tutte le colonne non chiave prendono _L o _R.
          Scegli qui sopra cosa fare dei nomi uguali (o le colonne da tenere) per passare al nuovo.
        </div>
      )}
      {legacy ? null : plan.error
        ? <div className="ppanel__error" role="alert">{plan.error}</div>
        : leftKeys.length > 0 && (
          <div className="ppanel__ok">
            Risultato: <b>{plan.outputs.length} colonne</b>
            {plan.outputs.some(o => /_(L|R)$/.test(o)) ? ', con suffissi _L/_R' : ', nessun suffisso _L/_R'}.
          </div>
        )}
    </div>
  );
}
