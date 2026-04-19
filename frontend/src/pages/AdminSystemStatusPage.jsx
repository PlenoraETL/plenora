/**
 * AdminSystemStatusPage.jsx — Dashboard stato sistema (superadmin)
 * Route: /admin/system-status
 *
 * Single pane of glass: backup, verify-restore, scheduler, worker Celery,
 * risorse host (CPU/RAM/disco), DB pool, Redis, schema size.
 * Ogni card legge dall'endpoint aggregato `/api/admin/system-status`.
 * Auto-refresh ogni 30s (backend aggrega in ~100ms).
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import AppSidebar from '../components/AppSidebar';
import { getSystemStatus } from '../api/adminSystemStatus';
import '../styles/AdminSystemStatusPage.css';


// ── Helpers UI ────────────────────────────────────────────────────────────
const STATUS_LABELS = { ok: 'OK', warn: 'WARN', error: 'ERRORE', unknown: 'N/D' };
const STATUS_ICONS = { ok: '✓', warn: '⚠', error: '✗', unknown: '?' };

function Badge({ status }) {
  const key = STATUS_LABELS[status] ? status : 'unknown';
  return (
    <span className={`sys__badge sys__badge--${key}`}>
      <span>{STATUS_ICONS[key]}</span>
      {STATUS_LABELS[key]}
    </span>
  );
}

function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function fmtUptime(sec) {
  if (sec == null) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  if (d > 0) return `${d}d ${h}h`;
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}


// ── Progress bar ─────────────────────────────────────────────────────────
function ProgressBar({ percent, warnAt = 80, errorAt = 92 }) {
  if (percent == null) return null;
  const cls = percent >= errorAt ? 'error' : percent >= warnAt ? 'warn' : 'ok';
  return (
    <div className="sys__progress">
      <div
        className={`sys__progress-fill sys__progress-fill--${cls}`}
        style={{ width: `${Math.min(percent, 100)}%` }}
      />
    </div>
  );
}


// ── Card generica ────────────────────────────────────────────────────────
function Card({ title, icon, data, children, linkLabel, onLink }) {
  const status = (data && data.status) || 'unknown';
  return (
    <div className={`sys__card sys__card--${status}`}>
      <div className="sys__card-head">
        <h3 className="sys__card-title">
          <span>{icon}</span>{title}
        </h3>
        <Badge status={status} />
      </div>
      {data ? (
        <>
          <div className="sys__card-message">{data.message || '—'}</div>
          <div className="sys__card-body">{children}</div>
        </>
      ) : (
        <div className="sys__card-body">Caricamento…</div>
      )}
      {linkLabel && (
        <div>
          <button className="sys__card-link" onClick={onLink}>
            {linkLabel} →
          </button>
        </div>
      )}
    </div>
  );
}


// ── Card specializzate ───────────────────────────────────────────────────
function BackupCard({ data, onLink }) {
  return (
    <Card title="Backup DB" icon="💾" data={data}
          linkLabel="Vedi Backup DB" onLink={onLink}>
      {data && (
        <>
          {data.last_modified && <div>Ultimo: <b>{fmtDate(data.last_modified)}</b></div>}
          {data.size_bytes != null && <div>Dimensione: <b>{fmtBytes(data.size_bytes)}</b></div>}
          {data.total_backups != null && <div>Totale dump S3: <b>{data.total_backups}</b></div>}
        </>
      )}
    </Card>
  );
}

function VerifyRestoreCard({ data }) {
  return (
    <Card title="Verifica Restore" icon="🔄" data={data}>
      {data && (
        <>
          {data.last_run_at && <div>Ultima: <b>{fmtDate(data.last_run_at)}</b></div>}
          {data.age_days != null && <div>Età: <b>{data.age_days} gg</b></div>}
          {data.exit_code != null && data.exit_code !== 0 && (
            <div>Exit code: <b>{data.exit_code}</b></div>
          )}
          <div className="sys__hint">Timer: domenica 03:00 UTC</div>
        </>
      )}
    </Card>
  );
}

function SchedulesCard({ data, onLink }) {
  return (
    <Card title="Scheduler" icon="⏰" data={data}
          linkLabel="Vedi Scheduler" onLink={onLink}>
      {data && (
        <>
          {data.total_active != null && <div>Attivi: <b>{data.total_active}</b></div>}
          {data.failing_count > 0 && (
            <div style={{ color: '#F87171' }}>Failing: <b>{data.failing_count}</b></div>
          )}
          {data.failing && data.failing.length > 0 && (
            <ul className="sys__list" style={{ marginTop: 6 }}>
              {data.failing.slice(0, 3).map((s) => (
                <li key={s.id} className="sys__list-item">{s.name || s.id}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

function WorkersCard({ data }) {
  return (
    <Card title="Worker Celery" icon="👷" data={data}>
      {data && (
        <>
          {data.queue_depth != null && data.queue_depth > 0 && (
            <div style={{ color: '#FBBF24', fontWeight: 600 }}>
              ⏳ {data.queue_depth} in coda
            </div>
          )}
          {data.details && data.details.length > 0 ? (
            <ul className="sys__list" style={{ marginTop: 4 }}>
              {data.details.map((n) => (
                <li key={n.name} className="sys__list-item">
                  <div><b>🖥️ {n.hostname}</b>
                    {n.pid != null && (
                      <span style={{ marginLeft: 6, color: '#7D8BA8', fontSize: 11 }}>
                        PID {n.pid}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 11, color: '#7D8BA8', marginTop: 2 }}>
                    Pool {n.pool_size ?? '?'} · attivi {n.active_tasks ?? 0}
                    {n.total_tasks_processed != null && <> · lifetime {n.total_tasks_processed}</>}
                  </div>
                  {n.queues && n.queues.length > 0 && (
                    <div style={{ fontSize: 11, color: '#7D8BA8' }}>
                      Code: {n.queues.join(', ')}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <>
              {data.nodes != null && <div>Nodi: <b>{data.nodes}</b></div>}
              {data.concurrency_slots != null && <div>Slot: <b>{data.concurrency_slots}</b></div>}
              {data.active_tasks != null && <div>Task attivi: <b>{data.active_tasks}</b></div>}
            </>
          )}
          <div className="sys__hint">
            Nodi = processi master · Pool = slot paralleli per nodo
          </div>
        </>
      )}
    </Card>
  );
}

function HostResourcesCard({ data }) {
  return (
    <Card title="Risorse Host" icon="🖥️" data={data}>
      {data && (
        <>
          {data.disk && (
            <div style={{ marginBottom: 8 }}>
              <div>Disco <b>{data.disk.percent}%</b> · {data.disk.free_gb} GB liberi / {data.disk.total_gb} GB</div>
              <ProgressBar percent={data.disk.percent} warnAt={80} errorAt={92} />
            </div>
          )}
          {data.memory && (
            <div style={{ marginBottom: 8 }}>
              <div>RAM <b>{data.memory.percent}%</b> · {data.memory.available_gb} GB liberi / {data.memory.total_gb} GB</div>
              <ProgressBar percent={data.memory.percent} warnAt={85} errorAt={95} />
            </div>
          )}
          {data.cpu && (
            <div>
              <div>CPU <b>{data.cpu.percent != null ? `${data.cpu.percent}%` : '—'}</b> · {data.cpu.count} core</div>
              {data.cpu.load_1m != null && (
                <div className="sys__hint">
                  Load avg: {data.cpu.load_1m} / {data.cpu.load_5m} / {data.cpu.load_15m}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function DbPoolCard({ data }) {
  return (
    <Card title="DB Connection Pool" icon="🗄️" data={data}>
      {data && data.total != null && (
        <>
          <div><b>{data.total}</b>/{data.max_connections} conn ({data.percent}%)</div>
          <ProgressBar percent={data.percent} warnAt={80} errorAt={95} />
          <div style={{ marginTop: 6 }}>
            Attive: <b>{data.active}</b> · Idle: <b>{data.idle}</b>
            {data.idle_in_transaction > 0 && (
              <span style={{ color: '#FBBF24' }}> · Idle-TX: {data.idle_in_transaction}</span>
            )}
            {data.waiting > 0 && (
              <span style={{ color: '#F87171' }}> · Waiting: {data.waiting}</span>
            )}
          </div>
        </>
      )}
    </Card>
  );
}

function RedisCard({ data }) {
  return (
    <Card title="Redis" icon="🔴" data={data}>
      {data && data.used_memory_human && (
        <>
          <div>
            Memoria: <b>{data.used_memory_human}</b>
            {data.percent != null && ` (${data.percent}%)`}
          </div>
          {data.percent != null && (
            <ProgressBar percent={data.percent} warnAt={75} errorAt={90} />
          )}
          <div style={{ marginTop: 6 }}>
            Client: <b>{data.connected_clients}</b>
            {data.uptime_seconds != null && <> · Uptime: <b>{fmtUptime(data.uptime_seconds)}</b></>}
          </div>
        </>
      )}
    </Card>
  );
}

function TicketsCard({ data, onLink }) {
  const sev = data?.by_severity || {};
  return (
    <Card title="Ticket" icon="🎫" data={data} onLink={onLink} linkLabel="Vai ai ticket">
      {data && data.total_active != null && (
        <>
          <div>Attivi: <b>{data.total_active}</b></div>
          <ul className="sys__list" style={{ marginTop: 6 }}>
            {['critical', 'high', 'medium', 'low'].map((s) => (
              (sev[s] || 0) > 0 && (
                <li key={s} className="sys__kv">
                  <span className="sys__kv-key" style={{ textTransform: 'capitalize' }}>{s}</span>
                  <span className="sys__kv-val"><b>{sev[s]}</b></span>
                </li>
              )
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function JwtBlacklistCard({ data }) {
  const c = data?.counters || {};
  const mins = data?.minutes_since_last_ok;
  const lastOk =
    mins == null
      ? 'mai'
      : mins < 1
        ? 'pochi secondi fa'
        : `${mins} min fa`;
  return (
    <Card title="JWT Blacklist" icon="🔑" data={data}>
      {data && (
        <>
          <div>Ultimo OK: <b>{lastOk}</b></div>
          {data.status === 'warn' && (
            <div style={{ marginTop: 6, color: 'var(--sys-warn, #b45309)' }}>
              ⚠️ Operazioni inoperanti da troppo tempo
            </div>
          )}
          <ul className="sys__list" style={{ marginTop: 6 }}>
            <li className="sys__kv">
              <span className="sys__kv-key">Write OK / Fail</span>
              <span className="sys__kv-val">
                {(c.write_ok || 0).toLocaleString('it-IT')}
                {' / '}
                {(c.write_fail || 0).toLocaleString('it-IT')}
              </span>
            </li>
            <li className="sys__kv">
              <span className="sys__kv-key">Read OK / Fail</span>
              <span className="sys__kv-val">
                {(c.read_ok || 0).toLocaleString('it-IT')}
                {' / '}
                {(c.read_fail || 0).toLocaleString('it-IT')}
              </span>
            </li>
            {c.client_missing > 0 && (
              <li className="sys__kv">
                <span className="sys__kv-key">Client missing</span>
                <span className="sys__kv-val">
                  {c.client_missing.toLocaleString('it-IT')}
                </span>
              </li>
            )}
          </ul>
        </>
      )}
    </Card>
  );
}

function DbSchemaCard({ data }) {
  return (
    <Card title="DB Schema" icon="📊" data={data}>
      {data && (
        <>
          <div>Size totale: <b>{data.size_pretty}</b></div>
          {data.tables && data.tables.length > 0 && (
            <ul className="sys__list" style={{ marginTop: 6 }}>
              {data.tables.slice(0, 5).map((t) => (
                <li key={t.name} className="sys__kv">
                  <span className="sys__kv-key">{t.name}</span>
                  <span className="sys__kv-val">
                    {t.rows.toLocaleString('it-IT')} · {t.size_pretty}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}


// ── Pagina principale ────────────────────────────────────────────────────
const REFRESH_INTERVAL_MS = 30000;

const STATUS_KEYS = [
  'backup', 'verify_restore', 'schedules', 'workers',
  'host_resources', 'db_pool', 'redis', 'db_schema', 'jwt_blacklist',
  'tickets',
];

export default function AdminSystemStatusPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [lastError, setLastError] = useState(null);
  const [refreshedAt, setRefreshedAt] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getSystemStatus();
      setStatus(res);
      setRefreshedAt(new Date());
      setLastError(null);
    } catch (e) {
      setLastError(e.response?.data?.detail || e.message || 'Errore caricamento');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  const overall = useMemo(() => {
    if (!status) return null;
    const vals = STATUS_KEYS.map((k) => status[k]?.status).filter(Boolean);
    if (vals.includes('error')) return 'error';
    if (vals.includes('warn')) return 'warn';
    if (vals.every((v) => v === 'ok')) return 'ok';
    return 'unknown';
  }, [status]);

  return (
    <div className="sys">
      <AppSidebar />
      <main className="sys__main">
        <header className="sys__header">
          <div>
            <h1 className="sys__title">
              Stato Sistema
              {overall && <Badge status={overall} />}
            </h1>
            <div className="sys__subtitle">
              {refreshedAt
                ? `Ultimo aggiornamento: ${refreshedAt.toLocaleTimeString('it-IT')}`
                : 'Caricamento…'}
              {loading && <span style={{ marginLeft: 8 }}>↻</span>}
            </div>
          </div>
          <div className="sys__header-actions">
            <button
              onClick={load}
              disabled={loading}
              className="sys__btn sys__btn--primary"
            >
              {loading ? 'Aggiornamento…' : '↻ Aggiorna ora'}
            </button>
          </div>
        </header>

        {lastError && (
          <div className="sys__error-box">
            Errore caricamento: {lastError}
          </div>
        )}

        <section className="sys__cards">
          <BackupCard
            data={status?.backup}
            onLink={() => navigate('/admin/backups')}
          />
          <VerifyRestoreCard data={status?.verify_restore} />
          <SchedulesCard
            data={status?.schedules}
            onLink={() => navigate('/schedules')}
          />
          <WorkersCard data={status?.workers} />
          <HostResourcesCard data={status?.host_resources} />
          <DbPoolCard data={status?.db_pool} />
          <RedisCard data={status?.redis} />
          <DbSchemaCard data={status?.db_schema} />
          <JwtBlacklistCard data={status?.jwt_blacklist} />
          <TicketsCard
            data={status?.tickets}
            onLink={() => navigate('/tickets')}
          />
        </section>

        <footer className="sys__footer">
          Auto-refresh ogni {REFRESH_INTERVAL_MS / 1000}s. Le metriche con
          stato <i>N/D</i> indicano servizi non interrogabili (non
          necessariamente fallimenti).
        </footer>
      </main>
    </div>
  );
}
