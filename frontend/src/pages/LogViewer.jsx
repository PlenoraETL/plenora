/**
 * LogViewer.jsx — Visualizzatore log di sistema
 * Redesign Plenora design system
 * Route: /logs
 */

import React, { useState, useEffect, useRef } from 'react';
import { useResource } from '../hooks/useResource';
import { saveBlob } from '../utils/downloadBlob';
import { ResourceError } from '../components/common/ListState';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getLogs, getLogsStats } from '../api/logs';
import { useAuth } from '../context/AuthContext';
import RunsTab from '../components/logviewer/RunsTab';
import { formatLogTime, projectLabel } from '../utils/logViewerHelpers.jsx';
import { useProjectNames } from '../hooks/useProjectNames';

import { useHexCanvas } from '../utils/hexCanvas';
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch, useSearchPage } from '../hooks/useSearch';
import { useToast } from '../hooks/useToast';
import { toastApiError } from '../utils/toastApiError';
import { formatNumber } from '../utils/format';
import { can } from '../utils/permissions';
import '../styles/LogViewer.css';
import { openProjectPath } from '../utils/projectRoutes';
import { pressable } from '../utils/pressable';

const LEVELS   = ['INFO', 'WARNING', 'ERROR'];
const MODULES  = ['estrai', 'manipola', 'arricchisci', 'carica', 'orchestrator', 'scheduler', 'connections', 'projects', 'files', 'services', 'db', 'utils', 'workers'];
const OPS      = ['execute', 'preview', 'save', 'load', 'transform', 'enrich', 'carica', 'validate'];
const PAGE_SIZE = 50;
const EXPORT_PAGE_SIZE = 1000; // massimo di GET /logs (core/logs/router.py)
const EXPORT_MAX_ROWS = 20000;

const LEVEL_CONFIG = {
  INFO:    { cls: 'lv__badge--info',  dot: 'var(--lv-blue)', label: 'INFO' },
  WARNING: { cls: 'lv__badge--warn',  dot: 'var(--lv-warn)', label: 'WARN' },
  ERROR:   { cls: 'lv__badge--error', dot: 'var(--lv-err)',  label: 'ERROR' },
};

const MODULE_COLORS = {
  estrai: '#34C17A', manipola: '#9B6DFF', arricchisci: '#F5A623', carica: '#4B8BFF',
  orchestrator: '#60A5FA', scheduler: '#34D399', connections: '#FBBF24', projects: '#818CF8',
  files: '#A78BFA', services: '#94A3B8', db: '#F87171', utils: '#6B7280', workers: '#F472B6',
};
function LogViewer() {
  const navigate     = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const projectNames = useProjectNames();
  const canvasRef    = useRef(null);
  useHexCanvas(canvasRef);

  // Capability resolution dal policy file unico (vedi utils/permissions.js)
  const canSeeTechnicalLog = can.seeTechnicalLogs(user);

  const [filterLevel,  setFilterLevelRaw]  = useState('');
  const [filterModule, setFilterModuleRaw] = useState('');
  const [filterOp,     setFilterOpRaw]     = useState('');
  // Ricerca lato server (hooks/useSearch): la stessa pausa delle altre
  // ricerche; la query applicata entra nella chiave di useResource, che
  // tiene una richiesta alla volta. Una ricerca nuova riparte dalla prima
  // pagina: la pagina e' legata alla ricerca per cui e' stata scelta.
  const search = useSearch();
  const searchText = search.query;
  const [offset,       setOffset]       = useSearchPage(search, 0);
  const [autoRefresh,  setAutoRefresh]  = useState(false);
  const [expandedRow,  setExpandedRow]  = useState(null);
  const [mainTab,      setMainTab]      = useState('runs');
  // «Cronologia» della home per catene: /logs?project=<id> mostra le sue esecuzioni.
  const [searchParams, setSearchParams] = useSearchParams();
  const projectFilter = searchParams.get('project') || '';
  const clearProjectFilter = () => setSearchParams(p => { const n = new URLSearchParams(p); n.delete('project'); return n; }, { replace: true });
  // Un filtro nuovo riparte dalla prima pagina.
  const resetting = (setter) => (value) => { setOffset(0); setter(value); };
  const setFilterLevel = resetting(setFilterLevelRaw);
  const setFilterModule = resetting(setFilterModuleRaw);
  const setFilterOp = resetting(setFilterOpRaw);

  // Se un utente non-admin ha per qualche motivo mainTab='logs', forziamo
  // 'runs' (l'unico tab che vede). Evita stati incoerenti.
  useEffect(() => {
    if (!canSeeTechnicalLog && mainTab === 'logs') setMainTab('runs');
  }, [canSeeTechnicalLog, mainTab]);

  // Fase 5 T2 (R4.13): log tecnici e statistiche dall'hook comune, chiesti
  // SOLO a chi puo' vederli e solo sulla loro scheda; Live aggiorna la
  // pagina mostrata (non torna alla prima) e si ferma con la scheda nascosta.
  const techTab = canSeeTechnicalLog && mainTab === 'logs';
  const logsResource = useResource(() => getLogs({
    level: filterLevel, module: filterModule, op: filterOp, search: searchText, limit: PAGE_SIZE, offset,
  }), {
    key: { filterLevel, filterModule, filterOp, searchText, offset },
    enabled: techTab,
    keepPreviousData: true,
    refreshInterval: autoRefresh ? 'live' : 0,
  });
  const statsResource = useResource(getLogsStats, {
    key: 'logs-stats',
    enabled: techTab,
    refreshInterval: autoRefresh ? 'live' : 0,
  });
  const logs = logsResource.data?.entries || [];
  const total = logsResource.data?.total || 0;
  const hasMore = logsResource.data?.has_more || false;
  const stats = statsResource.data || null;
  const loading = logsResource.loading;
  const loadLogs = (newOffset) => setOffset(newOffset);
  // Riga aperta per identita' del log, non per posizione: un aggiornamento
  // che aggiunge righe in testa non apre un'altra riga.
  const entryKey = (entry) => entry.id || `${entry.ts}|${entry.module}|${entry.op}|${entry.msg}`;

  // P21: l'export contiene TUTTE le righe del filtro corrente (pagine da
  // EXPORT_PAGE_SIZE fino a EXPORT_MAX_ROWS), non solo la pagina mostrata.
  const [exporting, setExporting] = useState(false);
  const handleExportCSV = async () => {
    if (exporting || !total) return;
    setExporting(true);
    try {
      const rowsOut = [];
      const filters = { level: filterLevel, module: filterModule, op: filterOp, search: searchText };
      for (let page = 0; rowsOut.length < EXPORT_MAX_ROWS; page += 1) {
        const data = await getLogs({ ...filters, limit: EXPORT_PAGE_SIZE, offset: page * EXPORT_PAGE_SIZE });
        rowsOut.push(...(data?.entries || []));
        if (!data?.has_more || !(data?.entries || []).length) break;
      }
      const truncated = total > EXPORT_MAX_ROWS;
      const headers = ['Timestamp (UTC)', 'Livello', 'Modulo', 'Operazione', 'Messaggio', 'Progetto', 'Dettagli'];
      const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
      const rows = rowsOut.slice(0, EXPORT_MAX_ROWS).map(l => [l.ts || '', l.level || '', l.module || '', l.op || '', q(l.msg), q(projectLabel(l.project_id, projectNames)), l.details ? q(JSON.stringify(l.details)) : '']);
      const csv = [headers.join(';'), ...rows.map(r => r.join(';'))].join('\n');
      const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
      saveBlob(blob, `plenora-logs-${new Date().toISOString().slice(0, 10)}.csv`);
      if (truncated) toast.warning(`Esportate le prime ${formatNumber(EXPORT_MAX_ROWS)} righe di ${formatNumber(total)}: restringi i filtri per il resto.`);
    } catch (e) {
      toastApiError(toast, e, 'Export non riuscito');
    } finally {
      setExporting(false);
    }
  };
  const resetFilters = () => { setFilterLevel(''); setFilterModule(''); setFilterOp(''); search.clear(); };
  const hasFilters = filterLevel || filterModule || filterOp || searchText;

  return (
    <PageLayout
      className="lv"
      bare
      title={canSeeTechnicalLog ? 'Log di Sistema' : 'Cronologia Esecuzioni'}
      subtitle="Orari nel fuso di questo browser"
      actions={(
        <>
          {/* Live: aggiorna sia i log tecnici che le run, utile su entrambi i tab */}
          <button type="button"
            className={`lv__btn lv__btn--ghost${autoRefresh ? ' lv__btn--live' : ''}`}
            onClick={() => setAutoRefresh(!autoRefresh)}>
            {autoRefresh ? '⏸ Stop Live' : '▶ Live'}
          </button>
          {/* Export CSV dei log tecnici (solo admin, sul loro tab) */}
          {canSeeTechnicalLog && mainTab === 'logs' && (
            <button type="button" className="lv__btn lv__btn--ghost" onClick={handleExportCSV} disabled={exporting || !total}>
              {exporting ? 'Esportazione…' : `↓ Export CSV (${formatNumber(total)})`}
            </button>
          )}
        </>
      )}
    >

        <div className="lv__content">
          <canvas ref={canvasRef} className="lv__hex-bg" />

          {/* Tab bar principale — mostrata solo se l'utente ha più di un tab
              disponibile. Per i non-admin esiste solo "Esecuzioni" → niente
              tab bar, va direttamente al contenuto. */}
          {canSeeTechnicalLog && (
            <div className="lv__main-tabs">
              <button
                className={`lv__main-tab${mainTab === 'runs' ? ' lv__main-tab--on' : ''}`}
                onClick={() => setMainTab('runs')}>
                🚀 Esecuzioni
              </button>
              <button
                className={`lv__main-tab${mainTab === 'logs' ? ' lv__main-tab--on' : ''}`}
                onClick={() => setMainTab('logs')}>
                📋 Log Tecnici
              </button>
            </div>
          )}

          {/* Tab Esecuzioni — visibile a tutti gli utenti loggati */}
          {mainTab === 'runs' && <RunsTab projectNames={projectNames} live={autoRefresh} projectId={projectFilter} onClearProject={clearProjectFilter} />}

          {/* Tab Log Tecnici — solo admin/superadmin */}
          {mainTab === 'logs' && canSeeTechnicalLog && <>

          <ResourceError resource={statsResource} title="Statistiche non caricate" />
          {stats && (
            <div className="lv__stats">
              <div className="lv__stat lv__stat--total">
                <div className="lv__stat-label">Log Totali</div>
                <div className="lv__stat-val">{stats.total}</div>
                <div className="lv__stat-sub">tutti i livelli</div>
              </div>
              {[
                { key: 'INFO',    label: 'Info',    mod: 'info',  val: stats.by_level?.INFO    || 0 },
                { key: 'WARNING', label: 'Warning', mod: 'warn',  val: stats.by_level?.WARNING || 0 },
                { key: 'ERROR',   label: 'Errori',  mod: 'error', val: stats.by_level?.ERROR   || 0 },
              ].map(s => (
                <button
                  type="button"
                  key={s.key}
                  className={`lv__stat lv__stat--${s.mod} lv__btn-reset${filterLevel === s.key ? ' lv__stat--active' : ''}`}
                  onClick={() => setFilterLevel(filterLevel === s.key ? '' : s.key)}
                  aria-pressed={filterLevel === s.key}
                  aria-label={`Filtra log per livello ${s.label}`}>
                  <span className="lv__stat-label">{s.label}</span>
                  <span className="lv__stat-val">{s.val}</span>
                  <span className="lv__stat-sub">clicca per filtrare</span>
                </button>
              ))}
            </div>
          )}

          <div className="lv__filters">
            <div className="lv__filters-row">
              <select className="lv__select" value={filterLevel} onChange={e => setFilterLevel(e.target.value)}>
                <option value="">Tutti i livelli</option>
                {LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
              </select>
              <select className="lv__select" value={filterModule} onChange={e => setFilterModule(e.target.value)}>
                <option value="">Tutti i moduli</option>
                {MODULES.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select className="lv__select" value={filterOp} onChange={e => setFilterOp(e.target.value)}>
                <option value="">Tutte le operazioni</option>
                {OPS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
              <SearchField
                search={search}
                label="Cerca nel messaggio"
                className="lv__search"
                shown={logsResource.hasData ? total : null}
              />
              {hasFilters && (
                <button type="button" className="lv__btn lv__btn--ghost lv__btn--sm" onClick={resetFilters}>✕ Reset</button>
              )}
            </div>
            <div className="lv__filters-info">
              {loading ? 'Caricamento...' : `${total} risultati${hasFilters ? ' (filtrati)' : ''}`}
              {autoRefresh && <span className="lv__live-badge">● LIVE</span>}
            </div>
          </div>

          {/* Errore SEMPRE visibile (filtro, pagina, Live, Aggiorna): senza
              dati al posto della tabella, con dati come avviso sopra. */}
          <ResourceError resource={logsResource} title="Log non caricati" />
          <div className="lv__table-wrap">
            <table className="lv__table">
              <thead>
                <tr>
                  <th className="lv__th lv__th--time">Timestamp</th>
                  <th className="lv__th lv__th--level">Livello</th>
                  <th className="lv__th lv__th--module">Modulo</th>
                  <th className="lv__th lv__th--op">Op</th>
                  <th className="lv__th lv__th--msg">Messaggio</th>
                  <th className="lv__th lv__th--project">Progetto</th>
                </tr>
              </thead>
              <tbody>
                {logsResource.initialLoading ? (
                  <tr><td colSpan="6" className="lv__empty">Caricamento...</td></tr>
                ) : logsResource.status === 'error' && !logsResource.hasData ? null : logs.length === 0 && search.active ? (
                  <tr><td colSpan="6"><SearchEmpty search={search} shown={0} /></td></tr>
                ) : logs.length === 0 ? (
                  <tr>
                    <td colSpan="6">
                      <EmptyState
                        icon="📋"
                        title={hasFilters ? 'Nessun log con questi filtri' : 'Nessun log trovato'}
                        action={hasFilters && <button type="button" className="lv__btn lv__btn--ghost lv__btn--sm" onClick={resetFilters}>Reset filtri</button>}
                      />
                    </td>
                  </tr>
                ) : logs.map((entry, idx) => {
                  const levelCfg = LEVEL_CONFIG[entry.level] || {};
                  const modColor = MODULE_COLORS[entry.module] || '#6B7280';
                  const rowKey = entryKey(entry);
                  const isExpanded = expandedRow === rowKey;
                  const hasDetails = entry.details || entry.error || entry.project_id;
                  const detailRowId = `lv-detail-row-${idx}`;
                  return (
                    <React.Fragment key={`${rowKey}|${idx}`}>
                      <tr
                        className={`lv__row lv__row--${(entry.level||'').toLowerCase()} ${hasDetails ? 'lv__row--expandable' : 'lv__row--inert'}${isExpanded ? ' lv__row--expanded' : ''}`}
                        {...pressable(() => hasDetails && setExpandedRow(isExpanded ? null : rowKey))}>
                        <td className="lv__td lv__td--time">
                          {hasDetails && (
                            <button
                              type="button"
                              className="lv__expand-toggle"
                              onClick={e => { e.stopPropagation(); setExpandedRow(isExpanded ? null : rowKey); }}
                              aria-expanded={isExpanded}
                              aria-controls={detailRowId}
                              aria-label={isExpanded ? 'Comprimi dettagli log' : 'Espandi dettagli log'}
                            >
                              {isExpanded ? '▾' : '▸'}
                            </button>
                          )}
                          {' '}{formatLogTime(entry.ts)}
                        </td>
                        <td className="lv__td lv__td--level">
                          <span className={`lv__badge ${levelCfg.cls || ''}`}>{entry.level}</span>
                        </td>
                        <td className="lv__td lv__td--module">
                          <span className="lv__module-tag" style={{ color: modColor, background: `${modColor}18`, borderColor: `${modColor}30` }}>
                            {entry.module}
                          </span>
                        </td>
                        <td className="lv__td lv__td--op">
                          {entry.op ? <span className="lv__op-tag">{entry.op}</span> : <span className="lv__op-empty">—</span>}
                        </td>
                        <td className="lv__td lv__td--msg">
                          <span className="lv__msg-text">{entry.msg}</span>
                        </td>
                        <td className="lv__td lv__td--project">
                          {entry.project_id ? (
                            <button type="button" className="lv__project-link"
                              onClick={e => { e.stopPropagation(); navigate(openProjectPath(entry.project_id)); }}
                              title={entry.project_id}>
                              {projectLabel(entry.project_id, projectNames)}
                            </button>
                          ) : '—'}
                        </td>
                      </tr>
                      {isExpanded && hasDetails && (
                        <tr className="lv__detail-row" id={detailRowId}>
                          <td colSpan="6" className="lv__detail-cell">
                            <div className="lv__detail">
                              {entry.project_id && (
                                <div className="lv__detail-field">
                                  <span className="lv__detail-label">Progetto</span>
                                  <span className="lv__detail-val">{entry.project_id}</span>
                                </div>
                              )}
                              {entry.details && (
                                <div className="lv__detail-field">
                                  <span className="lv__detail-label">Dettagli</span>
                                  <pre className="lv__detail-json">{JSON.stringify(entry.details, null, 2)}</pre>
                                </div>
                              )}
                              {entry.error && (
                                <div className="lv__detail-field lv__detail-field--error">
                                  <span className="lv__detail-label">Errore</span>
                                  <pre className="lv__detail-error">{entry.error}</pre>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          {total > PAGE_SIZE && (
            <div className="lv__pagination">
              <button type="button" className="lv__btn lv__btn--ghost" disabled={offset === 0}
                onClick={() => loadLogs(Math.max(0, offset - PAGE_SIZE))}>← Precedenti</button>
              <span className="lv__pagination-info">{offset+1}–{Math.min(offset+PAGE_SIZE,total)} di {total}</span>
              <button type="button" className="lv__btn lv__btn--ghost" disabled={!hasMore}
                onClick={() => loadLogs(offset + PAGE_SIZE)}>Successivi →</button>
            </div>
          )}
          </>}
        </div>
    </PageLayout>
  );
}

export default LogViewer;
