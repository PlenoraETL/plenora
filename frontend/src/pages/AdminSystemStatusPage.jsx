/**
 * AdminSystemStatusPage.jsx — Dashboard stato sistema (superadmin)
 * Route: /admin/system-status
 *
 * Single pane of glass: backup, verify-restore, scheduler, worker
 * (task in esecuzione, derivati da PostgreSQL), risorse host
 * (CPU/RAM/disco), DB pool, schema size.
 * Ogni card legge dall'endpoint aggregato `/api/admin/system-status`.
 * Auto-refresh ogni 30s (backend aggrega in ~100ms).
 */
import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import PageLayout from '../components/common/PageLayout';
import { getSystemStatus } from '../api/adminSystemStatus';
import '../styles/AdminSystemStatusPage.css';
import { useResource } from '../hooks/useResource';
import { ResourceError } from '../components/common/ListState';
import { formatDateTime as fmtDate, formatBytes as fmtBytes, formatNumber, formatTime } from '../utils/format';
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
            <div className="sys__txt-error">Failing: <b>{data.failing_count}</b></div>
          )}
          {data.failing && data.failing.length > 0 && (
            <ul className="sys__list sys__mt-sm">
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
  // Plenora 2.0: il backend deriva lo stato worker da PostgreSQL
  // (task_results in PROGRESS) — niente piu' inspect per-nodo
  // (hostname/PID/pool/queue_depth erano celery-only).
  return (
    <Card title="Worker" icon="👷" data={data}>
      {data && (
        <>
          {data.active_tasks != null && (
            <div>Task in esecuzione: <b>{data.active_tasks}</b></div>
          )}
          <div className="sys__hint">
            Conteggio derivato dallo stato task su PostgreSQL
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
            <div className="sys__mb-sm">
              <div>Disco <b>{data.disk.percent}%</b> · {fmtBytes(data.disk.free_gb, { from: 'GB' })} liberi / {fmtBytes(data.disk.total_gb, { from: 'GB' })}</div>
              <ProgressBar percent={data.disk.percent} warnAt={80} errorAt={92} />
            </div>
          )}
          {data.memory && (
            <div className="sys__mb-sm">
              <div>RAM <b>{data.memory.percent}%</b> · {fmtBytes(data.memory.available_gb, { from: 'GB' })} liberi / {fmtBytes(data.memory.total_gb, { from: 'GB' })}</div>
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
          <div className="sys__mt-sm">
            Attive: <b>{data.active}</b> · Idle: <b>{data.idle}</b>
            {data.idle_in_transaction > 0 && (
              <span className="sys__txt-warn"> · Idle-TX: {data.idle_in_transaction}</span>
            )}
            {data.waiting > 0 && (
              <span className="sys__txt-error"> · Waiting: {data.waiting}</span>
            )}
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
          <ul className="sys__list sys__mt-sm">
            {['critical', 'high', 'medium', 'low'].map((s) => (
              (sev[s] || 0) > 0 && (
                <li key={s} className="sys__kv">
                  <span className="sys__kv-key sys__kv-key--cap">{s}</span>
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
            <div className="sys__mt-sm" style={{ color: 'var(--plen-warn)' }}>
              ⚠️ Operazioni inoperanti da troppo tempo
            </div>
          )}
          <ul className="sys__list sys__mt-sm">
            <li className="sys__kv">
              <span className="sys__kv-key">Write OK / Fail</span>
              <span className="sys__kv-val">
                {formatNumber(c.write_ok || 0)}
                {' / '}
                {formatNumber(c.write_fail || 0)}
              </span>
            </li>
            <li className="sys__kv">
              <span className="sys__kv-key">Read OK / Fail</span>
              <span className="sys__kv-val">
                {formatNumber(c.read_ok || 0)}
                {' / '}
                {formatNumber(c.read_fail || 0)}
              </span>
            </li>
            {c.client_missing > 0 && (
              <li className="sys__kv">
                <span className="sys__kv-key">Client missing</span>
                <span className="sys__kv-val">
                  {formatNumber(c.client_missing)}
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
          {(data.warnings || []).map((warning) => (
            <div key={warning} className="sys__mt-sm" role="alert">{warning}</div>
          ))}
          {data.tables && data.tables.length > 0 && (
            <ul className="sys__list sys__mt-sm">
              {data.tables.slice(0, 5).map((t) => (
                <li key={t.name} className="sys__kv">
                  <span className="sys__kv-key">{t.name}</span>
                  <span className="sys__kv-val">
                    {formatNumber(t.rows)} · {t.size_pretty}
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
const REFRESH_INTERVAL_MS = 30000;

const STATUS_KEYS = [
  'backup', 'verify_restore', 'schedules', 'workers',
  'host_resources', 'db_pool', 'db_schema', 'jwt_blacklist',
  'tickets',
];

export default function AdminSystemStatusPage() {
  const navigate = useNavigate();
  // Fase 5 T2 (R4.14): hook comune con aggiornamento periodico (un poll
  // alla volta, fermo con la scheda nascosta); "Aggiorna ora" fa vincere
  // l'ultima richiesta.
  const resource = useResource(async () => ({ status: await getSystemStatus(), at: new Date() }), {
    key: 'system-status',
    refreshInterval: REFRESH_INTERVAL_MS,
  });
  const status = resource.data?.status || null;
  const refreshedAt = resource.data?.at || null;
  const loading = resource.loading;
  const load = resource.reload;

  const overall = useMemo(() => {
    if (!status) return null;
    // P19 (Fase 5 T7): un componente senza stato non si ignora: il
    // riepilogo e' «ok» solo se TUTTI i componenti attesi sono ok.
    const vals = STATUS_KEYS.map((k) => status[k]?.status || 'unknown');
    if (vals.includes('error')) return 'error';
    if (vals.includes('warn')) return 'warn';
    if (vals.length > 0 && vals.every((v) => v === 'ok')) return 'ok';
    return 'unknown';
  }, [status]);

  return (
    <PageLayout
      className="sys"
      title="Stato Sistema"
      badge={overall && <Badge status={overall} />}
      subtitle={(
        <>
          {refreshedAt
            ? `Ultimo aggiornamento: ${formatTime(refreshedAt, { seconds: true })}`
            : 'Caricamento…'}
          {loading && <span className="sys__ml-xs">↻</span>}
        </>
      )}
      actions={(
        <button
          type="button"
          onClick={() => load()}
          disabled={loading}
          className="sys__btn sys__btn--primary"
        >
          {loading ? 'Aggiornamento…' : '↻ Aggiorna ora'}
        </button>
      )}
    >

        {/* Con le schede gia' caricate: «Dati non aggiornati» + Riprova. */}
        <ResourceError resource={resource} title="Stato del sistema non caricato" />

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
    </PageLayout>
  );
}
