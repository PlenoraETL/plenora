/**
 * AdminBackupsPage.jsx — Gestione backup DB (S3) per superadmin
 * Route: /admin/backups
 * Accessibile solo a superadmin (enforcement lato backend).
 *
 * Features:
 *  - Lista backup presenti su S3 (key, size, data, storage class)
 *  - Card stato ultimo run del systemd timer (successo/fallimento + log)
 *  - Card schedulazione prossimo run
 *  - Pulsante "Esegui backup ora" (rate-limited lato server)
 *
 * Il restore NON e' esposto in UI: resta procedura manuale via SSH
 * (vedi docstring di backend/scripts/backup_db.py).
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useToast } from '../hooks/useToast';
import {
  listBackups, getBackupStatus, getBackupSchedule, triggerBackup,
} from '../api/adminBackups';
import AppSidebar from '../components/AppSidebar';
import '../styles/AdminBackupsPage.css';


// ── Helpers di error handling ────────────────────────────────────────────
//
// RB.#9: il backend (RB.2 / admin_backups.router) ora ritorna 503 con
//        body strutturato `{detail: {error, message}}` quando le credenziali
//        AWS sono scadute. Passare `detail` (object) a `toast.error` causava
//        crash React: "Objects are not valid as a React child".
//        Questi helper garantiscono che messaggio e codice siano sempre
//        scalari prima di toccare il DOM o il toast.
//
// RB.#10: il codice errore consente alla UI di mostrare un banner
//         persistente con istruzioni operative ("aws sso login"), invece di
//         un toast effimero che scompare e lascia l'utente nel buio.

function extractErrorMessage(err) {
  // axios error -> response.data.detail (string | object)
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message;
    if (typeof detail.error === 'string' && detail.error.trim()) return detail.error;
  }
  if (typeof err?.message === 'string' && err.message.trim()) return err.message;
  return 'Errore generico';
}

function extractErrorCode(err) {
  const detail = err?.response?.data?.detail;
  if (detail && typeof detail === 'object' && typeof detail.error === 'string') {
    return detail.error;
  }
  return null;
}


// ── Helpers di formattazione ─────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function fmtSize(mb) {
  if (mb == null) return '—';
  if (mb < 1) return `${(mb * 1024).toFixed(0)} KB`;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function StorageBadge({ cls }) {
  const labels = {
    STANDARD:                 { txt: 'Standard',     color: '#4B8BFF' },
    STANDARD_IA:              { txt: 'Standard-IA',  color: '#9B6DFF' },
    ONEZONE_IA:               { txt: 'OneZone-IA',   color: '#9B6DFF' },
    INTELLIGENT_TIERING:      { txt: 'Intelligent',  color: '#34C17A' },
    GLACIER_IR:               { txt: 'Glacier IR',   color: '#F5A623' },
    GLACIER:                  { txt: 'Glacier',      color: '#F5A623' },
    DEEP_ARCHIVE:             { txt: 'Deep Archive', color: '#6B7280' },
  };
  const v = labels[cls] || { txt: cls, color: '#6B7280' };
  return (
    <span className="ab__storage-badge" style={{ borderColor: v.color, color: v.color }}>
      {v.txt}
    </span>
  );
}


// ── Card: stato ultimo run ───────────────────────────────────────────────
function StatusCard({ status }) {
  if (!status) return <div className="ab__card ab__card--status">Caricamento...</div>;

  const resultClasses = {
    success: 'ab__card--ok',
    failed:  'ab__card--err',
    running: 'ab__card--info',
    unknown: 'ab__card--muted',
  };
  const resultLabels = {
    success: '✓ Ultimo backup riuscito',
    failed:  '✗ Ultimo backup FALLITO',
    running: '⟳ Backup in corso...',
    unknown: '? Stato sconosciuto',
  };
  const cls = resultClasses[status.result] || 'ab__card--muted';

  return (
    <div className={`ab__card ${cls}`}>
      <div className="ab__card-title">{resultLabels[status.result] || status.result}</div>
      <div className="ab__card-body">
        <div><strong>Terminato:</strong> {status.finished_at || '—'}</div>
        <div><strong>Exit code:</strong> {status.exit_code ?? '—'}</div>
        {status.log_tail && status.log_tail.length > 0 && (
          <details className="ab__log-details">
            <summary>Log ultimo run ({status.log_tail.length} righe)</summary>
            <pre className="ab__log-tail">{status.log_tail.join('\n')}</pre>
          </details>
        )}
      </div>
    </div>
  );
}


// ── Card: schedulazione ──────────────────────────────────────────────────
function ScheduleCard({ schedule }) {
  if (!schedule) return <div className="ab__card ab__card--schedule">Caricamento...</div>;

  return (
    <div className={`ab__card ${schedule.timer_active ? 'ab__card--ok' : 'ab__card--err'}`}>
      <div className="ab__card-title">
        {schedule.timer_active ? '⏰ Timer attivo' : '⚠ Timer disattivato'}
      </div>
      <div className="ab__card-body">
        <div><strong>Prossimo run:</strong> {schedule.next_run || '—'}</div>
        <div><strong>Ultimo run:</strong> {schedule.last_run || '—'}</div>
      </div>
    </div>
  );
}


// ── Pagina principale ────────────────────────────────────────────────────
export default function AdminBackupsPage() {
  const toast = useToast();

  const [data, setData] = useState(null);
  const [status, setStatus] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  // RB.#10: stato dedicato per credenziali AWS scadute. Bloccato in
  // banner persistente + nessun auto-refresh finche' l'operatore non
  // clicca "Riprova" (dopo aver fatto `aws sso login` lato server).
  const [awsCredentialsError, setAwsCredentialsError] = useState(null);

  // Guard della rotta: /admin/backups e' protetta da <SuperadminRoute>
  // in App.jsx che fa il redirect PRIMA del mount. Qui nessun check
  // ridondante (il loadAll sotto non parte per utenti non superadmin).

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [list, st, sch] = await Promise.all([
        listBackups(),
        getBackupStatus(),
        getBackupSchedule(),
      ]);
      setData(list);
      setStatus(st);
      setSchedule(sch);
      // Reset stato AWS-error se eravamo in errore e ora la chiamata torna OK.
      setAwsCredentialsError(null);
    } catch (e) {
      const code = extractErrorCode(e);
      const message = extractErrorMessage(e);
      if (code === 'aws_credentials_expired') {
        // Banner persistente, no toast effimero. Stop auto-refresh.
        setAwsCredentialsError(message);
      } else {
        toast.error(message);
      }
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleRetryAws = useCallback(() => {
    setAwsCredentialsError(null);
    loadAll();
  }, [loadAll]);

  const handleTrigger = async () => {
    if (!confirm('Avviare un backup manuale adesso?\n\nIl pg_dump e l\'upload S3 richiedono qualche secondo. I backup schedulati continuano normalmente.')) {
      return;
    }
    setTriggering(true);
    try {
      const res = await triggerBackup();
      if (res.started) {
        toast.success(res.message);
        // Ricarica lista tra 5 secondi (tempo tipico per pg_dump + upload).
        // RB.#10: se siamo in stato AWS-credentials-expired, il refresh
        // automatico sarebbe inutile (la list_backups continuera' a 503);
        // l'utente riavvia manualmente con "Riprova" dopo `aws sso login`.
        setTimeout(() => {
          if (!awsCredentialsError) loadAll();
        }, 5000);
      } else {
        toast.error(res.message || 'Trigger backup non avviato');
      }
    } catch (e) {
      const code = extractErrorCode(e);
      const message = extractErrorMessage(e);
      if (code === 'aws_credentials_expired') {
        setAwsCredentialsError(message);
      } else {
        toast.error(message);
      }
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div className="ab">
      <AppSidebar />
      <main className="ab__main">
        {awsCredentialsError && (
          <div
            role="alert"
            style={{
              background: '#fff4e5',
              border: '1px solid #d97706',
              borderRadius: 6,
              color: '#92400e',
              padding: '12px 14px',
              marginBottom: 16,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12,
            }}
          >
            <div style={{ flex: 1 }}>
              <strong>Credenziali AWS scadute o non disponibili.</strong>
              <div style={{ marginTop: 4, fontSize: '0.92em', lineHeight: 1.4 }}>
                {awsCredentialsError}
              </div>
              <div style={{ marginTop: 6, fontSize: '0.85em', opacity: 0.85 }}>
                L'auto-refresh e' sospeso finche' non riprovi. Esegui
                <code style={{ margin: '0 4px' }}>aws sso login</code>
                sul server, poi clicca "Riprova".
              </div>
            </div>
            <button
              type="button"
              onClick={handleRetryAws}
              disabled={loading}
              style={{
                background: '#d97706',
                border: 'none',
                borderRadius: 4,
                color: '#fff',
                cursor: loading ? 'wait' : 'pointer',
                fontWeight: 600,
                padding: '6px 14px',
                whiteSpace: 'nowrap',
              }}
            >
              {loading ? 'Verifica...' : '↻ Riprova'}
            </button>
          </div>
        )}
        <header className="ab__header">
          <div>
            <h1 className="ab__title">Backup Database</h1>
            <div className="ab__subtitle">
              Schema applicativo <code>plenora</code> su S3
              {data && <> · bucket <code>{data.bucket}</code></>}
            </div>
          </div>
          <div className="ab__header-actions">
            <button
              className="ab__btn ab__btn--secondary"
              onClick={loadAll}
              disabled={loading}
            >
              {loading ? 'Aggiornamento...' : '↻ Aggiorna'}
            </button>
            <button
              className="ab__btn ab__btn--primary"
              onClick={handleTrigger}
              disabled={triggering || loading}
            >
              {triggering ? 'Avvio...' : '⚡ Esegui backup ora'}
            </button>
          </div>
        </header>

        <section className="ab__cards">
          <StatusCard status={status} />
          <ScheduleCard schedule={schedule} />
        </section>

        <section className="ab__list">
          <h2 className="ab__list-title">
            Backup su S3 {data && <span className="ab__count">({data.count})</span>}
          </h2>
          {loading && !data ? (
            <div className="ab__loading">Caricamento...</div>
          ) : data?.items?.length > 0 ? (
            <table className="ab__table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Dimensione</th>
                  <th>Storage class</th>
                  <th>Data</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map(item => (
                  <tr key={item.key}>
                    <td className="ab__key">{item.key}</td>
                    <td>{fmtSize(item.size_mb)}</td>
                    <td><StorageBadge cls={item.storage_class} /></td>
                    <td>{fmtDate(item.last_modified)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="ab__empty">Nessun backup presente.</div>
          )}
        </section>

        <section className="ab__notes">
          <h3>Procedure operative</h3>
          <ul>
            <li>
              <strong>Schedulazione:</strong> systemd timer <code>urby-backup.timer</code> —
              ogni notte alle 02:00 UTC (circa +05:00 dal delay randomizzato).
            </li>
            <li>
              <strong>Retention:</strong> lifecycle S3 transition a Standard-IA a 30 giorni,
              delete a 365 giorni. Multipart abort a 7 giorni.
            </li>
            <li>
              <strong>Restore:</strong> NON esposto in UI. Procedura manuale via SSH,
              vedi docstring di <code>backend/scripts/backup_db.py</code>.
            </li>
            <li>
              <strong>Scope:</strong> solo schema <code>plenora</code> (~12 MB). Schemi dominio
              (catasto, aste, OMI, ecc.) sono rigenerabili e non backuppati.
            </li>
          </ul>
        </section>
      </main>
    </div>
  );
}
