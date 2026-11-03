/**
 * ScheduleList.jsx — Gestione schedulazioni
 * Redesign Plenora: sidebar layout, hex bg, card con accent bar
 * Route: /schedules
 */
import React, { useState, useEffect, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  listSchedules, deleteSchedule, toggleSchedule,
  runScheduleNow, getBeatHealthcheck, getScheduleLogs,
  listScheduleRuns, downloadScheduleOutput
} from '../api/schedules';
import ScheduleWizard from '../components/modals/ScheduleWizard';
import { useHexCanvas } from '../utils/hexCanvas';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import { can } from '../utils/permissions';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { toastApiError } from '../utils/toastApiError';
import '../styles/ScheduleList.css';
const formatDate = (s) => {
  if (!s) return '—';
  return new Date(s).toLocaleDateString('it-IT', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
};
const formatCron = (cron) => {
  if (!cron) return '';
  const map = {
    '0 * * * *':'Ogni ora','*/30 * * * *':'Ogni 30 min','*/15 * * * *':'Ogni 15 min',
    '0 0 * * *':'Ogni giorno a mezzanotte','0 3 * * *':'Ogni notte alle 03:00',
    '0 6 * * *':'Ogni mattina alle 06:00','0 0 * * 1':'Ogni lunedì a mezzanotte',
    '0 0 1 * *':'Il primo del mese',
  };
  return map[cron] || cron;
};
const formatSize = (bytes) => {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024*1024) return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1024/1024).toFixed(1)} MB`;
};
const formatRunTimestamp = (ts) => {
  if (!ts || ts.length < 15) return ts;
  const [y,m,d,hh,mm] = [ts.slice(0,4),ts.slice(4,6),ts.slice(6,8),ts.slice(9,11),ts.slice(11,13)];
  const date = new Date(`${y}-${m}-${d}T${hh}:${mm}:00`);
  return date.toLocaleDateString('it-IT',{day:'2-digit',month:'short',year:'numeric'})+', '+date.toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'});
};
const getElapsed = (v) => {
  if (!v) return null;
  const ms = typeof v === 'number' ? v : new Date(v).getTime();
  const s = Math.floor((Date.now()-ms)/1000);
  if (s < 0) return '0s';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
};
const getStatusConfig = (status) => ({
  idle:      { label:'Inattivo',       cls:'sl__badge--idle',      bar:'var(--sl-text-dim)' },
  running:   { label:'In esecuzione',  cls:'sl__badge--running',   bar:'var(--sl-blue)' },
  queued:    { label:'In coda',        cls:'sl__badge--queued',    bar:'var(--sl-warn)' },
  completed: { label:'Completato',     cls:'sl__badge--ok',        bar:'var(--sl-ok)' },
  partial:   { label:'Parziale',       cls:'sl__badge--warn',      bar:'var(--sl-warn)' },
  failed:    { label:'Fallito',        cls:'sl__badge--failed',    bar:'var(--sl-err)' },
  disabled:  { label:'Disabilitato',   cls:'sl__badge--idle',      bar:'var(--sl-text-dim)' },
  orphan:    { label:'Orfano',         cls:'sl__badge--idle',      bar:'var(--sl-text-dim)' },
}[status] || { label:status, cls:'sl__badge--idle', bar:'var(--sl-text-dim)' });

const getProjectTypeConfig = (type) => ({
  estrai:      { icon:'📥', color:'rgba(52,193,122,0.12)',  dot:'#34C17A' },
  manipola:    { icon:'⚙️', color:'rgba(155,109,255,0.12)', dot:'#9B6DFF' },
  arricchisci: { icon:'🔗', color:'rgba(245,166,35,0.12)',  dot:'#F5A623' },
}[type] || { icon:'📋', color:'rgba(75,139,255,0.10)', dot:'#4B8BFF' });

const getLogIcon = (event) => {
  if (event?.includes('COMPLETED')) return '✅';
  if (event?.includes('PARTIAL'))   return '⚠️';
  if (event?.includes('FAILED'))    return '❌';
  if (event?.includes('SKIPPED'))   return '⏭️';
  if (event?.includes('RETRY'))     return '🔁';
  return '📋';
};
function ScheduleList() {
  const toast   = useToast();
  const confirmModal = useConfirmModal();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const projectFilter = searchParams.get('project_id');
  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  const [schedules,      setSchedules]      = useState([]);
  const [loading,        setLoading]        = useState(true);
  const loadedOnceRef = useRef(false);
  const [beatHealth,     setBeatHealth]     = useState(null);
  const [expandedLogs,   setExpandedLogs]   = useState({});
  const [expandedRuns,   setExpandedRuns]   = useState({});
  const [activeFilter,   setActiveFilter]   = useState('all');
  const [showWizard,     setShowWizard]     = useState(false);
  const [editingId,      setEditingId]      = useState(null);
  const [runStartTimes,  setRunStartTimes]  = useState({});
  const [runningManual,  setRunningManual]  = useState({});
  const [tick,           setTick]           = useState(0);
  const pollingRef = useRef(null);
  const tickRef    = useRef(null);
  const pollingGenerationRef = useRef(0);
  // Generation counter per scartare response stale dopo cambio projectFilter.
  // Pre-fix (P1 contracts-flows): se l'utente cambiava filter mentre una
  // listSchedules in volo non era ancora arrivata, la response stale
  // (filter precedente) sovrascriveva setSchedules dopo che la nuova
  // listSchedules era partita. Pattern equivalente a AbortController
  // ma Go-friendly: monotonic counter int condiviso.
  const requestGenRef = useRef(0);

  useEffect(() => {
    // Bump generation: tutte le response in volo precedenti diventano stale.
    requestGenRef.current += 1;
    loadData();
    return () => stopPolling();
  }, [projectFilter]);

  useEffect(() => {
    const runningIds = new Set(schedules.filter(s => s.runtime?.status === 'running' || s.runtime?.status === 'queued').map(s => s.id));
    setRunStartTimes(prev => {
      const next = { ...prev };
      runningIds.forEach(id => { if (!next[id]) next[id] = Date.now(); });
      Object.keys(next).forEach(id => { if (!runningIds.has(id)) delete next[id]; });
      return next;
    });
    setRunningManual(prev => {
      const next = { ...prev };
      Object.keys(next).forEach(id => { if (!runningIds.has(id)) delete next[id]; });
      return next;
    });
    if (runningIds.size > 0 && !pollingRef.current) startPolling();
    else if (runningIds.size === 0 && pollingRef.current) stopPolling();
  }, [schedules]);

  const startPolling = () => {
    if (pollingRef.current) return;
    const generation = ++pollingGenerationRef.current;
    const poll = async () => {
      await refreshData();
      if (
        pollingRef.current
        && pollingGenerationRef.current === generation
      ) pollingRef.current = setTimeout(poll, 3000);
    };
    pollingRef.current = setTimeout(poll, 3000);
    tickRef.current    = setInterval(() => setTick(t => t+1), 1000);
  };
  const stopPolling = () => {
    pollingGenerationRef.current += 1;
    if (pollingRef.current) { clearTimeout(pollingRef.current); pollingRef.current = null; }
    if (tickRef.current)    { clearInterval(tickRef.current);    tickRef.current    = null; }
  };
  const refreshData = async () => {
    const myGen = requestGenRef.current;
    try {
      const d = await listSchedules(projectFilter);
      // Scarta response stale: se projectFilter e' cambiato durante
      // l'await, l'effect ha incrementato requestGenRef e questa
      // response e' obsoleta — non sovrascrivere setSchedules con
      // dati del filter precedente.
      if (myGen !== requestGenRef.current) return;
      setSchedules(d.schedules || []);
    }
    catch (err) {
      console.error('[ScheduleList] refresh fallito:', err);
      // Sessione scaduta o accesso revocato: fermare il polling evita
      // un flood di richieste 401/403 che inquinano i log server e
      // generano rumore lato monitoring.
      const status = err?.response?.status;
      if (status === 401 || status === 403) {
        stopPolling();
      }
    }
  };
  const loadData = async () => {
    const myGen = requestGenRef.current;
    try {
      // Spinner a pagina intera solo al primo caricamento: i reload dopo
      // toggle/delete/salvataggio aggiornano la lista senza farla sparire
      // (prima la pagina lampeggiava e perdeva lo scroll; audit M1).
      if (!loadedOnceRef.current) setLoading(true);
      const [sd, hd] = await Promise.all([
        listSchedules(projectFilter),
        getBeatHealthcheck().catch((err) => {
          // Pre-fix: silent catch nascondeva errori network/auth del
          // beat healthcheck. Log esplicito facilita il triage quando
          // lo scheduler banner mostra "unknown" per troppo tempo.
          console.warn('[ScheduleList] beat healthcheck fallito:', err?.message || err);
          return { status: 'unknown', message: 'Non raggiungibile' };
        }),
      ]);
      // Scarta response stale: vedi requestGenRef in refreshData.
      if (myGen !== requestGenRef.current) return;
      setSchedules(sd.schedules || []);
      setBeatHealth(hd);
      const init = {};
      (sd.schedules || []).forEach(s => { if (s.runtime?.status === 'running' || s.runtime?.status === 'queued') init[s.id] = Date.now(); });
      if (Object.keys(init).length) setRunStartTimes(prev => ({ ...prev, ...init }));
    } catch (err) {
      // Pre-fix (P1 frontend-pages): solo console.error → l'utente
      // vedeva la pagina vuota senza spiegazione se listSchedules
      // falliva (auth, network, DB transient).
      console.error('[ScheduleList] load fallito:', err);
      const msg = err?.response?.data?.message || err?.message || 'Errore caricamento schedules';
      toast.error(msg);
    } finally {
      loadedOnceRef.current = true;
      setLoading(false);
    }
  };

  const handleToggle = async (id, current) => {
    // Optimistic update + rollback su failure: l'utente vede subito il
    // nuovo stato. Senza optimistic UX il toggle "lampeggia" (state
    // vecchio → reload completo del backend → nuovo state); con questo
    // pattern il visual feedback e' immediato e si annulla in caso di
    // errore prima del reload.
    // Lo stato attivo vive in `schedule.enabled` (e' quello che la UI legge):
    // prima l'ottimistico scriveva `s.enabled` e il toggle non si muoveva
    // (audit R9.12).
    const withEnabled = (s, value) => ({ ...s, schedule: { ...s.schedule, enabled: value } });
    const next = !current;
    setSchedules(prev => prev.map(s => (s.id === id ? withEnabled(s, next) : s)));
    try {
      await toggleSchedule(id, next);
      loadData();
    } catch (e) {
      // Rollback: ripristina lo state precedente prima di mostrare l'errore.
      setSchedules(prev => prev.map(s => (s.id === id ? withEnabled(s, current) : s)));
      toastApiError(toast, e, 'Errore toggle');
    }
  };
  const handleDelete = async (id, desc) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina schedule',
      message: `Eliminare "${desc || id}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteSchedule(id);
      loadData();
    } catch (e) {
      // Pre-fix (P1 contracts-flows): error.message generico non distingue
      // 404 (schedule gia' cancellato), 409 (cascaded reference in active
      // runs), 403 (permission). Post-fix: branching su status per
      // messaggi user-friendly + auto-refresh quando appropriato.
      const status = e?.response?.status;
      if (status === 404) {
        toast.warning('Schedule gia\' eliminato (404). Aggiorno la lista.');
        loadData();
        return;
      }
      if (status === 409) {
        const detail = e?.response?.data?.message || 'esecuzione in corso o dipendenza attiva';
        toast.error(`Impossibile eliminare: ${detail}`);
        return;
      }
      const msg = e?.response?.data?.message || e.message || 'errore sconosciuto';
      toast.error(`Errore eliminazione: ${msg}`);
    }
  };
  const handleRunNow = async (id) => {
    if (runningManual[id]) return;
    // Pre-fix (P2 contracts-flows): l'`runningManual[id]` veniva
    // settato ottimisticamente ma `schedules[id].runtime.status`
    // restava al valore precedente fino al successivo refreshData
    // (~3s di polling). Il badge "Inattivo" rimaneva visibile per
    // tutto l'intervallo, sembrava che il bottone non rispondesse.
    // Post-fix: aggiorniamo anche `runtime.status='queued'` con
    // rollback al valore originale nel catch — pattern speculare a
    // `handleToggle` per coerenza UX.
    const prevStatus = schedules.find(s => s.id === id)?.runtime?.status;
    try {
      setRunningManual(prev => ({ ...prev, [id]: true }));
      setRunStartTimes(prev => ({ ...prev, [id]: Date.now() }));
      setSchedules(prev => prev.map(s =>
        s.id === id ? { ...s, runtime: { ...(s.runtime || {}), status: 'queued' } } : s
      ));
      await runScheduleNow(id);
      await refreshData();
    } catch (e) {
      // Rollback ottimistico: ripristina lo status precedente.
      setSchedules(prev => prev.map(s =>
        s.id === id ? { ...s, runtime: { ...(s.runtime || {}), status: prevStatus } } : s
      ));
      toastApiError(toast, e, 'Errore');
      setRunningManual(prev => { const n={...prev}; delete n[id]; return n; });
      setRunStartTimes(prev => { const n={...prev}; delete n[id]; return n; });
    }
  };
  const handleToggleLogs = async (id) => {
    if (expandedLogs[id]) { setExpandedLogs(prev => { const n={...prev}; delete n[id]; return n; }); return; }
    try { const d = await getScheduleLogs(id, 1, 5); setExpandedLogs(prev => ({ ...prev, [id]: d.logs || [] })); }
    catch (err) {
      console.error('[ScheduleList] logs fallito:', err);
      toastApiError(toast, err, 'Impossibile caricare i log');
    }
  };
  const handleToggleRuns = async (id) => {
    if (expandedRuns[id]) { setExpandedRuns(prev => { const n={...prev}; delete n[id]; return n; }); return; }
    try { const d = await listScheduleRuns(id); setExpandedRuns(prev => ({ ...prev, [id]: d.runs || [] })); }
    catch (err) {
      console.error('[ScheduleList] runs fallito:', err);
      toastApiError(toast, err, 'Impossibile caricare le esecuzioni');
    }
  };
const enabledCount   = schedules.filter(s => s.schedule?.enabled).length;
  const failedCount    = schedules.filter(s => s.runtime?.status === 'failed').length;
  const runningCount   = schedules.filter(s => s.runtime?.status === 'running' || s.runtime?.status === 'queued').length;
  const orphanCount    = schedules.filter(s => s.runtime?.status === 'orphan').length;
  const disabledCount  = schedules.filter(s => !s.schedule?.enabled && s.runtime?.status !== 'orphan').length;
  const completedCount = schedules.filter(s => s.runtime?.status === 'completed' || s.runtime?.status === 'partial').length;

  const filterConfigs = {
    all:       { label:'Tutti',         count:schedules.length, icon:'📋' },
    active:    { label:'Attivi',        count:enabledCount,     icon:'✅' },
    running:   { label:'In esecuzione', count:runningCount,     icon:'⚡' },
    completed: { label:'Completati',    count:completedCount,   icon:'✔️' },
    failed:    { label:'Falliti',       count:failedCount,      icon:'❌' },
    orphan:    { label:'Orfani',        count:orphanCount,      icon:'👻' },
    disabled:  { label:'Disabilitati',  count:disabledCount,    icon:'⏸' },
  };
  const filteredSchedules = schedules.filter(s => {
    switch(activeFilter) {
      case 'active':    return s.schedule?.enabled;
      case 'running':   return s.runtime?.status === 'running' || s.runtime?.status === 'queued';
      case 'completed': return s.runtime?.status === 'completed' || s.runtime?.status === 'partial';
      case 'failed':    return s.runtime?.status === 'failed';
      case 'orphan':    return s.runtime?.status === 'orphan';
      case 'disabled':  return !s.schedule?.enabled && s.runtime?.status !== 'orphan';
      default:          return true;
    }
  });

  if (loading) return (
    <div className="sl sl--loading"><div className="sl__loading-inner"><div className="sl__loading-icon">⬡</div>Caricamento schedule...</div></div>
  );

  return (
    <div className="sl">
      {/* SIDEBAR */}
      <AppSidebar counts={{ schedules: schedules.length }} />

      {/* MAIN */}
      <div className="sl__main">
        <header className="sl__topbar">
          <button type="button" className="sl__back-btn" onClick={() => navigate(-1)}>← Indietro</button>
          <div className="sl__page-title">{projectFilter ? 'Scheduler (filtrato)' : 'Scheduler'}</div>
          <div className="sl__topbar-right">
            {projectFilter && <button type="button" className="sl__btn sl__btn--ghost" onClick={() => navigate('/schedules')}>Mostra tutti</button>}
            <ThemeToggle classPrefix="sl" />
            {can.createSchedule(user) && (
              <button type="button" className="sl__btn sl__btn--primary" onClick={() => { setEditingId(null); setShowWizard(true); }}>＋ Nuovo Schedule</button>
            )}
          </div>
        </header>

        <div className="sl__content">
          <canvas ref={canvasRef} className="sl__hex-bg" />

          {/* Beat health banner */}
          {beatHealth && beatHealth.status !== 'ok' && (
            <div className={`sl__banner sl__banner--${beatHealth.status === 'down' ? 'error' : 'warning'}`}>
              {beatHealth.status === 'down' ? '🔴' : '🟡'} {beatHealth.status === 'down' ? 'Beat scheduler non attivo — gli schedule non verranno eseguiti automaticamente.' : beatHealth.message}
            </div>
          )}

          {/* Running banner */}
          {runningCount > 0 && (
            <div className="sl__banner sl__banner--running">
              ⚡ {runningCount === 1 ? '1 schedule in esecuzione' : `${runningCount} schedule in esecuzione`} — aggiornamento automatico ogni 3s
            </div>
          )}

          {/* Stats */}
          <div className="sl__stats">
            {[
              { label:'Totali',        val:schedules.length, sub:'configurati',      mod:'total' },
              { label:'Attivi',        val:enabledCount,     sub:'abilitati',        mod:'active' },
              { label:'In esecuzione', val:runningCount,     sub:'adesso',           mod:'running' },
              { label:'Falliti',       val:failedCount,      sub:'ultima settimana', mod:'failed' },
            ].map(s => (
              <div key={s.mod} className={`sl__stat sl__stat--${s.mod}`}>
                <div className="sl__stat-label">{s.label}</div>
                <div className="sl__stat-val">{s.val}</div>
                <div className="sl__stat-sub">{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Filter tabs */}
          <div className="sl__filter-tabs">
            {Object.entries(filterConfigs).map(([key, cfg]) => (
              <button key={key} type="button"
                className={`sl__ftab${activeFilter === key ? ' sl__ftab--active' : ''}${cfg.count === 0 ? ' sl__ftab--empty' : ''}`}
                onClick={() => setActiveFilter(key)}>
                <span>{cfg.icon}</span> {cfg.label} <span className="sl__ftab-count">{cfg.count}</span>
              </button>
            ))}
          </div>

          {/* List */}
          {filteredSchedules.length === 0 ? (
            <div className="sl__empty">
              <div className="sl__empty-icon">{activeFilter === 'all' ? '⏰' : filterConfigs[activeFilter]?.icon}</div>
              <div className="sl__empty-title">{activeFilter === 'all' ? (projectFilter ? 'Nessuno schedule per questo progetto' : 'Nessuno schedule configurato') : `Nessuno schedule ${filterConfigs[activeFilter]?.label?.toLowerCase()}`}</div>
              <div className="sl__empty-text">{activeFilter === 'all' ? 'Crea uno schedule per eseguire le pipeline automaticamente' : 'Cambia filtro o crea un nuovo schedule'}</div>
              {activeFilter === 'all'
                ? (can.createSchedule(user) && <button type="button" className="sl__btn sl__btn--primary sl__btn--mt-md" onClick={() => { setEditingId(null); setShowWizard(true); }}>＋ Crea il primo schedule</button>)
                : <button type="button" className="sl__btn sl__btn--ghost sl__btn--mt-md" onClick={() => setActiveFilter('all')}>Mostra tutti</button>
              }
            </div>
          ) : (
            <div className="sl__list">
              <div className="sl__list-count">{filteredSchedules.length} schedule{activeFilter !== 'all' ? ' '+filterConfigs[activeFilter]?.label?.toLowerCase() : ''}</div>
              {filteredSchedules.map(s => (
                <ScheduleCard
                  key={s.id}
                  schedule={s}
                  isRunning={s.runtime?.status === 'running' || s.runtime?.status === 'queued' || !!runningManual[s.id]}
                  runStartTime={runStartTimes[s.id]}
                  logs={expandedLogs[s.id]}
                  runs={expandedRuns[s.id]}
                  runningManual={runningManual[s.id]}
                  onToggle={() => handleToggle(s.id, s.schedule?.enabled)}
                  onDelete={() => handleDelete(s.id, s.schedule?.description)}
                  onRunNow={() => handleRunNow(s.id)}
                  onToggleLogs={() => handleToggleLogs(s.id)}
                  onToggleRuns={() => handleToggleRuns(s.id)}
                  onEdit={() => { setEditingId(s.id); setShowWizard(true); }}
                  onDownload={() => downloadScheduleOutput(s.id)}
                  onDownloadRun={(ts) => downloadScheduleOutput(s.id, ts)}
                  formatDate={formatDate}
                  formatCron={formatCron}
                  getElapsed={getElapsed}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Wizard */}
      {showWizard && (
        <ScheduleWizard
          onClose={() => { setShowWizard(false); setEditingId(null); }}
          onCreated={() => { setShowWizard(false); setEditingId(null); loadData(); }}
          editId={editingId}
          defaultProjectId={projectFilter}
        />
      )}
      <ConfirmDialog {...confirmModal} />
    </div>
  );
}
function ScheduleCard({ schedule, isRunning, runStartTime, logs, runs, runningManual, onToggle, onDelete, onRunNow, onToggleLogs, onToggleRuns, onEdit, onDownload, onDownloadRun, formatDate, formatCron, getElapsed }) {
  const isEnabled = schedule.schedule?.enabled;
  const isOrphan  = schedule.runtime?.status === 'orphan';
  const status    = schedule.runtime?.status || 'idle';
  const statusCfg = getStatusConfig(status);
  const typeCfg   = getProjectTypeConfig(schedule.pipeline?.project_type);
  const runtime   = schedule.runtime;
  const lastExec  = runtime?.last_execution;

  return (
    <div className={`sl__card${!isEnabled ? ' sl__card--disabled' : ''}${isOrphan ? ' sl__card--orphan' : ''}${isRunning ? ' sl__card--running' : ''}`}>
      {/* Accent bar */}
      <div className="sl__card-bar" style={{ background: statusCfg.bar }} />

      <div className="sl__card-inner">
        {/* Header */}
        <div className="sl__card-header">
          <div className="sl__card-header-left">
            <div className="sl__card-project-icon" style={{ background: typeCfg.color }}>{typeCfg.icon}</div>
            <div>
              <div className="sl__card-title">{schedule.pipeline?.project_name || 'Progetto sconosciuto'}</div>
              <div className="sl__card-desc">{schedule.schedule?.description || formatCron(schedule.schedule?.cron_expression)}</div>
            </div>
          </div>
          <div className="sl__card-header-right">
            <span className={`sl__badge ${statusCfg.cls}`}>{statusCfg.label}</span>
            <label className="sl__toggle" title={isEnabled ? 'Disabilita' : 'Abilita'}>
              <input
                type="checkbox"
                checked={!!isEnabled}
                onChange={onToggle}
                disabled={isOrphan}
                aria-label={isEnabled ? 'Disabilita schedule' : 'Abilita schedule'}
              />
              <span className="sl__toggle-slider" />
            </label>
          </div>
        </div>

        {/* Progress bar for running */}
        {isRunning && <div className="sl__progress"><div className="sl__progress-fill" /></div>}

        {/* Status bar */}
        <StatusBar schedule={schedule} isRunning={isRunning} runStartTime={runStartTime} getElapsed={getElapsed} />

        {/* Info grid */}
        <div className="sl__info-grid">
          <div><div className="sl__info-label">Cron</div><div className="sl__info-val sl__info-val--mono">{schedule.schedule?.cron_expression}</div></div>
          <div><div className="sl__info-label">Frequenza</div><div className="sl__info-val">{formatCron(schedule.schedule?.cron_expression)}</div></div>
          <div><div className="sl__info-label">Prossima esecuzione</div><div className="sl__info-val">{isEnabled ? formatDate(runtime?.next_execution) : '—'}</div></div>
          <div><div className="sl__info-label">Ultima esecuzione</div><div className="sl__info-val">{formatDate(lastExec?.timestamp)}</div></div>
          <div>
            <div className="sl__info-label">Esecuzioni totali</div>
            <div className="sl__info-val">
              {runtime?.total_runs || 0}
              {runtime?.failure_count > 0 && <span className="sl__fail-count"> ({runtime.failure_count} fallite)</span>}
            </div>
          </div>
          <div><div className="sl__info-label">Destinazione</div><div className="sl__info-val">{schedule.destination ? `${schedule.destination.table_name} (${schedule.destination.write_mode})` : '— Solo file'}</div></div>
        </div>

        {/* Actions */}
        <div className="sl__card-actions">
          <div className="sl__card-actions-left">
            <button type="button" className={`sl__btn sl__btn--sm ${isRunning || runningManual ? 'sl__btn--running' : 'sl__btn--ghost'}`}
              onClick={onRunNow} disabled={isOrphan || isRunning || runningManual}>
              {isRunning || runningManual ? '⏳ In corso...' : '▶ Esegui Ora'}
            </button>
            <button type="button" className={`sl__btn sl__btn--sm sl__btn--ghost${logs ? ' sl__btn--active' : ''}`} onClick={onToggleLogs}>
              📋 {logs ? 'Chiudi Log' : 'Log'}
            </button>
            {lastExec && (
              <button type="button" className={`sl__btn sl__btn--sm sl__btn--ghost${runs ? ' sl__btn--active' : ''}`} onClick={onToggleRuns}>
                📥 {runs ? 'Chiudi' : 'Download'}
              </button>
            )}
          </div>
          <div className="sl__card-actions-right">
            <button type="button" className="sl__btn sl__btn--sm sl__btn--ghost" onClick={onEdit} aria-label="Modifica schedule">✏️</button>
            <button type="button" className="sl__btn sl__btn--sm sl__btn--danger" onClick={onDelete} aria-label="Elimina schedule">🗑</button>
          </div>
        </div>

        {/* Logs panel */}
        {logs && (
          <div className="sl__logs">
            <div className="sl__logs-title">Ultime 5 esecuzioni</div>
            {logs.length === 0 ? (
              <div className="sl__logs-empty">Nessun log disponibile</div>
            ) : (
              <div className="sl__logs-list">
                {logs.map((log, i) => (
                  <div
                    key={log.id || `${schedule.id}-log-${log.timestamp || ''}-${i}`}
                    className="sl__log-entry"
                  >
                    <span className="sl__log-icon">{getLogIcon(log.event)}</span>
                    <span className={`sl__log-event${log.event?.includes('FAILED') ? ' sl__log-event--failed' : ''}`}>{log.event}</span>
                    <span className="sl__log-date">{formatDate(log.timestamp)}</span>
                    {log.total_duration_ms && (
                      <span className={`sl__log-dur${log.event?.includes('FAILED') ? ' sl__log-dur--failed' : ''}`}>{(log.total_duration_ms/1000).toFixed(1)}s</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Runs panel */}
        {runs && (
          <div className="sl__runs">
            <div className="sl__runs-header">
              <span className="sl__runs-title">📂 Storico esecuzioni</span>
              <button type="button" className="sl__btn sl__btn--sm sl__btn--ghost" onClick={onDownload}>⬇ Scarica ultimo</button>
            </div>
            {runs.length === 0 ? (
              <div className="sl__runs-empty">Nessun run archiviato. Esegui lo schedule per generare output.</div>
            ) : (
              <div className="sl__runs-list">
                {runs.map((run, i) => (
                  <div
                    key={run.id || `${schedule.id}-run-${run.timestamp || ''}-${i}`}
                    className="sl__run-entry"
                  >
                    <span className="sl__run-ts">{formatRunTimestamp(run.timestamp)}</span>
                    <span className="sl__run-files">{run.files.map(f => f.name).join(', ')}</span>
                    <span className="sl__run-size">{formatSize(run.files.reduce((s,f) => s+f.size_bytes, 0))}</span>
                    <button type="button" className="sl__btn sl__btn--sm sl__btn--ghost" onClick={() => onDownloadRun(run.timestamp)} aria-label="Scarica output di questa esecuzione">📥</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
function StatusBar({ schedule, isRunning, runStartTime, getElapsed }) {
  const runtime  = schedule.runtime;
  const status   = runtime?.status;
  const lastExec = runtime?.last_execution;
  const maxRetries       = schedule.on_error?.max_retries || 0;
  const consecutiveFails = runtime?.consecutive_failures || 0;

  if (status === 'running' || isRunning) {
    const elapsed = runStartTime ? getElapsed(runStartTime) : null;
    return (
      <div className="sl__statusbar sl__statusbar--running">
        <div className="sl__statusbar-left">
          <span className="sl__statusbar-dot sl__statusbar-dot--running" />
          Pipeline in esecuzione...
          {elapsed && <span className="sl__statusbar-elapsed">({elapsed})</span>}
        </div>
        {runtime?.current_task_id && <span className="sl__statusbar-task">Task: {runtime.current_task_id.slice(0,8)}…</span>}
      </div>
    );
  }
  if (status === 'queued') return (
    <div className="sl__statusbar sl__statusbar--queued">
      <span className="sl__statusbar-dot sl__statusbar-dot--queued" /> In coda — un'altra esecuzione è in corso
    </div>
  );
  if (status === 'failed') {
    const errorMsg = lastExec?.error || lastExec?.event || 'Errore sconosciuto';
    const isRetrying = consecutiveFails > 0 && consecutiveFails < maxRetries;
    return (
      <div className="sl__statusbar sl__statusbar--failed">
        <div className="sl__statusbar-left">
          <span className="sl__statusbar-dot sl__statusbar-dot--failed" />
          <span className="sl__statusbar-errmsg">{errorMsg}</span>
        </div>
        <div className="sl__statusbar-right">
          {isRetrying && <span className="sl__statusbar-retry">🔁 Retry {consecutiveFails}/{maxRetries}</span>}
          {consecutiveFails >= maxRetries && maxRetries > 0 && <span className="sl__statusbar-retry sl__statusbar-retry--exhausted">❌ {maxRetries} retry esauriti</span>}
          {lastExec?.timestamp && <span className="sl__statusbar-ago">{getElapsed(lastExec.timestamp)} fa</span>}
        </div>
      </div>
    );
  }
  if (status === 'completed' || status === 'partial') {
    const duration = lastExec?.total_duration_ms ? (lastExec.total_duration_ms/1000).toFixed(1)+'s' : null;
    return (
      <div className={`sl__statusbar sl__statusbar--${status}`}>
        <div className="sl__statusbar-left">
          <span className={`sl__statusbar-dot sl__statusbar-dot--${status}`} />
          {status === 'completed' ? 'Ultima esecuzione completata' : 'Completato con avvisi'}
          {duration && <span className="sl__statusbar-elapsed">({duration})</span>}
        </div>
        {lastExec?.timestamp && <span className="sl__statusbar-ago">{getElapsed(lastExec.timestamp)} fa</span>}
      </div>
    );
  }
  return null;
}

export default ScheduleList;
