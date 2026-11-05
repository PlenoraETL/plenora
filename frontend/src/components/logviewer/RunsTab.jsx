/**
 * RunsTab.jsx — Tabella esecuzioni pipeline con filtri e dettaglio
 * Props:
 *   live {boolean} - aggiornamento periodico (Live) della pagina mostrata
 *
 * Fase 5 T2 (R4.13): pagina dall'hook comune con filtri e posizione nella
 * chiave: Live aggiorna la pagina corrente (non torna alla prima), la riga
 * aperta resta aperta (chiave = run_id), il dettaglio aperto si aggiorna
 * con Live; un errore e' un errore con «Riprova», non una tabella vuota.
 */

import React, { useState } from 'react';
import { getLogRuns } from '../../api/logRuns';
import { formatDuration, formatNumber } from '../../utils/format';
import { formatLogTime, projectLabel } from '../../utils/logViewerHelpers.jsx';
import RunDetail from './RunDetail';
import { useResource } from '../../hooks/useResource';
import { ResourceError } from '../common/ListState';
import EmptyState from '../common/EmptyState';
import { pressable } from '../../utils/pressable';

export const LIVE_REFRESH_MS = 5000;

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

export default function RunsTab({ live = false, projectNames = null }) {
  const [offset,       setOffset]       = useState(0);
  const [expandedRun,  setExpandedRun]  = useState(null);
  const [filterStatus, setFilterStatusRaw] = useState('');
  const [filterModule, setFilterModuleRaw] = useState('');
  // Un filtro nuovo riparte dalla prima pagina.
  const setFilterStatus = (v) => { setOffset(0); setFilterStatusRaw(v); };
  const setFilterModule = (v) => { setOffset(0); setFilterModuleRaw(v); };
  const resource = useResource(() => getLogRuns({
    status: filterStatus || undefined,
    module: filterModule || undefined,
    limit:  PAGE,
    offset,
  }), {
    key: { filterStatus, filterModule, offset },
    keepPreviousData: true,
    refreshInterval: live ? LIVE_REFRESH_MS : 0,
  });
  const runs = resource.data?.runs || [];
  const total = resource.data?.total || 0;
  const hasMore = resource.data?.has_more || false;
  const loading = resource.loading;
  const load = (newOffset) => setOffset(newOffset);


  // P21: «Run totali» e' il totale del filtro (server); completate e fallite
  // contano le righe della pagina mostrata e lo dicono.
  const countOk  = runs.filter(r => r.status === 'success').length;
  const countErr = runs.filter(r => r.status === 'failure').length;

  return (
    <div className="lv__page-col">

      {/* Mini stats */}
      <div className="lv__stats lv__stats--3col">
        <div className="lv__stat lv__stat--total">
          <div className="lv__stat-label">Run Totali</div>
          <div className="lv__stat-val">{total}</div>
          <div className="lv__stat-sub">con i filtri attuali</div>
        </div>
        <button
          type="button"
          className={`lv__stat lv__stat--info lv__btn-reset${filterStatus === 'success' ? ' lv__stat--active' : ''}`}
          onClick={() => setFilterStatus(filterStatus === 'success' ? '' : 'success')}
          aria-pressed={filterStatus === 'success'}
          aria-label="Filtra esecuzioni completate">
          <span className="lv__stat-label">Completate</span>
          <span className="lv__stat-val lv__stat-val--ok">{countOk}</span>
          <span className="lv__stat-sub">in questa pagina · clicca per filtrare</span>
        </button>
        <button
          type="button"
          className={`lv__stat lv__stat--error lv__btn-reset${filterStatus === 'failure' ? ' lv__stat--active' : ''}`}
          onClick={() => setFilterStatus(filterStatus === 'failure' ? '' : 'failure')}
          aria-pressed={filterStatus === 'failure'}
          aria-label="Filtra esecuzioni fallite">
          <span className="lv__stat-label">Fallite</span>
          <span className="lv__stat-val lv__stat-val--err">{countErr}</span>
          <span className="lv__stat-sub">in questa pagina · clicca per filtrare</span>
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

      {resource.status === 'error' && (
        <ResourceError resource={resource} title="Esecuzioni non caricate" />
      )}

      {/* Tabella */}
      <div className="lv__table-wrap">
        <table className="lv__table">
          <thead>
            <tr>
              <th className="lv__th lv__th--w-130">Avvio</th>
              <th className="lv__th lv__th--w-90">Stato</th>
              <th className="lv__th lv__th--w-100">Modulo</th>
              <th className="lv__th">Progetto</th>
              <th className="lv__th lv__th--w-80">Durata</th>
              <th className="lv__th lv__th--w-90">Righe out</th>
              <th className="lv__th lv__th--w-90">Trigger</th>
            </tr>
          </thead>
          <tbody>
            {resource.initialLoading ? (
              <tr><td colSpan="7" className="lv__empty">Caricamento...</td></tr>
            ) : resource.status === 'error' && !resource.hasData ? null : runs.length === 0 ? (
              <tr><td colSpan="7">
                <EmptyState icon="🚀" title={filterStatus || filterModule ? 'Nessuna esecuzione con questi filtri' : 'Nessuna esecuzione trovata'} />
              </td></tr>
            ) : runs.map(run => {
              const st     = STATUS_CONFIG[run.status] || { label: run.status, cls: '', icon: '?' };
              const modClr = MODULE_COLORS[run.module] || '#6B7280';
              const isExp  = expandedRun === run.run_id;
              const isFail = run.status === 'failure';
              const detailRowId = `lv-run-detail-${run.run_id}`;
              return (
                <React.Fragment key={run.run_id}>
                  <tr
                    className={`lv__row lv__row--clickable${isFail ? ' lv__row--error' : ''}${isExp ? ' lv__row--expanded' : ''}`}
                    {...pressable(() => setExpandedRun(isExp ? null : run.run_id))}>
                    <td className="lv__td lv__td--time">
                      <button
                        type="button"
                        className="lv__expand-toggle"
                        onClick={e => { e.stopPropagation(); setExpandedRun(isExp ? null : run.run_id); }}
                        aria-expanded={isExp}
                        aria-controls={detailRowId}
                        aria-label={isExp ? 'Comprimi dettagli esecuzione' : 'Espandi dettagli esecuzione'}
                      >
                        {isExp ? '▾' : '▸'}
                      </button>
                      {' '}{formatLogTime(run.ts_start)}
                    </td>
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
                    <td className="lv__td lv__td--mono-11">
                      {projectLabel(run.project_id, projectNames)}
                    </td>
                    <td className="lv__td lv__td--mono-12">
                      {formatDuration(run.duration_ms)}
                    </td>
                    <td className="lv__td lv__td--mono-12">
                      {formatNumber(run.rows_out)}
                    </td>
                    <td className="lv__td">
                      <span className="lv__op-tag">{run.triggered_by || '—'}</span>
                    </td>
                  </tr>

                  {isExp && (
                    <tr className="lv__detail-row" id={detailRowId}>
                      <td colSpan="7" className="lv__detail-cell">
                        {isFail && run.error_message && (
                          <div className="lv__run-error-banner">
                            <span className="lv__run-error-icon">⚠</span>
                            <span className="lv__run-error-msg">{run.error_message}</span>
                          </div>
                        )}
                        <RunDetail runId={run.run_id} live={live && run.status === 'running'} />
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
