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
import { useResource } from '../hooks/useResource';
import { ResourceError } from '../components/common/ListState';
import { useToast } from '../hooks/useToast';
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';
import {
  listBackups, getBackupStatus, getBackupSchedule, triggerBackup,
} from '../api/adminBackups';
import PageLayout from '../components/common/PageLayout';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import '../styles/AdminBackupsPage.css';
import { requestErrorMessage } from '../utils/errorMessages';
import { formatDateTime as fmtDate, formatBytes } from '../utils/format';

const fmtSize = (mb) => formatBytes(mb, { from: 'MB' });
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
// Fase 5 T2 (R4.15): ogni scheda mostra il SUO stato (caricamento, errore
// con «Riprova», dati): mai "Caricamento..." per sempre.
function CardState({ resource, className, title }) {
  if (resource.status === 'error') {
    return (
      <div className={`ab__card ${className}`}>
        <ResourceError compact resource={resource} title={title} />
      </div>
    );
  }
  return <div className={`ab__card ${className}`}>Caricamento...</div>;
}

function StatusCard({ status, resource }) {
  if (!status) return <CardState resource={resource} className="ab__card--status" title="Stato non caricato" />;

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
      {/* Ricarica non riuscita: lo stato mostrato e' quello di prima. */}
      <ResourceError resource={resource} title="Stato non caricato" />
      <div className="ab__card-title">{resultLabels[status.result] || status.result}</div>
      <div className="ab__card-body">
        <div><strong>Terminato:</strong> {fmtDate(status.finished_at)}</div>
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
function ScheduleCard({ schedule, resource }) {
  if (!schedule) return <CardState resource={resource} className="ab__card--schedule" title="Pianificazione non caricata" />;

  return (
    <div className={`ab__card ${schedule.timer_active ? 'ab__card--ok' : 'ab__card--err'}`}>
      <ResourceError resource={resource} title="Pianificazione non caricata" />
      <div className="ab__card-title">
        {schedule.timer_active ? '⏰ Timer attivo' : '⚠ Timer disattivato'}
      </div>
      <div className="ab__card-body">
        <div><strong>Prossimo run:</strong> {fmtDate(schedule.next_run)}</div>
        <div><strong>Ultimo run:</strong> {fmtDate(schedule.last_run)}</div>
      </div>
    </div>
  );
}
export default function AdminBackupsPage() {
  const toast = useToast();
  const { user } = useAuth();
  const canTrigger = can.manageBackups(user);
  const confirmModal = useConfirmModal();

  // Fase 5 T2 (R4.15): tre risorse dell'hook comune, ognuna con il suo
  // stato. Credenziali AWS scadute: nessun aggiornamento automatico finche'
  // l'operatore non clicca «Riprova». Dopo "Esegui backup ora" lo stato si
  // segue (ogni 5 s) finche' il backup non e' finito; la lista si ricarica
  // quando l'ultimo backup cambia (chiave della lista).
  const [triggered, setTriggered] = useState(null); // { finishedAt } prima dell'avvio
  const [triggerError, setTriggerError] = useState(null); // { code, text }
  // Versione della lista: l'ultimo backup finito visto seguendo lo stato.
  const [listVersion, setListVersion] = useState(null);
  const statusResource = useResource(getBackupStatus, { key: 'backup-status' });
  const status = statusResource.data || null;
  const scheduleResource = useResource(getBackupSchedule, { key: 'backup-schedule' });
  const schedule = scheduleResource.data || null;
  const listResource = useResource(() => listBackups(), {
    key: ['backup-list', listVersion],
    keepPreviousData: true,
  });
  const listCode = listResource.rawError ? extractErrorCode(listResource.rawError) : null;
  const listMessage = listResource.rawError ? extractErrorMessage(listResource.rawError) : null;
  const errorCode = listCode || triggerError?.code || null;
  const backupUnavailable = isObjectStorageDisabled(errorCode) ? (listMessage || triggerError?.text) : null;
  const awsCredentialsError = errorCode === 'aws_credentials_expired' ? (listMessage || triggerError?.text) : null;
  const [extra, setExtra] = useState(null); // pagine successive della lista
  const base = backupUnavailable ? null : (listResource.data || null);
  const data = base && extra && extra.after === base.next_cursor
    ? { ...extra.page, items: [...(base.items || []), ...extra.items], count: (base.items?.length || 0) + extra.items.length }
    : base;
  const loading = listResource.loading || statusResource.loading || scheduleResource.loading;
  const [loadingMore, setLoadingMore] = useState(false);
  const [triggering, setTriggering] = useState(false);

  // Si segue il backup avviato finche' non c'e' un esito nuovo.
  const following = Boolean(triggered) && !awsCredentialsError;
  const running = status?.result === 'running';
  const followStatus = useResource(getBackupStatus, {
    key: 'backup-status-follow',
    enabled: following || running,
    refreshInterval: 5000,
  });
  const followed = followStatus.data;
  useEffect(() => {
    if (!followed) return;
    statusResource.setData(followed);
    if (followed.result === 'running') return;
    if (followed.finished_at && followed.finished_at !== listVersion) setListVersion(followed.finished_at);
    if (triggered && followed.finished_at !== triggered.finishedAt) setTriggered(null);
  }, [followed]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadAll = useCallback(() => {
    setTriggerError(null);
    setExtra(null);
    listResource.reload();
    statusResource.reload();
    scheduleResource.reload();
  }, [listResource, statusResource, scheduleResource]);

  const handleRetryAws = loadAll;

  const handleLoadMore = useCallback(async () => {
    if (!data?.next_cursor || loadingMore || !base) return;
    setLoadingMore(true);
    try {
      const cursor = data.next_cursor;
      const page = await listBackups({ cursor });
      setExtra(cur => {
        const prev = cur && cur.after === base.next_cursor ? cur : { after: base.next_cursor, items: [] };
        return { after: prev.after, page, items: [...prev.items, ...(page.items || [])] };
      });
    } catch (error) {
      toast.error(extractErrorMessage(error));
    } finally {
      setLoadingMore(false);
    }
  }, [data?.next_cursor, loadingMore, toast, base]);

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
        // Si segue lo stato fino all'esito (followStatus); la lista si
        // ricarica quando l'ultimo backup cambia.
        setTriggered({ finishedAt: status?.finished_at || null });
      } else {
        toast.error(res.message || 'Trigger backup non avviato');
      }
    } catch (e) {
      const code = extractErrorCode(e);
      const message = extractErrorMessage(e);
      if (isObjectStorageDisabled(code) || code === 'aws_credentials_expired') {
        setTriggerError({ code, text: message });
      } else {
        toast.error(message);
      }
    } finally {
      setTriggering(false);
    }
  };

  return (
    <PageLayout
      className="ab"
      title="Backup Database"
      subtitle={<>Schema applicativo <code>plenora</code> su S3{data && <> · bucket <code>{data.bucket}</code></>}</>}
      actions={(
        <>
          <button
            type="button"
            className="ab__btn ab__btn--secondary"
            onClick={() => loadAll()}
            disabled={loading}
          >
            {loading ? 'Aggiornamento...' : '↻ Aggiorna'}
          </button>
          {canTrigger && <button
            type="button"
            className="ab__btn ab__btn--primary"
            onClick={handleTrigger}
            disabled={triggering || loading || Boolean(backupUnavailable) || Boolean(awsCredentialsError)}
          >
            {triggering ? 'Avvio...' : '⚡ Esegui backup ora'}
          </button>}
        </>
      )}
    >
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
          <StatusCard status={status} resource={statusResource} />
          <ScheduleCard schedule={schedule} resource={scheduleResource} />
        </section>

        <section className="ab__list">
          <h2 className="ab__list-title">
            Backup su S3 {data && <span className="ab__count">({data.count})</span>}
          </h2>
          {following && (
            <p className="ab__subtitle" role="status">Backup avviato: lo stato si aggiorna fino alla fine.</p>
          )}
          {(following || running) && <ResourceError resource={followStatus} title="Stato del backup non aggiornato" />}
          {/* Errore SEMPRE visibile (Aggiorna, nuova versione della lista):
              senza dati al posto della lista, con dati come avviso sopra. I
              casi con un pannello proprio (storage disattivato, credenziali
              AWS scadute) non lo ripetono. */}
          {!backupUnavailable && !awsCredentialsError && (
            <ResourceError resource={listResource} title="Lista dei backup non caricata" />
          )}
          {listResource.initialLoading ? (
            <div className="ab__loading">Caricamento...</div>
          ) : backupUnavailable ? (
            <div className="ab__empty ab__empty--disabled">
              Lista backup non disponibile in questo deployment.
            </div>
          ) : listResource.status === 'error' && !data ? null : data?.items?.length > 0 ? (
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
      <ConfirmDialog {...confirmModal} />
    </PageLayout>
  );
}
