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
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';
import {
  listBackups, getBackupStatus, getBackupSchedule, triggerBackup,
} from '../api/adminBackups';
import AppSidebar from '../components/AppSidebar';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import '../styles/AdminBackupsPage.css';
import { requestErrorMessage } from '../utils/errorMessages';
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
  // Una sola normalizzazione: utils/errorMessages.
  return requestErrorMessage(err, 'Errore generico');
}

function extractErrorCode(err) {
  // Preferisce err.code (attaccato dall'interceptor da data.error).
  // Fallback a detail.error per shape legacy.
  if (typeof err?.code === 'string') return err.code;
  const detail = err?.response?.data?.detail;
  if (detail && typeof detail === 'object' && typeof detail.error === 'string') {
    return detail.error;
  }
  return null;
}

function isObjectStorageDisabled(code) {
  return code === 'object_storage_disabled';
}

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
export default function AdminBackupsPage() {
  const toast = useToast();
  const { user } = useAuth();
  const canTrigger = can.manageBackups(user);
  const confirmModal = useConfirmModal();

  const [data, setData] = useState(null);
  const [status, setStatus] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [backupUnavailable, setBackupUnavailable] = useState(null);
  // RB.#10: stato dedicato per credenziali AWS scadute. Bloccato in
  // banner persistente + nessun auto-refresh finche' l'operatore non
  // clicca "Riprova" (dopo aver fatto `aws sso login` lato server).
  const [awsCredentialsError, setAwsCredentialsError] = useState(null);

  // Guard della rotta: /admin/backups e' protetta da <SuperadminRoute>
  // in App.jsx che fa il redirect PRIMA del mount. Qui nessun check
  // ridondante (il loadAll sotto non parte per utenti non superadmin).

  const loadAll = useCallback(async () => {
    setLoading(true);
    // try/finally garantisce che setLoading(false) venga sempre eseguito
    // anche se un helper sincrono successivo solleva (extractErrorCode/
    // Message o isObjectStorageDisabled potrebbero throware in futuro).
    // Pre-fix la UI restava in stato "Loading..." indefinito su throw.
    try {
      const [listResult, statusResult, scheduleResult] = await Promise.allSettled([
        listBackups(),
        getBackupStatus(),
        getBackupSchedule(),
      ]);

      let handledStorageDisabled = false;
      let handledAwsCredentials = false;
      let genericError = null;

      if (listResult.status === 'fulfilled') {
        setData(listResult.value);
        setBackupUnavailable(null);
        setAwsCredentialsError(null);
      } else {
        const code = extractErrorCode(listResult.reason);
        const message = extractErrorMessage(listResult.reason);
        if (isObjectStorageDisabled(code)) {
          handledStorageDisabled = true;
          setBackupUnavailable(message);
          setAwsCredentialsError(null);
          setData(null);
        } else if (code === 'aws_credentials_expired') {
          handledAwsCredentials = true;
          setAwsCredentialsError(message);
          setBackupUnavailable(null);
        } else {
          genericError = message;
        }
      }

      if (statusResult.status === 'fulfilled') {
        setStatus(statusResult.value);
      } else if (!handledStorageDisabled && !handledAwsCredentials) {
        genericError = genericError || extractErrorMessage(statusResult.reason);
      }

      if (scheduleResult.status === 'fulfilled') {
        setSchedule(scheduleResult.value);
      } else if (!handledStorageDisabled && !handledAwsCredentials) {
        genericError = genericError || extractErrorMessage(scheduleResult.reason);
      }

      if (genericError) {
        toast.error(genericError);
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

  const handleLoadMore = useCallback(async () => {
    if (!data?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listBackups({ cursor: data.next_cursor });
      setData(current => ({
        ...page,
        items: [...(current?.items || []), ...(page.items || [])],
        count: (current?.items?.length || 0) + (page.items?.length || 0),
      }));
    } catch (error) {
      toast.error(extractErrorMessage(error));
    } finally {
      setLoadingMore(false);
    }
  }, [data?.next_cursor, loadingMore, toast]);

  const handleTrigger = async () => {
    if (!canTrigger) return;
    const ok = await confirmModal.confirm({
      title: 'Backup manuale',
      message: (
        <>
          Avviare un backup manuale adesso?
          <br /><br />
          Il pg_dump e l'upload S3 richiedono qualche secondo. I backup
          schedulati continuano normalmente.
        </>
      ),
      confirmText: 'Avvia backup',
      variant: 'warning',
    });
    if (!ok) return;
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
      if (isObjectStorageDisabled(code)) {
        setBackupUnavailable(message);
        setAwsCredentialsError(null);
      } else if (code === 'aws_credentials_expired') {
        setAwsCredentialsError(message);
        setBackupUnavailable(null);
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
          <div role="alert" className="plen-banner-warn plen-banner-warn--lg ab__aws-banner-row">
            <div className="ab__aws-banner-body">
              <strong>Credenziali AWS scadute o non disponibili.</strong>
              <div className="plen-banner-warn__detail plen-banner-warn__detail--md">
                {awsCredentialsError}
              </div>
              <div className="plen-banner-warn__detail">
                L'auto-refresh e' sospeso finche' non riprovi. Esegui
                <code className="ab__aws-banner-cmd">aws sso login</code>
                sul server, poi clicca "Riprova".
              </div>
            </div>
            <button
              type="button"
              onClick={handleRetryAws}
              disabled={loading}
              className="ab__aws-banner-btn"
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
            {canTrigger && <button
              className="ab__btn ab__btn--primary"
              onClick={handleTrigger}
              disabled={triggering || loading || Boolean(backupUnavailable) || Boolean(awsCredentialsError)}
            >
              {triggering ? 'Avvio...' : '⚡ Esegui backup ora'}
            </button>}
          </div>
        </header>

        {backupUnavailable && (
          <section className="ab__disabled-panel" aria-live="polite">
            <div className="ab__disabled-title">Backup S3 non configurato</div>
            <p className="ab__disabled-text">{backupUnavailable}</p>
            <p className="ab__disabled-text">
              Nel profilo Docker locale l'app gira in modalita' DB-only. Questa
              pagina resta disattivata finche' non configuri object storage.
            </p>
            <div className="ab__disabled-codes">
              <code>OBJECT_STORAGE_ENABLED=true</code>
              <code>BACKUP_BUCKET=&lt;bucket&gt;</code>
              <code>CLOUD_PROVIDER=aws|minio</code>
            </div>
          </section>
        )}

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
          ) : backupUnavailable ? (
            <div className="ab__empty ab__empty--disabled">
              Lista backup non disponibile in questo deployment.
            </div>
          ) : data?.items?.length > 0 ? (
            <>
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
            {data.next_cursor && (
              <button
                type="button"
                className="ab__btn ab__btn--secondary"
                onClick={handleLoadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Caricamento...' : 'Carica altri backup'}
              </button>
            )}
            </>
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
      <ConfirmDialog {...confirmModal} />
    </div>
  );
}
