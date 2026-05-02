/**
 * RunsTab.jsx — Tabella esecuzioni pipeline con filtri e dettaglio
 *
 * Estratto da LogViewer.jsx.
 *
 * Props:
 *   refreshTick {number} - Incrementato dall'esterno per forzare un reload
 */

import React, { useState, useEffect, useCallback } from 'react';
import { getLogRuns } from '../../api/logRuns';
import { fmtDuration, fmtRows, formatTime, truncateProjectId } from '../../utils/logViewerHelpers.jsx';
import { useToast } from '../../hooks/useToast';
import RunDetail from './RunDetail';

const MODULE_COLORS = {
  estrai: '#34C17A', manipola: '#9B6DFF', arricchisci: '#F5A623', carica: '#4B8BFF',
  orchestrator: '#60A5FA', scheduler: '#34D399', connections: '#FBBF24', projects: '#818CF8',
  files: '#A78BFA', services: '#94A3B8', db: '#F87171', utils: '#6B7280', workers: '#F472B6',
};

const STATUS_CONFIG = {
  success: { label: 'Successo', cls: 'lv__run-status--ok',      icon: '✓' },
  failure: { label: 'Fallita',  cls: 'lv__run-status--err',     icon: '✕' },
  running: { label: 'In corso', cls: 'lv__run-status--running', icon: '◌' },
};

const PAGE = 30;

export default function RunsTab({ refreshTick = 0 }) {
  const toast = useToast();
  const [runs,         setRuns]         = useState([]);
  const [total,        setTotal]        = useState(0);
  const [hasMore,      setHasMore]      = useState(false);
  const [offset,       setOffset]       = useState(0);
  const [loading,      setLoading]      = useState(true);
  const [loadError,    setLoadError]    = useState(null);
  const [expandedRun,  setExpandedRun]  = useState(null);
  const [filterStatus, setFilterStatus] = useState('');
  const [filterModule, setFilterModule] = useState('');

  const load = useCallback(async (newOffset = 0) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await getLogRuns({
        status: filterStatus || undefined,
        module: filterModule || undefined,
        limit:  PAGE,
        offset: newOffset,
      });
      setRuns(data.runs || []);
      setTotal(data.total || 0);
      setHasMore(data.has_more || false);
      setOffset(newOffset);
    } catch (err) {
      // Prima: catch{} silenzioso → l'utente vedeva tabella vuota senza spiegazione.
      // Ora: log + toast + stato di errore visibile inline.
      console.error('[RunsTab] errore caricamento runs:', err);
      const msg = err?.message || 'Errore sconosciuto';
      setLoadError(msg);
      toast.error(`Errore caricamento esecuzioni: ${msg}`);
      setRuns([]);
      setTotal(0);
      setHasMore(false);
    }
    finally { setLoading(false); }
  }, [filterStatus, filterModule, toast]);

  useEffect(() => { load(0); }, [filterStatus, filterModule, refreshTick]);

  const countOk  = runs.filter(r => r.status === 'success').length;
  const countErr = runs.filter(r => r.status === 'failure').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, position: 'relative', zIndex: 1 }}>

      {/* Mini stats */}
      <div className="lv__stats" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>
        <div className="lv__stat lv__stat--total">
          <div className="lv__stat-label">Run Totali</div>
          <div className="lv__stat-val">{total}</div>
          <div className="lv__stat-sub">questa pagina</div>
        </div>
        <button
          type="button"
          className={`lv__stat lv__stat--info${filterStatus === 'success' ? ' lv__stat--active' : ''}`}
          style={{ cursor: 'pointer', textAlign: 'inherit', font: 'inherit', color: 'inherit', appearance: 'none', WebkitAppearance: 'none' }}
          onClick={() => setFilterStatus(filterStatus === 'success' ? '' : 'success')}
          aria-pressed={filterStatus === 'success'}
          aria-label="Filtra esecuzioni completate">
          <span className="lv__stat-label">Completate</span>
          <span className="lv__stat-val" style={{ color: 'var(--lv-ok)' }}>{countOk}</span>
          <span className="lv__stat-sub">clicca per filtrare</span>
        </button>
        <button
          type="button"
          className={`lv__stat lv__stat--error${filterStatus === 'failure' ? ' lv__stat--active' : ''}`}
          style={{ cursor: 'pointer', textAlign: 'inherit', font: 'inherit', color: 'inherit', appearance: 'none', WebkitAppearance: 'none' }}
          onClick={() => setFilterStatus(filterStatus === 'failure' ? '' : 'failure')}
          aria-pressed={filterStatus === 'failure'}
          aria-label="Filtra esecuzioni fallite">
          <span className="lv__stat-label">Fallite</span>
          <span className="lv__stat-val" style={{ color: 'var(--lv-err)' }}>{countErr}</span>
          <span className="lv__stat-sub">clicca per filtrare</span>
        </button>
      </div>

      {/* Filtri */}
      <div className="lv__filters">
        <div className="lv__filters-row">
          <select className="lv__select" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
            <option value="">Tutti gli stati</option>
            <option value="success">Successo</option>
            <option value="failure">Fallite</option>
            <option value="running">In corso</option>
          </select>
          <select className="lv__select" value={filterModule} onChange={e => setFilterModule(e.target.value)}>
            <option value="">Tutti i moduli</option>
            {['estrai', 'manipola', 'arricchisci', 'carica', 'scheduler'].map(m =>
              <option key={m} value={m}>{m}</option>
            )}
          </select>
          {(filterStatus || filterModule) && (
            <button className="lv__btn lv__btn--ghost lv__btn--sm"
              onClick={() => { setFilterStatus(''); setFilterModule(''); }}>
              ✕ Reset
            </button>
          )}
        </div>
        <div className="lv__filters-info">
          {loading ? 'Caricamento...' : `${total} esecuzioni`}
        </div>
      </div>

      {/* Tabella */}
      <div className="lv__table-wrap">
        <table className="lv__table">
          <thead>
            <tr>
              <th className="lv__th" style={{ width: 130 }}>Avvio</th>
              <th className="lv__th" style={{ width: 90 }}>Stato</th>
              <th className="lv__th" style={{ width: 100 }}>Modulo</th>
              <th className="lv__th">Progetto</th>
              <th className="lv__th" style={{ width: 80 }}>Durata</th>
              <th className="lv__th" style={{ width: 90 }}>Righe out</th>
              <th className="lv__th" style={{ width: 90 }}>Trigger</th>
            </tr>
          </thead>
          <tbody>
            {loading && !runs.length ? (
              <tr><td colSpan="7" className="lv__empty">Caricamento...</td></tr>
            ) : runs.length === 0 ? (
              <tr><td colSpan="7" className="lv__empty">
                <div className="lv__empty-icon">🚀</div>
                <div>Nessuna esecuzione trovata</div>
              </td></tr>
            ) : runs.map(run => {
              const st     = STATUS_CONFIG[run.status] || { label: run.status, cls: '', icon: '?' };
              const modClr = MODULE_COLORS[run.module] || '#6B7280';
              const isExp  = expandedRun === run.run_id;
              const isFail = run.status === 'failure';
              return (
                <React.Fragment key={run.run_id}>
                  <tr
                    className={`lv__row${isFail ? ' lv__row--error' : ''}${isExp ? ' lv__row--expanded' : ''}`}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setExpandedRun(isExp ? null : run.run_id)}>
                    <td className="lv__td lv__td--time">{formatTime(run.ts_start)}</td>
                    <td className="lv__td">
                      <span className={`lv__run-status ${st.cls}`}>
                        <span className="lv__run-status-icon">{st.icon}</span>
                        {st.label}
                      </span>
                    </td>
                    <td className="lv__td">
                      <span className="lv__module-tag" style={{
                        color: modClr, background: `${modClr}18`, borderColor: `${modClr}30`
                      }}>{run.module || '—'}</span>
                    </td>
                    <td className="lv__td" style={{ fontFamily: 'var(--lv-font-m)', fontSize: 11, color: 'var(--lv-text-sub)' }}>
                      {truncateProjectId(run.project_id)}
                    </td>
                    <td className="lv__td" style={{ fontFamily: 'var(--lv-font-m)', fontSize: 12 }}>
                      {fmtDuration(run.duration_ms)}
                    </td>
                    <td className="lv__td" style={{ fontFamily: 'var(--lv-font-m)', fontSize: 12 }}>
                      {fmtRows(run.rows_out)}
                    </td>
                    <td className="lv__td">
                      <span className="lv__op-tag">{run.triggered_by || '—'}</span>
                    </td>
                  </tr>

                  {isExp && (
                    <tr className="lv__detail-row">
                      <td colSpan="7" className="lv__detail-cell">
                        {isFail && run.error_message && (
                          <div className="lv__run-error-banner">
                            <span className="lv__run-error-icon">⚠</span>
                            <span className="lv__run-error-msg">{run.error_message}</span>
                          </div>
                        )}
                        <RunDetail runId={run.run_id} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Paginazione */}
      {total > PAGE && (
        <div className="lv__pagination">
          <button className="lv__btn lv__btn--ghost" disabled={offset === 0}
            onClick={() => load(Math.max(0, offset - PAGE))}>← Precedenti</button>
          <span className="lv__pagination-info">{offset + 1}–{Math.min(offset + PAGE, total)} di {total}</span>
          <button className="lv__btn lv__btn--ghost" disabled={!hasMore}
            onClick={() => load(offset + PAGE)}>Successivi →</button>
        </div>
      )}

    </div>
  );
}
