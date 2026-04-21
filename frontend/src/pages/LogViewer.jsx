/**
 * LogViewer.jsx — Visualizzatore log di sistema
 * Redesign Plenora design system
 * Route: /logs
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { getLogs, getLogsStats } from '../api/logs';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import RunsTab from '../components/logviewer/RunsTab';

import { useHexCanvas } from '../utils/hexCanvas';
import AppSidebar from '../components/AppSidebar';
import { can } from '../utils/permissions';
import '../styles/LogViewer.css';

const LEVELS   = ['INFO', 'WARNING', 'ERROR'];
const MODULES  = ['estrai', 'manipola', 'arricchisci', 'carica', 'orchestrator', 'scheduler', 'connections', 'projects', 'files', 'services', 'db', 'utils', 'workers'];
const OPS      = ['execute', 'preview', 'save', 'load', 'transform', 'enrich', 'carica', 'validate'];
const PAGE_SIZE = 50;

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

// ── ThemeToggle ───────────────────────────────────────────────────────────
function ThemeToggle() {
  const [theme, setTheme] = useState(() => {
    const s = localStorage.getItem('plenora-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', s);
    return s;
  });
  const toggle = t => { document.documentElement.setAttribute('data-theme', t); localStorage.setItem('plenora-theme', t); setTheme(t); };
  return (
    <div className="lv__theme-toggle">
      <div className={`lv__theme-opt${theme === 'dark'  ? ' lv__theme-opt--on' : ''}`} onClick={() => toggle('dark')}>🌙 Scuro</div>
      <div className={`lv__theme-opt${theme === 'light' ? ' lv__theme-opt--on' : ''}`} onClick={() => toggle('light')}>☀️ Chiaro</div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────
function formatTime(ts) {
  if (!ts) return '—';
  const parts = ts.split('T');
  if (parts.length !== 2) return ts;
  return `${parts[0].slice(5)} ${parts[1].slice(0, 8)}`;
}
function truncateProjectId(id) {
  if (!id || id.length <= 20) return id;
  return id.slice(0, 18) + '…';
}

// ── Main component ────────────────────────────────────────────────────────
function LogViewer() {
  const navigate     = useNavigate();
  const toast        = useToast();
  const { user, logout } = useAuth();
  const canvasRef    = useRef(null);
  useHexCanvas(canvasRef);

  // Capability resolution dal policy file unico (vedi utils/permissions.js)
  const canSeeTechnicalLog = can.seeTechnicalLogs(user);

  const [logs,         setLogs]         = useState([]);
  const [stats,        setStats]        = useState(null);
  const [total,        setTotal]        = useState(0);
  const [hasMore,      setHasMore]      = useState(false);
  const [filterLevel,  setFilterLevel]  = useState('');
  const [filterModule, setFilterModule] = useState('');
  const [filterOp,     setFilterOp]     = useState('');
  const [searchText,   setSearchText]   = useState('');
  const [searchInput,  setSearchInput]  = useState('');
  const [offset,       setOffset]       = useState(0);
  const [autoRefresh,  setAutoRefresh]  = useState(false);
  const [expandedRow,  setExpandedRow]  = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [mainTab,      setMainTab]      = useState('runs');
  const [refreshTick,  setRefreshTick]  = useState(0);
  const intervalRef = useRef(null);

  // Se un utente non-admin ha per qualche motivo mainTab='logs', forziamo
  // 'runs' (l'unico tab che vede). Evita stati incoerenti.
  useEffect(() => {
    if (!canSeeTechnicalLog && mainTab === 'logs') setMainTab('runs');
  }, [canSeeTechnicalLog, mainTab]);

  // Ref di mounted: previene setState() su componente smontato dopo
  // un fetch lento o un tick di setInterval che si scatena durante l'unmount.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const loadLogs = useCallback(async (newOffset = 0) => {
    try {
      setLoading(true);
      const data = await getLogs({ level: filterLevel, module: filterModule, op: filterOp, search: searchText, limit: PAGE_SIZE, offset: newOffset });
      if (!mountedRef.current) return;
      setLogs(data.entries || []);
      setTotal(data.total || 0);
      setHasMore(data.has_more || false);
      setOffset(newOffset);
    } catch (err) {
      console.error('[LogViewer] errore caricamento logs:', err);
      // Non resettiamo lo stato precedente: l'utente vede i log vecchi
      // invece di una pagina vuota improvvisa.
    }
    finally { if (mountedRef.current) setLoading(false); }
  }, [filterLevel, filterModule, filterOp, searchText]);

  const loadStats = useCallback(async () => {
    try {
      const data = await getLogsStats();
      if (!mountedRef.current) return;
      setStats(data);
    } catch (err) {
      console.error('[LogViewer] errore caricamento stats:', err);
    }
  }, []);

  useEffect(() => { loadLogs(0); loadStats(); }, [filterLevel, filterModule, filterOp, searchText]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(() => {
        // Doppio check: il setInterval può scattare DOPO l'unmount se il
        // cleanup non è ancora stato eseguito (race tra clearInterval e tick).
        if (!mountedRef.current) return;
        loadLogs(0);
        loadStats();
        setRefreshTick(t => t + 1);
      }, 5000);
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [autoRefresh, loadLogs, loadStats]);

  const handleSearch = e => { e.preventDefault(); setSearchText(searchInput); };
  const handleExportCSV = () => {
    if (!logs.length) return;
    const headers = ['Timestamp', 'Livello', 'Modulo', 'Operazione', 'Messaggio', 'Progetto', 'Dettagli'];
    const rows = logs.map(l => [l.ts||'', l.level||'', l.module||'', l.op||'', `"${(l.msg||'').replace(/"/g,'""')}"`, l.project_id||'', l.details?`"${JSON.stringify(l.details).replace(/"/g,'""')}"`:'' ]);
    const csv = [headers.join(';'), ...rows.map(r => r.join(';'))].join('\n');
    const blob = new Blob(['\uFEFF'+csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href=url; a.download=`plenora-logs-${new Date().toISOString().slice(0,10)}.csv`; a.click();
    URL.revokeObjectURL(url);
  };
  const resetFilters = () => { setFilterLevel(''); setFilterModule(''); setFilterOp(''); setSearchText(''); setSearchInput(''); };
  const hasFilters = filterLevel || filterModule || filterOp || searchText;

  return (
    <>
    <div className="lv">
      {/* SIDEBAR */}
      <AppSidebar />

      {/* MAIN */}
      <div className="lv__main">
        <header className="lv__topbar">
          <button type="button" className="lv__back-btn" onClick={() => navigate(-1)}>← Indietro</button>
          <div className="lv__page-title">{canSeeTechnicalLog ? 'Log di Sistema' : 'Cronologia Esecuzioni'}</div>
          <div className="lv__topbar-right">
            {/* Live: aggiorna sia i log tecnici che le run, utile su entrambi i tab */}
            <button type="button"
              className={`lv__btn lv__btn--ghost${autoRefresh ? ' lv__btn--live' : ''}`}
              onClick={() => setAutoRefresh(!autoRefresh)}>
              {autoRefresh ? '⏸ Stop Live' : '▶ Live'}
            </button>
            {/* Export CSV: esporta i log tecnici → mostrato solo quando si è
                effettivamente sul tab "Log Tecnici" (cioè solo per admin) */}
            {canSeeTechnicalLog && mainTab === 'logs' && (
              <button type="button" className="lv__btn lv__btn--ghost" onClick={handleExportCSV}>↓ Export CSV</button>
            )}
            <ThemeToggle />
          </div>
        </header>

        <div className="lv__content">
          <canvas ref={canvasRef} className="lv__hex-bg" />

          {/* Tab bar principale — mostrata solo se l'utente ha più di un tab
              disponibile. Per i non-admin esiste solo "Esecuzioni" → niente
              tab bar, va direttamente al contenuto. */}
          {canSeeTechnicalLog && (
            <div className="lv__main-tabs" style={{ position: 'relative', zIndex: 1 }}>
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
          {mainTab === 'runs' && <RunsTab refreshTick={refreshTick} />}

          {/* Tab Log Tecnici — solo admin/superadmin */}
          {mainTab === 'logs' && canSeeTechnicalLog && <>

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
                <div key={s.key}
                  className={`lv__stat lv__stat--${s.mod}${filterLevel === s.key ? ' lv__stat--active' : ''}`}
                  onClick={() => setFilterLevel(filterLevel === s.key ? '' : s.key)}
                  style={{ cursor: 'pointer' }}>
                  <div className="lv__stat-label">{s.label}</div>
                  <div className="lv__stat-val">{s.val}</div>
                  <div className="lv__stat-sub">clicca per filtrare</div>
                </div>
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
              <form className="lv__search" onSubmit={handleSearch}>
                <input type="text" className="lv__search-input"
                  placeholder="Cerca nel messaggio..."
                  value={searchInput} onChange={e => setSearchInput(e.target.value)} />
                <button type="submit" className="lv__search-btn">🔍</button>
              </form>
              {hasFilters && (
                <button type="button" className="lv__btn lv__btn--ghost lv__btn--sm" onClick={resetFilters}>✕ Reset</button>
              )}
            </div>
            <div className="lv__filters-info">
              {loading ? 'Caricamento...' : `${total} risultati${hasFilters ? ' (filtrati)' : ''}`}
              {autoRefresh && <span className="lv__live-badge">● LIVE</span>}
            </div>
          </div>

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
                {loading && !logs.length ? (
                  <tr><td colSpan="6" className="lv__empty">Caricamento...</td></tr>
                ) : logs.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="lv__empty">
                      <div className="lv__empty-icon">📋</div>
                      <div>Nessun log trovato</div>
                      {hasFilters && <button type="button" className="lv__btn lv__btn--ghost lv__btn--sm" style={{ marginTop: 12 }} onClick={resetFilters}>Reset filtri</button>}
                    </td>
                  </tr>
                ) : logs.map((entry, idx) => {
                  const levelCfg = LEVEL_CONFIG[entry.level] || {};
                  const modColor = MODULE_COLORS[entry.module] || '#6B7280';
                  const isExpanded = expandedRow === idx;
                  const hasDetails = entry.details || entry.error || entry.project_id;
                  return (
                    <React.Fragment key={idx}>
                      <tr
                        className={`lv__row lv__row--${(entry.level||'').toLowerCase()}${isExpanded ? ' lv__row--expanded' : ''}`}
                        onClick={() => hasDetails && setExpandedRow(isExpanded ? null : idx)}
                        style={{ cursor: hasDetails ? 'pointer' : 'default' }}>
                        <td className="lv__td lv__td--time">{formatTime(entry.ts)}</td>
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
                          {hasDetails && <span className="lv__expand-icon">{isExpanded ? '▾' : '▸'}</span>}
                        </td>
                        <td className="lv__td lv__td--project">
                          {entry.project_id ? (
                            <span className="lv__project-link"
                              onClick={e => { e.stopPropagation(); navigate(`/project/${entry.project_id}`); }}
                              title={entry.project_id}>
                              {truncateProjectId(entry.project_id)}
                            </span>
                          ) : '—'}
                        </td>
                      </tr>
                      {isExpanded && hasDetails && (
                        <tr className="lv__detail-row">
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
      </div>
    </div>
    </>
  );
}

export default LogViewer;
