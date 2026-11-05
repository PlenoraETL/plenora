/**
 * ScheduleList.jsx — Gestione schedulazioni
 * Redesign Plenora: sidebar layout, hex bg, card con accent bar
 * Route: /schedules
 */
import React, { useState, useEffect, useRef } from 'react';
import { useResource } from '../hooks/useResource';
import { useDownload } from '../hooks/useDownload';
import { useNow } from '../hooks/useNow';
import { ResourceError } from '../components/common/ListState';
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
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import { can } from '../utils/permissions';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { toastApiError } from '../utils/toastApiError';
import '../styles/ScheduleList.css';
import { requestErrorMessage, backendMessage } from '../utils/errorMessages';
import { formatDateTime as formatDate, formatBytes as formatSize, formatElapsed as getElapsed } from '../utils/format';
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
// Tempo trascorso che si aggiorna da solo: solo questa etichetta si
// ridisegna ogni secondo, non l'intera lista (Fase 5 T2, R4.16).
function Elapsed({ since }) {
  const now = useNow(1000, Boolean(since));
  return <>{getElapsed(since, now)}</>;
}

// Aggiornamento della lista: ogni 3 s con un'esecuzione in corso o in coda,
// altrimenti ogni 30 s (cosi' si vedono anche le esecuzioni partite dal
// pianificatore); mai con la scheda nascosta (hooks/useResource).
const FAST_REFRESH_MS = 3000;
const SLOW_REFRESH_MS = 30000;

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

  // Fase 5 T2 (R4.16, R9.14): lista e salute del pianificatore dall'hook
  // comune (id di richiesta, filtro nella chiave, errore con «Riprova»,
  // spinner a pagina intera solo al primo caricamento).
  const [runningManual,  setRunningManual]  = useState({}); // id -> ora del clic su "Esegui ora"
  const [anyActive, setAnyActive] = useState(false);
  const resource = useResource(async () => (await listSchedules(projectFilter)).schedules || [], {
    key: ['schedules', projectFilter || null],
    refreshInterval: anyActive ? FAST_REFRESH_MS : SLOW_REFRESH_MS,
  });
  const schedules = resource.data || [];
  const setSchedules = resource.setData;
  const loading = resource.initialLoading;
  const health = useResource(() => getBeatHealthcheck(), { key: 'beat-health' });
  const beatHealth = health.status === 'error'
    ? { status: 'unknown', message: 'Stato del pianificatore non raggiungibile' }
    : (health.data || null);
  const [expandedLogs,   setExpandedLogs]   = useState({});
  const [expandedRuns,   setExpandedRuns]   = useState({});
  const [activeFilter,   setActiveFilter]   = useState('all');
  const [showWizard,     setShowWizard]     = useState(false);
  const [editingId,      setEditingId]      = useState(null);
  const downloads = useDownload({ onError: (message) => toast.error(`Download non riuscito: ${message}`) });

  const activeIds = schedules
    .filter(s => s.runtime?.status === 'running' || s.runtime?.status === 'queued')
    .map(s => s.id);
  const hasActive = activeIds.length > 0 || Object.keys(runningManual).length > 0;
  useEffect(() => { setAnyActive(hasActive); }, [hasActive]);
  // Un avvio manuale si dimentica quando un aggiornamento arrivato DOPO il
  // clic mostra lo schedule non piu' in corso.
  const listData = resource.data;
  useEffect(() => {
    if (!listData) return;
    setRunningManual(prev => {
      const stale = Object.keys(prev).filter(id => {
        const st = listData.find(item => item.id === id)?.runtime?.status;
        return st !== 'running' && st !== 'queued' && Date.now() - prev[id] > FAST_REFRESH_MS;
      });
      if (!stale.length) return prev;
      const n = { ...prev };
      stale.forEach(id => delete n[id]);
      return n;
    });
  }, [listData]);

  const loadData = () => { resource.reload(); health.reload(); };
  const refreshData = () => resource.reload();

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
    setSchedules(prev => (prev || []).map(s => (s.id === id ? withEnabled(s, next) : s)));
    try {
      await toggleSchedule(id, next);
      loadData();
    } catch (e) {
      // Rollback: ripristina lo state precedente prima di mostrare l'errore.
      setSchedules(prev => (prev || []).map(s => (s.id === id ? withEnabled(s, current) : s)));
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
        const detail = backendMessage(e?.response?.data) || 'esecuzione in corso o dipendenza attiva';
        toast.error(`Impossibile eliminare: ${detail}`);
        return;
      }
      const msg = requestErrorMessage(e);
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
      setRunningManual(prev => ({ ...prev, [id]: Date.now() }));
      setSchedules(prev => (prev || []).map(s =>
        s.id === id ? { ...s, runtime: { ...(s.runtime || {}), status: 'queued' } } : s
      ));
      await runScheduleNow(id);
      await refreshData();
    } catch (e) {
      // Rollback ottimistico: ripristina lo status precedente.
      setSchedules(prev => (prev || []).map(s =>
        s.id === id ? { ...s, runtime: { ...(s.runtime || {}), status: prevStatus } } : s
      ));
      toastApiError(toast, e, 'Errore');
      setRunningManual(prev => { const n={...prev}; delete n[id]; return n; });
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
    <PageLayout className="sl" title="Scheduler">
      <div className="sl__loading-inner"><div className="sl__loading-icon">⬡</div>Caricamento schedule...</div>
    </PageLayout>
  );

  return (
    <PageLayout
      className="sl"
      bare
      title={projectFilter ? 'Scheduler (filtrato)' : 'Scheduler'}
      actions={(
        <>
          {projectFilter && <button type="button" className="sl__btn sl__btn--ghost" onClick={() => navigate('/schedules')}>Mostra tutti</button>}
          {can.createSchedule(user) && (
            <button type="button" className="sl__btn sl__btn--primary" onClick={() => { setEditingId(null); setShowWizard(true); }}>＋ Nuovo Schedule</button>
          )}
        </>
      )}
    >

        <div className="sl__content">
          <canvas ref={canvasRef} className="sl__hex-bg" />

          {resource.status === 'error' && (
            <ResourceError resource={resource} title="Schedule non caricati" />
          )}

          {/* Beat health banner */}
          {beatHealth && beatHealth.status !== 'ok' && (
            <div className={`sl__banner sl__banner--${beatHealth.status === 'down' ? 'error' : 'warning'}`}>
              {beatHealth.status === 'down' ? '🔴' : '🟡'} {beatHealth.status === 'down' ? 'Beat scheduler non attivo — gli schedule non verranno eseguiti automaticamente.' : beatHealth.message}
            </div>
          )}

          {/* Running banner */}
          {runningCount > 0 && (
            <div className="sl__banner sl__banner--running">
              ⚡ {runningCount === 1 ? '1 schedule in esecuzione o in coda' : `${runningCount} schedule in esecuzione o in coda`} — aggiornamento automatico ogni 3s
            </div>
          )}

          {/* Stats e filtri solo con dati (G2, S4): a zero c'e' lo stato vuoto. */}
          {schedules.length > 0 && (<>
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
          </>)}

          {/* List */}
          {resource.status === 'error' && !resource.hasData ? null : filteredSchedules.length === 0 ? (
            <EmptyState
              icon={activeFilter === 'all' ? '⏰' : filterConfigs[activeFilter]?.icon}
              title={activeFilter === 'all' ? (projectFilter ? 'Nessuno schedule per questo progetto' : 'Nessuno schedule configurato') : `Nessuno schedule ${filterConfigs[activeFilter]?.label?.toLowerCase()}`}
              text={activeFilter === 'all' ? 'Crea uno schedule per eseguire le pipeline automaticamente' : 'Cambia filtro o crea un nuovo schedule'}
              action={activeFilter === 'all'
                ? (can.createSchedule(user) && <button type="button" className="sl__btn sl__btn--primary" onClick={() => { setEditingId(null); setShowWizard(true); }}>＋ Crea il primo schedule</button>)
                : <button type="button" className="sl__btn sl__btn--ghost" onClick={() => setActiveFilter('all')}>Mostra tutti</button>}
            />
          ) : (
            <div className="sl__list">
              <div className="sl__list-count">{filteredSchedules.length} schedule{activeFilter !== 'all' ? ' '+filterConfigs[activeFilter]?.label?.toLowerCase() : ''}</div>
              {filteredSchedules.map(s => (
                <ScheduleCard
                  key={s.id}
                  schedule={s}
                  canManage={can.manageSchedule(user, s)}
                  isRunning={s.runtime?.status === 'running' || (!!runningManual[s.id] && s.runtime?.status !== 'queued')}
                  runStartTime={runningManual[s.id] || null}
                  logs={expandedLogs[s.id]}
                  runs={expandedRuns[s.id]}
                  runningManual={!!runningManual[s.id]}
                  onToggle={() => handleToggle(s.id, s.schedule?.enabled)}
                  onDelete={() => handleDelete(s.id, s.schedule?.description)}
                  onRunNow={() => handleRunNow(s.id)}
                  onToggleLogs={() => handleToggleLogs(s.id)}
                  onToggleRuns={() => handleToggleRuns(s.id)}
                  onEdit={() => { setEditingId(s.id); setShowWizard(true); }}
                  onDownload={() => downloads.run(`${s.id}:last`, () => downloadScheduleOutput(s.id))}
                  onDownloadRun={(ts) => downloads.run(`${s.id}:${ts}`, () => downloadScheduleOutput(s.id, ts))}
                  formatDate={formatDate}
                  formatCron={formatCron}
                  getElapsed={getElapsed}
                />
              ))}
            </div>
          )}
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
    </PageLayout>
  );
}
// `canManage` (can.manageSchedule): esegui, attiva, modifica ed elimina si
// mostrano solo a chi puo' farli (audit R10.1); log e download restano.
function ScheduleCard({ schedule, canManage, isRunning, runStartTime, logs, runs, runningManual, onToggle, onDelete, onRunNow, onToggleLogs, onToggleRuns, onEdit, onDownload, onDownloadRun, formatDate, formatCron, getElapsed }) {
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
            {canManage && (
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
            )}
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
            {canManage && (
              <button type="button" className={`sl__btn sl__btn--sm ${isRunning || runningManual ? 'sl__btn--running' : 'sl__btn--ghost'}`}
                onClick={onRunNow} disabled={isOrphan || isRunning || runningManual}>
                {isRunning || runningManual ? '⏳ In corso...' : '▶ Esegui Ora'}
              </button>
            )}
            <button type="button" className={`sl__btn sl__btn--sm sl__btn--ghost${logs ? ' sl__btn--active' : ''}`} onClick={onToggleLogs}>
              📋 {logs ? 'Chiudi Log' : 'Log'}
            </button>
            {lastExec && (
              <button type="button" className={`sl__btn sl__btn--sm sl__btn--ghost${runs ? ' sl__btn--active' : ''}`} onClick={onToggleRuns}>
                📥 {runs ? 'Chiudi' : 'Download'}
              </button>
            )}
          </div>
          {canManage && (
            <div className="sl__card-actions-right">
              <button type="button" className="sl__btn sl__btn--sm sl__btn--ghost" onClick={onEdit} aria-label="Modifica schedule">✏️</button>
              <button type="button" className="sl__btn sl__btn--sm sl__btn--danger" onClick={onDelete} aria-label="Elimina schedule">🗑</button>
            </div>
          )}
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
                    <span className="sl__run-ts">{formatDate(run.timestamp)}</span>
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

  // In coda prima di "in esecuzione": una run in coda non e' partita.
  if (status === 'queued') return (
    <div className="sl__statusbar sl__statusbar--queued">
      <span className="sl__statusbar-dot sl__statusbar-dot--queued" /> In coda — un'altra esecuzione è in corso
    </div>
  );
  if (status === 'running' || isRunning) {
    // Tempo trascorso solo se si sa quando e' partita (avvio da questa
    // pagina): il server non lo espone, e "da quando la pagina la vede"
    // sarebbe un tempo falso.
    return (
      <div className="sl__statusbar sl__statusbar--running">
        <div className="sl__statusbar-left">
          <span className="sl__statusbar-dot sl__statusbar-dot--running" />
          Pipeline in esecuzione...
          {runStartTime && <span className="sl__statusbar-elapsed">(<Elapsed since={runStartTime} />)</span>}
        </div>
        {runtime?.current_task_id && <span className="sl__statusbar-task">Task: {runtime.current_task_id.slice(0,8)}…</span>}
      </div>
    );
  }
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
