/**
 * StepPreview — anteprima dei dati DOPO il passo selezionato (Fase 4, M3).
 *
 * Usa GET /projects/{id}/preview?node_id= (backend B1/B6): esegue solo il
 * nodo e i suoi antenati. Non parte da sola (esegue query e chiamate API):
 * l'utente preme "Anteprima". Prima salva in modo silenzioso, perche' il
 * backend legge il canvas salvato.
 */
import React, { useEffect, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { useInlinePreview } from '../../hooks/useInlinePreview';
import { humanizeError } from '../../utils/errorMessages';
import { nodeTitle } from './nodeDescriptors';
import './StepPreview.css';

const LIMIT = 50;

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
  const handleSaveQuiet = useCanvasStore(s => s.handleSaveQuiet);
  const { status, data, error, run, clear } = useInlinePreview(projectId);
  const [shownFor, setShownFor] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [collapsed, setCollapsed] = useState(false);

  // Cambiando passo l'anteprima precedente non vale piu'.
  useEffect(() => {
    if (shownFor && shownFor !== nodeId) { clear(); setShownFor(null); setSaveError(null); }
  }, [nodeId, shownFor, clear]);

  if (!node || !projectId) return null;

  const start = async () => {
    setSaveError(null);
    try {
      await handleSaveQuiet();
    } catch (err) {
      setSaveError(humanizeError(err).message || 'Salvataggio non riuscito');
      return;
    }
    setShownFor(node.id);
    setCollapsed(false);
    run(LIMIT, node.id);
  };

  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const title = nodeTitle(node.type, node.data);

  return (
    <section className={`step-preview${collapsed ? ' step-preview--collapsed' : ''}`} aria-label="Anteprima dati del passo">
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
        <span className="step-preview__spacer" />
        <button type="button" className="step-preview__run" onClick={start} disabled={status === 'loading'}>
          {status === 'loading' ? 'Caricamento…' : (shownFor === node.id ? '↻ Aggiorna' : '▶ Anteprima')}
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
                  <tr key={i}>{columns.map(c => <td key={c.name}>{formatCell(row[c.name])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}

function formatCell(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
