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
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || 'Errore caricamento backup');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
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
        // Ricarica lista tra 5 secondi (tempo tipico per pg_dump + upload)
        setTimeout(() => loadAll(), 5000);
      } else {
        toast.error(res.message);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || 'Errore trigger backup');
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div className="ab">
      <AppSidebar />
      <main className="ab__main">
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
