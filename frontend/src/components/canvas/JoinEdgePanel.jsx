/**
 * JoinEdgePanel — il join di Estrai nel pannello laterale (Blocco A,
 * raccolta b). Si apre selezionando l'arco; intestazione, chiusura ed
 * "Elimina join" sono del guscio (NodeInspector), qui c'e' solo il corpo.
 *
 * Regole:
 *  - la bozza e' una sola (useDraftList): si scrive sull'arco SOLO una
 *    configurazione valida, e solo se diversa da quella salvata (aprire,
 *    guardare o rimettere lo stesso valore non rende "non salvato");
 *  - si legge sempre l'arco corrente dello store: dopo annulla/ripeti la
 *    bozza riparte da li', nessuna istantanea;
 *  - ogni campo che nomina una colonna passa da ColumnSelect con le scelte
 *    di utils/estraiJoins.joinFieldOptions: una colonna salvata che non c'e'
 *    piu' compare come "(non trovata)" e blocca la scrittura;
 *  - nessuna colonna scelta al posto dell'utente; il suggerimento della
 *    relazione ArcGIS si applica solo con "Usa";
 *  - in sola lettura tutto e' disabilitato.
 */
import React, { useMemo } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import useDraftList from '../transforms/panels/useDraftList';
import { getColumnTypeIcon } from '../../utils/columnUtils';
import {
  JOIN_COLUMN_FIELDS, JOIN_FUNCTIONS, JOIN_TYPES, ON_FILTER_OPERATORS, SPATIAL_PREDICATES,
  emptyCondition, emptyOnFilter, isColumnMissing, joinDataFromDraft, joinDraftFromData,
  joinEdgeLook, joinFieldOptions, joinOnText, joinProblems, joinTableName,
} from '../../utils/estraiJoins';
import '../transforms/panels/panels.css';

const FIELD = Object.fromEntries(JOIN_COLUMN_FIELDS.map(f => [f.id, f]));
const fieldLabel = (field, index) => (index === null || index === undefined ? field.label : `${field.label} ${index + 1}`);

/** Scelta di una colonna: le colonne della tabella, piu' il valore salvato se non c'e'. */
function ColumnSelect({ field, index = null, value, sides, disabled, onChange }) {
  const opts = joinFieldOptions(sides[field.side], field.kind);
  const missing = isColumnMissing(value, opts);
  const extra = value && !opts.options.some(o => o.name === value);
  return (
    <select
      className="ppanel__input"
      aria-label={fieldLabel(field, index)}
      aria-invalid={missing || undefined}
      value={value}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
    >
      <option value="">— scegli —</option>
      {opts.options.map(o => (
        <option key={o.name} value={o.name}>
          {field.kind === 'geom' ? '🌐' : getColumnTypeIcon(o.type)} {o.name}{o.isCalculated ? ' (calc)' : ''}
        </option>
      ))}
      {extra && <option value={value}>{value}{missing ? ' (non trovata)' : ''}</option>}
    </select>
  );
}

function FuncSelect({ label, value, disabled, onChange }) {
  return (
    <select className="ppanel__input ppanel__input--func" aria-label={label} value={value || ''} disabled={disabled}
      onChange={e => onChange(e.target.value)} style={{ flex: '0 0 92px' }}>
      {JOIN_FUNCTIONS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
    </select>
  );
}

export default function JoinEdgePanel({ edge, sourceNode, targetNode, disabled = false }) {
  const updateEdge = useCanvasStore(s => s.updateEdge);
  const sides = { source: sourceNode, target: targetNode };
  const saved = useMemo(() => joinDraftFromData(edge.data), [edge.data]);
  const [draft, setDraft] = useDraftList(saved, {
    validate: (next) => joinProblems(next, sourceNode, targetNode)[0]?.message || null,
    toSaved: joinDataFromDraft,
    write: (out) => updateEdge(edge.id, e => ({ ...joinEdgeLook(out, 'ok'), data: { ...e.data, ...out } })),
  });
  const problems = joinProblems(draft, sourceNode, targetNode);
  const sourceName = joinTableName(sourceNode, 'sorgente');
  const targetName = joinTableName(targetNode, 'target');
  const geomAvailable = joinFieldOptions(sourceNode, 'geom').options.length > 0
    && joinFieldOptions(targetNode, 'geom').options.length > 0;

  const set = (patch) => setDraft({ ...draft, ...patch });
  const setRow = (list, index, patch) => set({ [list]: draft[list].map((r, i) => (i === index ? { ...r, ...patch } : r)) });
  const suggestion = edge.data?.suggestion;
  const on = joinOnText(draft, sourceName, targetName);

  return (
    <div className="ppanel join-edge-panel">
      <div className="ppanel__note">
        <strong>{sourceName}</strong> ⟶ <strong>{targetName}</strong>
      </div>

      {suggestion && problems.some(p => p.code === 'incomplete') && draft.mode === 'standard' && (
        <div className="ppanel__suggest">
          <span>Dalla {suggestion.label}: {sourceName}.{suggestion.source_column} = {targetName}.{suggestion.target_column}</span>
          <button type="button" className="ppanel__btn ppanel__btn--primary" disabled={disabled}
            onClick={() => set({ conditions: [{ ...emptyCondition(), source_column: suggestion.source_column, target_column: suggestion.target_column }] })}>
            Usa
          </button>
        </div>
      )}

      <div className="ppanel__field">
        <span className="ppanel__label">Tipo di collegamento</span>
        <div className="ppanel__row" role="group" aria-label="Tipo di collegamento">
          <button type="button" className={`ppanel__btn${draft.mode === 'standard' ? ' ppanel__btn--primary' : ''}`}
            aria-pressed={draft.mode === 'standard'} disabled={disabled} onClick={() => set({ mode: 'standard' })}>
            🔗 Standard
          </button>
          <button type="button" className={`ppanel__btn${draft.mode === 'spatial' ? ' ppanel__btn--primary' : ''}`}
            aria-pressed={draft.mode === 'spatial'} disabled={disabled || (!geomAvailable && draft.mode !== 'spatial')}
            title={geomAvailable ? '' : 'Nessuna colonna geometrica in una delle due tabelle'}
            onClick={() => set({ mode: 'spatial' })}>
            🌐 Spaziale
          </button>
        </div>
      </div>

      <div className="ppanel__field">
        <label className="ppanel__label" htmlFor={`${edge.id}-type`}>Tipo join</label>
        <select id={`${edge.id}-type`} className="ppanel__input" value={draft.join_type} disabled={disabled}
          onChange={e => set({ join_type: e.target.value })}>
          {JOIN_TYPES.map(t => <option key={t.value} value={t.value}>{t.label} — {t.description}</option>)}
        </select>
      </div>

      {draft.mode === 'standard' && (
        <>
          <div className="ppanel__field">
            <span className="ppanel__label">Colonne di join ({sourceName} = {targetName})</span>
            {draft.conditions.map((c, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <div key={i} className="ppanel__card">
                {i > 0 && <span className="ppanel__hint">AND</span>}
                <div className="ppanel__row">
                  <FuncSelect label={`Funzione sorgente ${i + 1}`} value={c.source_func} disabled={disabled}
                    onChange={v => setRow('conditions', i, { source_func: v })} />
                  <ColumnSelect field={FIELD['condition.source_column']} index={i} value={c.source_column} sides={sides}
                    disabled={disabled} onChange={v => setRow('conditions', i, { source_column: v })} />
                </div>
                <div className="ppanel__row">
                  <span aria-hidden="true">=</span>
                  <FuncSelect label={`Funzione target ${i + 1}`} value={c.target_func} disabled={disabled}
                    onChange={v => setRow('conditions', i, { target_func: v })} />
                  <ColumnSelect field={FIELD['condition.target_column']} index={i} value={c.target_column} sides={sides}
                    disabled={disabled} onChange={v => setRow('conditions', i, { target_column: v })} />
                  {draft.conditions.length > 1 && (
                    <button type="button" className="ppanel__x" aria-label={`Rimuovi condizione ${i + 1}`} disabled={disabled}
                      onClick={() => set({ conditions: draft.conditions.filter((_, j) => j !== i) })}>×</button>
                  )}
                </div>
              </div>
            ))}
            <button type="button" className="ppanel__link" disabled={disabled}
              onClick={() => set({ conditions: [...draft.conditions, emptyCondition()] })}>
              + Aggiungi condizione (AND)
            </button>
          </div>

          <div className="ppanel__field">
            <span className="ppanel__label">Condizioni aggiuntive ON</span>
            <span className="ppanel__hint">Filtri su {targetName} nella clausola ON (diverso da WHERE con LEFT JOIN).</span>
            {draft.on_filters.map((f, i) => {
              const op = ON_FILTER_OPERATORS.find(o => o.value === f.operator);
              return (
                // eslint-disable-next-line react/no-array-index-key
                <div key={i} className="ppanel__card">
                  <div className="ppanel__row">
                    <FuncSelect label={`Funzione filtro ${i + 1}`} value={f.func} disabled={disabled}
                      onChange={v => setRow('on_filters', i, { func: v })} />
                    <ColumnSelect field={FIELD['on_filter.column']} index={i} value={f.column} sides={sides}
                      disabled={disabled} onChange={v => setRow('on_filters', i, { column: v })} />
                    <button type="button" className="ppanel__x" aria-label={`Rimuovi filtro ${i + 1}`} disabled={disabled}
                      onClick={() => set({ on_filters: draft.on_filters.filter((_, j) => j !== i) })}>×</button>
                  </div>
                  <div className="ppanel__row">
                    <select className="ppanel__input ppanel__input--op" aria-label={`Operatore filtro ${i + 1}`}
                      value={f.operator} disabled={disabled} onChange={e => setRow('on_filters', i, { operator: e.target.value })}>
                      {ON_FILTER_OPERATORS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    {(op?.needsValue ?? true) && (
                      <input className="ppanel__input" aria-label={`Valore filtro ${i + 1}`} placeholder="Valore…"
                        value={f.value ?? ''} disabled={disabled} onChange={e => setRow('on_filters', i, { value: e.target.value })} />
                    )}
                  </div>
                </div>
              );
            })}
            <button type="button" className="ppanel__link" disabled={disabled}
              onClick={() => set({ on_filters: [...draft.on_filters, emptyOnFilter()] })}>
              + Aggiungi filtro ON
            </button>
          </div>
        </>
      )}

      {draft.mode === 'spatial' && (
        <>
          <div className="ppanel__field">
            <label className="ppanel__label" htmlFor={`${edge.id}-pred`}>Predicato spaziale</label>
            <select id={`${edge.id}-pred`} className="ppanel__input" value={draft.spatial_predicate} disabled={disabled}
              onChange={e => set({ spatial_predicate: e.target.value })}>
              {SPATIAL_PREDICATES.map(p => <option key={p.value} value={p.value}>{p.label} — {p.description}</option>)}
            </select>
          </div>
          {draft.spatial_predicate === 'ST_DWithin' && (
            <div className="ppanel__field">
              <label className="ppanel__label" htmlFor={`${edge.id}-dist`}>Distanza (unità del sistema di riferimento)</label>
              <input id={`${edge.id}-dist`} type="number" min="0" step="1" className="ppanel__input" disabled={disabled}
                value={draft.spatial_distance ?? ''}
                onChange={e => set({ spatial_distance: e.target.value === '' ? '' : Number(e.target.value) })} />
            </div>
          )}
          <div className="ppanel__field">
            <span className="ppanel__label">Colonne geometriche</span>
            <div className="ppanel__row">
              <ColumnSelect field={FIELD.source_geom_column} value={draft.source_geom_column} sides={sides}
                disabled={disabled} onChange={v => set({ source_geom_column: v })} />
              <span aria-hidden="true">⟷</span>
              <ColumnSelect field={FIELD.target_geom_column} value={draft.target_geom_column} sides={sides}
                disabled={disabled} onChange={v => set({ target_geom_column: v })} />
            </div>
          </div>
        </>
      )}

      {problems.length > 0 && (
        <div className={problems[0].code === 'incomplete' ? 'ppanel__note branch-warn' : 'ppanel__error'} role="status">
          {problems[0].code === 'incomplete' ? 'Da completare: ' : ''}{problems[0].message}
        </div>
      )}

      <div className="ppanel__field">
        <span className="ppanel__label">Anteprima SQL</span>
        <code className="ppanel__mono">
          {sourceName} {draft.join_type.toUpperCase()} JOIN {targetName}{on ? ` ON ${on}` : ' ON …'}
        </code>
      </div>
      {!disabled && <div className="ppanel__hint">Le modifiche si applicano appena la configurazione è completa.</div>}
    </div>
  );
}
