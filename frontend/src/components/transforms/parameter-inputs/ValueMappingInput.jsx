/**
 * ValueMappingInput — "Mappa valori" (Lookup): righe valore → nuovo valore,
 * scritte a mano (non sono nomi di colonna). Legge la mappatura in ogni
 * formato salvato (dizionario, {from,to}, {old_name,new_name}) e scrive
 * sempre [{from, to}] (backend shared/execution/lookup_mapping.py).
 * Le righe senza valore di partenza restano qui finche' non sono complete.
 *
 * «Riempi con i valori presenti» (Blocco A, F3; raccolta c): chiede al server
 * i valori distinti della colonna scelta nell'INGRESSO del passo
 * (utils/lookupFill), sul canvas dell'editor anche non salvato, e aggiunge
 * le righe mancanti senza toccare quelle esistenti. Le righe aggiunte sono
 * PROPOSTE neutre («diventa» vuoto, "da tradurre"): entrano nella mappa
 * salvata solo quando l'utente scrive «diventa» (utils/lookupFill: regola
 * del valore utilizzabile, toSavedRows). Il merge si applica alle righe
 * correnti al ritorno della lettura (useDraftList, update funzionale).
 * Le proposte sono legate alla colonna letta: se la colonna del Lookup
 * cambia, quelle non compilate si tolgono e una lettura arrivata dopo il
 * cambio non aggiunge niente (utils/lookupFill, dropOrphanProposals).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import useDraftList from '../panels/useDraftList';
import useCanvasStore from '../../../store/useCanvasStore';
import { readValueMapping } from '../../../utils/lookupMapping';
import {
  describeFill, dropOrphanProposals, fetchPresentValues, fillSourceKey, isEmptyRow, isUsableValue,
  mergePresentValues, toSavedRows, FILL_LIMIT,
} from '../../../utils/lookupFill';
import { term } from '../../../utils/glossary';
import { requestErrorMessage } from '../../../utils/errorMessages';
import '../panels/panels.css';

export default function ValueMappingInput({ value, onChange, disabled = false, nodeId = null, column = null, columnId = null }) {
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const off = Boolean(disabled || isReadOnly);
  const saved = useMemo(() => readValueMapping(value), [value]);
  const [rows, setRows, error] = useDraftList(saved, {
    validate: (next) => {
      const live = next.filter(r => !isEmptyRow(r));
      if (live.some(r => !isUsableValue(r.from))) return 'Ogni riga ha bisogno del valore di partenza';
      const dup = live.map(r => r.from).find((f, i, all) => all.indexOf(f) !== i);
      return dup !== undefined ? `Il valore "${dup}" compare due volte` : null;
    },
    toSaved: toSavedRows,
    write: onChange,
  });
  const list = rows.length ? rows : [{ from: '', to: '' }];
  const set = (i, patch) => setRows(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  // «Riempi con i valori presenti»: { loading, info, error }.
  const [fill, setFill] = useState({ loading: false, info: null, error: null });
  const canFill = !off && Boolean(nodeId) && Boolean(column || columnId);
  const fillTerm = term('fill_values');

  // Colonna da cui vengono le proposte: se cambia, quelle non compilate si tolgono.
  const source = fillSourceKey({ column, columnId });
  const sourceRef = useRef(source);
  useEffect(() => {
    if (sourceRef.current === source) return;
    sourceRef.current = source;
    setRows(current => dropOrphanProposals(current, source));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const handleFill = async () => {
    if (!canFill || fill.loading) return;
    setFill({ loading: true, info: null, error: null });
    const requested = source;
    try {
      const res = await fetchPresentValues({ nodeId, column, columnId });
      if (sourceRef.current !== requested) {
        setFill({ loading: false, info: 'La colonna è cambiata durante la lettura: nessun valore aggiunto. Usa di nuovo «Riempi».', error: null });
        return;
      }
      // Merge sulle righe CORRENTI (non su quelle lette prima della richiesta).
      let merged = { added: [], skipped: undefined };
      setRows((current) => {
        merged = mergePresentValues(current, res.values, requested);
        return merged.added.length ? merged.rows : current;
      });
      setFill({ loading: false, info: describeFill(res, merged.added.length, merged.skipped), error: null });
    } catch (e) {
      setFill({ loading: false, info: null, error: requestErrorMessage(e) });
    }
  };

  return (
    <div className="ppanel">
      {list.map((r, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div className="ppanel__row" key={i}>
          <input className="ppanel__input" value={r.from} disabled={off} placeholder="valore"
            onChange={e => set(i, { from: e.target.value })} aria-label="Valore di partenza" />
          <span aria-hidden="true">→</span>
          <input className="ppanel__input" value={r.to} disabled={off} placeholder={r.proposed ? 'da tradurre' : 'diventa'}
            onChange={e => set(i, { to: e.target.value })} aria-label="Nuovo valore" />
          <button type="button" className="ppanel__x" disabled={off} aria-label="Rimuovi riga"
            onClick={() => setRows(list.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {error && <div className="ppanel__error" role="alert">{error}. Le modifiche non sono salvate finché non è risolto.</div>}
      <button type="button" className="ppanel__link" disabled={off}
        onClick={() => setRows([...list, { from: '', to: '' }])}>＋ Aggiungi riga</button>
      <button type="button" className="ppanel__link" disabled={!canFill || fill.loading}
        title={canFill ? `${fillTerm.description} (al massimo ${FILL_LIMIT})` : (off ? 'Sola lettura' : 'Scegli prima la colonna da mappare')}
        onClick={handleFill}>
        {fillTerm.icon} {fill.loading ? 'Lettura dei valori…' : fillTerm.name}
      </button>
      {fill.info && <div className="ppanel__hint" role="status">{fill.info}</div>}
      {fill.error && <div className="ppanel__error" role="alert">Valori non letti: {fill.error}</div>}
    </div>
  );
}
