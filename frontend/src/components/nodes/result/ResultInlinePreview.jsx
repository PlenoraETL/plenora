/**
 * ResultInlinePreview.jsx — anteprima compatta del risultato, mostrata
 * INLINE dentro al ResultNode dopo un'esecuzione riuscita (#5).
 *
 * Niente modale: legge lo stato di `useInlinePreview` (gestito dal parent
 * ResultNode) e renderizza una mini-tabella con le prime righe/colonne.
 * "Espandi" riapre la `PreviewModal` piena (callback `onExpand` dal parent).
 *
 * Le classi `nodrag`/`nowheel` impediscono a React Flow di trascinare il nodo
 * o zoomare il canvas mentre si interagisce con la tabella.
 */
import React from 'react';
import { isGeometryType } from '../../../utils/columnUtils';
import { formatNumber } from '../../../utils/previewFormat';

const MAX_ROWS = 5;
const MAX_COLS = 6;
const CELL_MAX = 26;

function formatCell(value, type) {
  if (value == null) return '∅';
  if (isGeometryType(type)) return '⬡ geom';
  if (typeof value === 'object') {
    const j = JSON.stringify(value);
    return j.length > CELL_MAX ? `${j.slice(0, CELL_MAX - 1)}…` : j;
  }
  if (type === 'bool') return value ? '✔' : '✗';
  const s = formatNumber(value);
  return s.length > CELL_MAX ? `${s.slice(0, CELL_MAX - 1)}…` : s;
}

export default function ResultInlinePreview({ state, onExpand, onRefresh }) {
  const { status, data, error } = state;
  if (status === 'idle') return null;

  const rows = data?.rows || [];
  const allCols = data?.columns || [];
  const cols = allCols.slice(0, MAX_COLS);
  const hiddenCols = allCols.length - cols.length;
  const shownRows = rows.slice(0, MAX_ROWS);

  // Riga conteggio (truncated → preview/total, altrimenti righe caricate).
  const countLabel = (() => {
    if (status !== 'success') return null;
    const total = data?.total_count;
    const preview = data?.preview_count ?? rows.length;
    return data?.truncated && total
      ? `${preview.toLocaleString()} / ${total.toLocaleString()} righe`
      : `${rows.length.toLocaleString()} righe`;
  })();

  return (
    <div className="result-node__preview-inline nodrag nowheel">
      <div className="result-node__preview-inline-head">
        <span className="result-node__preview-inline-title">👁️ Anteprima</span>
        {countLabel && <span className="result-node__preview-inline-count">{countLabel}</span>}
        <div className="result-node__preview-inline-actions">
          {onRefresh && (
            <button
              type="button"
              className="result-node__preview-inline-btn"
              onClick={onRefresh}
              disabled={status === 'loading'}
              title="Ricarica anteprima"
            >
              ↻
            </button>
          )}
          {onExpand && (status === 'success' || status === 'empty') && (
            <button
              type="button"
              className="result-node__preview-inline-btn result-node__preview-inline-btn--expand"
              onClick={onExpand}
              title="Apri anteprima completa"
            >
              ⤢ Espandi
            </button>
          )}
        </div>
      </div>

      {status === 'loading' && (
        <div className="result-node__preview-inline-msg">Caricamento anteprima…</div>
      )}

      {status === 'error' && (
        <div className="result-node__preview-inline-msg result-node__preview-inline-msg--err">
          ⚠️ {error || 'Errore anteprima'}
        </div>
      )}

      {status === 'empty' && (
        <div className="result-node__preview-inline-msg">Nessun dato da mostrare</div>
      )}

      {status === 'success' && (
        <div className="result-node__preview-inline-scroll">
          <table className="result-node__preview-inline-table">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c.name} title={`${c.name} (${c.type || 'str'})`}>{c.name}</th>
                ))}
                {hiddenCols > 0 && <th className="result-node__preview-inline-more">+{hiddenCols}</th>}
              </tr>
            </thead>
            <tbody>
              {shownRows.map((row, ri) => (
                <tr key={ri}>
                  {cols.map((c) => {
                    const v = row[c.name];
                    return (
                      <td key={c.name} className={v == null ? 'result-node__preview-inline-null' : undefined}>
                        {formatCell(v, c.type)}
                      </td>
                    );
                  })}
                  {hiddenCols > 0 && <td className="result-node__preview-inline-more">…</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > MAX_ROWS && (
            <div className="result-node__preview-inline-foot">
              +{(rows.length - MAX_ROWS).toLocaleString()} righe — usa “Espandi”
            </div>
          )}
        </div>
      )}
    </div>
  );
}
