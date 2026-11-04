/**
 * StepPreview — anteprima dei dati DOPO il passo selezionato (Fase 4, M3).
 *
 * Usa GET /projects/{id}/preview?node_id= (backend B1/B6): esegue solo il
 * nodo e i suoi antenati. Non parte da sola (esegue query e chiamate API):
 * l'utente preme "Anteprima". Il backend legge il canvas SALVATO: con
 * modifiche in sospeso il pulsante lo dice ("Salva e mostra anteprima").
 * Se cio' che il backend legge cambia dopo il calcolo (canvasContentKey:
 * dati e collegamenti, non le posizioni), i dati mostrati sono segnalati
 * come non aggiornati.
 *
 * Occupa il fondo del canvas: dichiara la sua altezza in
 * --step-preview-h sul contenitore, cosi' controlli, minimappa e pannelli
 * in basso (Query Preview) salgono invece di finirci sotto.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { hasUnsavedChanges } from '../../store/saveState';
import { saveBeforeServerRead } from '../../utils/serverActions';
import { useInlinePreview } from '../../hooks/useInlinePreview';
import { humanizeError } from '../../utils/errorMessages';
import { PREVIEW_ROW_LIMIT, formatPreviewValue } from '../../utils/previewFormat';
import { canvasContentKey } from '../../utils/canvasContentKey';
import { nodeTitle } from './nodeDescriptors';
import './StepPreview.css';

const HEIGHT_OPEN = 240;
const HEIGHT_COLLAPSED = 38;
const STALE_CHECK_MS = 200;
const currentKey = () => {
  const { nodes, edges } = useCanvasStore.getState();
  return canvasContentKey(nodes, edges);
};

const selectSelectedId = (s) => {
  let found = null;
  for (const n of s.nodes || []) {
    if (!n.selected || n.type === 'comment') continue;
    if (found) return null;
    found = n.id;
  }
  return found;
};

export default function StepPreview({ projectId }) {
  const nodeId = useCanvasStore(selectSelectedId);
  const node = useCanvasStore(s => (nodeId ? (s.nodes || []).find(n => n.id === nodeId) : null));
  const unsaved = useCanvasStore(hasUnsavedChanges);
  const { status, data, error, run, clear } = useInlinePreview(projectId);
  const [shownFor, setShownFor] = useState(null);
  const [shownKey, setShownKey] = useState(null);
  const [stale, setStale] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const ref = useRef(null);
  const visible = Boolean(node && projectId);

  // Cambiando passo l'anteprima precedente non vale piu'.
  useEffect(() => {
    if (shownFor && shownFor !== nodeId) { clear(); setShownFor(null); setShownKey(null); setSaveError(null); }
  }, [nodeId, shownFor, clear]);

  // Dopo un calcolo: confronta l'impronta del canvas a ogni modifica
  // (con un piccolo ritardo, per non serializzare a ogni frame di drag).
  useEffect(() => {
    setStale(false);
    if (!shownKey) return undefined;
    let timer = null;
    const unsubscribe = useCanvasStore.subscribe((s, prev) => {
      if (s.nodes === prev.nodes && s.edges === prev.edges) return;
      clearTimeout(timer);
      timer = setTimeout(() => setStale(currentKey() !== shownKey), STALE_CHECK_MS);
    });
    return () => { clearTimeout(timer); unsubscribe(); };
  }, [shownKey]);

  useLayoutEffect(() => {
    const host = ref.current?.parentElement;
    if (!visible || !host) return undefined;
    host.style.setProperty('--step-preview-h', `${collapsed ? HEIGHT_COLLAPSED : HEIGHT_OPEN}px`);
    return () => host.style.removeProperty('--step-preview-h');
  }, [visible, collapsed]);

  if (!visible) return null;

  const start = async () => {
    setSaveError(null);
    try {
      // Il pulsante dice gia' "Salva e …": niente seconda domanda.
      await saveBeforeServerRead("L'anteprima del passo", { explicit: true });
    } catch (err) {
      setSaveError(humanizeError(err).message || 'Salvataggio non riuscito');
      return;
    }
    setShownFor(node.id);
    setShownKey(currentKey());
    setCollapsed(false);
    run(PREVIEW_ROW_LIMIT, node.id);
  };
  const showStale = stale && shownFor === node.id && (status === 'success' || status === 'empty');

  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const title = nodeTitle(node.type, node.data);

  return (
    <section ref={ref} className={`step-preview${collapsed ? ' step-preview--collapsed' : ''}`} aria-label="Anteprima dati del passo">
      <header className="step-preview__head">
        <button type="button" className="step-preview__toggle" onClick={() => setCollapsed(c => !c)}
          aria-label={collapsed ? 'Espandi anteprima' : 'Comprimi anteprima'}>{collapsed ? '▸' : '▾'}</button>
        <strong className="step-preview__title">Dati dopo «{title}»</strong>
        {status === 'success' && data && (
          <span className="step-preview__meta">
            {columns.length} colonne · {Number(data.total_count ?? rows.length).toLocaleString('it-IT')} righe
            {data.truncated || data.approximate ? ' (campione)' : ''}
          </span>
        )}
        {showStale && <span className="step-preview__stale">Il canvas è cambiato: dati non aggiornati</span>}
        <span className="step-preview__spacer" />
        <button
          type="button"
          className="step-preview__run"
          onClick={start}
          disabled={status === 'loading'}
          title={unsaved ? "L'anteprima legge il canvas salvato: le modifiche vengono salvate prima" : undefined}
        >
          {status === 'loading'
            ? 'Caricamento…'
            : `${unsaved ? 'Salva e ' : ''}${shownFor === node.id ? (unsaved ? 'aggiorna' : '↻ Aggiorna') : (unsaved ? 'mostra anteprima' : '▶ Anteprima')}`}
        </button>
      </header>
      {!collapsed && (
        <div className="step-preview__body">
          {saveError && <div className="step-preview__msg step-preview__msg--error">{saveError}</div>}
          {!saveError && status === 'idle' && (
            <div className="step-preview__msg">Premi «Anteprima» per vedere i dati in questo punto della pipeline.</div>
          )}
          {status === 'loading' && <div className="step-preview__msg">Calcolo dell&apos;anteprima…</div>}
          {status === 'error' && <div className="step-preview__msg step-preview__msg--error">{error}</div>}
          {status === 'empty' && <div className="step-preview__msg">Nessuna riga in questo punto.</div>}
          {status === 'success' && (
            <table className="step-preview__table">
              <thead>
                <tr>{columns.map(c => (
                  <th key={c.name}>{c.name}<span className="step-preview__type">{c.type}</span></th>
                ))}</tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  // eslint-disable-next-line react/no-array-index-key
                  <tr key={i}>{columns.map(c => <td key={c.name}>{formatPreviewValue(row[c.name])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
