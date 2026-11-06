import React, { useCallback, useState } from 'react';
import PageLayout from '../components/common/PageLayout';
import ModalWrapper, { ModalButton } from '../components/common/ModalWrapper';
import { useAuth } from '../context/AuthContext';
import { can } from '../utils/permissions';
import { getTaskRuntime, resolveUncertainCaricaRun } from '../api/adminTaskRuntime';
import { humanizeErrorMessage } from '../utils/errorMessages';
import { useResource } from '../hooks/useResource';
import { ResourceError } from '../components/common/ListState';
import { useBusy } from '../hooks/useBusy';
import '../styles/AdminTaskRuntimePage.css';
import { formatDateTime, formatSeconds } from '../utils/format';



// Durate da utils/format (UI-3, U17): una sola forma in tutta l'app.
const duration = (seconds) => formatSeconds(Math.max(0, Number(seconds || 0)));

// Le due risoluzioni di una scrittura CARICA incerta, con le conseguenze
// spiegate prima di confermare (audit R10.5: niente window.prompt).
const RESOLUTIONS = {
  confirm_completed: {
    title: 'Conferma scrittura completata',
    consequence: 'L’esecuzione viene segnata come completata e non verrà ripetuta. '
      + 'Confermala solo dopo aver verificato nella destinazione che i dati sono stati scritti.',
    label: 'Cosa hai verificato sulla destinazione',
    confirmText: 'Conferma completata',
  },
  allow_retry: {
    title: 'Autorizza la retry',
    consequence: 'La scrittura potrà essere ripetuta. Se in realtà era già avvenuta, '
      + 'i dati verranno scritti una seconda volta (righe duplicate o sovrascritte). '
      + 'Autorizzala solo se hai verificato che la destinazione non contiene i dati.',
    label: 'Perché la retry è sicura',
    confirmText: 'Autorizza retry',
  },
};
const NOTE_MIN_LENGTH = 3;

function ResolveRunDialog({ request, busy, onCancel, onConfirm }) {
  const [note, setNote] = useState('');
  const cfg = RESOLUTIONS[request.resolution];
  const valid = note.trim().length >= NOTE_MIN_LENGTH;
  return (
    <ModalWrapper
      isOpen={true}
      onClose={onCancel}
      title={cfg.title}
      icon="⚠️"
      size="sm"
      busy={busy}
      footer={(
        <>
          <ModalButton variant="secondary" onClick={onCancel} disabled={busy}>Annulla</ModalButton>
          <ModalButton variant="danger" onClick={() => onConfirm(note.trim())} disabled={!valid || busy} loading={busy}>
            {cfg.confirmText}
          </ModalButton>
        </>
      )}
    >
      <p className="task-runtime__dialog-run">Esecuzione <code>{request.run.run_id}</code> · progetto <code>{request.run.project_id}</code></p>
      <p className="task-runtime__dialog-consequence">{cfg.consequence}</p>
      <label className="task-runtime__dialog-label">
        {cfg.label} (obbligatoria, almeno {NOTE_MIN_LENGTH} caratteri)
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={3}
          autoFocus
          disabled={busy}
        />
      </label>
    </ModalWrapper>
  );
}

function StatusPill({ value }) {
  return <span className={`task-runtime__pill task-runtime__pill--${value}`}>{value}</span>;
}

export default function AdminTaskRuntimePage() {
  // Fase 5 T2 (R4.14, R9.10): dati dall'hook comune (aggiornamento ogni
  // 5 s, un poll alla volta, fermo con la scheda nascosta); l'errore di
  // caricamento e quello di una risoluzione sono distinti (il poll non
  // cancella l'errore della risoluzione); risoluzioni per run (useBusy).
  const resource = useResource(getTaskRuntime, { key: 'task-runtime', refreshInterval: 'taskRuntime' });
  const data = resource.data || null;
  const loading = resource.loading;
  const load = resource.reload;
  const [actionError, setActionError] = useState('');
  const busy = useBusy();
  const [resolveRequest, setResolveRequest] = useState(null); // { run, resolution }
  const { user } = useAuth();
  const canResolve = can.manageTaskRuntime(user);

  const resolveRun = useCallback(async (note) => {
    if (!resolveRequest) return;
    const { run, resolution } = resolveRequest;
    setActionError('');
    try {
      const r = await busy.run(run.run_id, () => resolveUncertainCaricaRun(run.run_id, resolution, note));
      if (r.skipped) return;
      setResolveRequest(null);
      await load();
    } catch (err) {
      setActionError(humanizeErrorMessage(err));
    }
  }, [load, resolveRequest, busy]);

  const totals = data?.totals || {};
  const workQueues = (data?.queues || []).filter((item) => item.kind === 'work');
  const deadQueues = (data?.queues || []).filter((item) => item.kind === 'dead_letter');

  return (
    <PageLayout
      className="task-runtime"
      title="Code e Worker"
      badge={data?.status && <StatusPill value={data.status} />}
      subtitle="Stato operativo di RabbitMQ, worker Dramatiq e task in esecuzione."
      actions={(
        <button type="button" onClick={() => load()} disabled={loading}>
          {loading ? 'Aggiornamento…' : 'Aggiorna'}
        </button>
      )}
    >

        <ResourceError resource={resource} title="Stato dei task non caricato" />
        {actionError && <div className="task-runtime__error" role="alert">{actionError}</div>}
        {(data?.warnings || []).map((warning) => (
          <div className="task-runtime__warning" key={warning}>{warning}</div>
        ))}

        <section className="task-runtime__summary">
          <article><span>In coda</span><b>{totals.messages_ready ?? '—'}</b></article>
          <article><span>In esecuzione</span><b>{totals.running_tasks ?? '—'}</b></article>
          <article><span>Worker online</span><b>{totals.workers_online ?? '—'}</b></article>
          <article><span>Consumer</span><b>{totals.consumers ?? '—'}</b></article>
          <article className={totals.expired_task_leases > 0 ? 'task-runtime__summary-warning' : ''}>
            <span>Lease scaduti</span><b>{totals.expired_task_leases ?? '—'}</b>
          </article>
          <article className={totals.dead_letters > 0 ? 'task-runtime__summary-error' : ''}>
            <span>Dead letter</span><b>{totals.dead_letters ?? '—'}</b>
          </article>
          <article className={totals.uncertain_carica_runs > 0 ? 'task-runtime__summary-error' : ''}>
            <span>CARICA incerte</span><b>{totals.uncertain_carica_runs ?? '—'}</b>
          </article>
        </section>

        {!!(data?.uncertain_runs || []).length && (
          <section className="task-runtime__panel">
            <div className="task-runtime__panel-head">
              <div>
                <h2>Scritture CARICA da verificare</h2>
                <p>Controlla la destinazione prima di confermare o autorizzare una retry.</p>
              </div>
            </div>
            <div className="task-runtime__table-wrap">
              <table>
                <thead><tr><th>Esecuzione</th><th>Progetto</th><th>Gruppo</th><th>Avvio</th><th>Errore</th><th>Azioni</th></tr></thead>
                <tbody>
                  {data.uncertain_runs.map((run) => (
                    <tr key={run.run_id}>
                      <td className="task-runtime__mono" title={run.run_id}>{run.run_id}</td>
                      <td className="task-runtime__mono">{run.project_id}</td>
                      <td>{run.group_id}</td>
                      <td>{formatDateTime(run.timestamp, { seconds: true })}</td>
                      <td className="task-runtime__message" title={run.error_message || ''}>{run.error_message || '—'}</td>
                      <td className="task-runtime__actions">
                        {canResolve && <button type="button" disabled={busy.isBusy(run.run_id)} onClick={() => setResolveRequest({ run, resolution: 'confirm_completed' })}>Conferma completata</button>}
                        {canResolve && <button type="button" disabled={busy.isBusy(run.run_id)} onClick={() => setResolveRequest({ run, resolution: 'allow_retry' })}>Autorizza retry</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <section className="task-runtime__panel">
          <div className="task-runtime__panel-head">
            <div>
              <h2>RabbitMQ</h2>
              <p>{data?.broker_message || 'Caricamento…'}</p>
            </div>
          </div>
          <div className="task-runtime__queue-grid">
            {workQueues.map((queue) => (
              <article className="task-runtime__queue" key={queue.name}>
                <div><b>{queue.name}</b><StatusPill value={queue.status} /></div>
                <dl>
                  <dt>Messaggi pronti</dt><dd>{queue.messages_ready}</dd>
                  <dt>Consumer</dt><dd>{queue.consumers}</dd>
                </dl>
              </article>
            ))}
          </div>
          {deadQueues.some((queue) => queue.messages_ready > 0) && (
            <div className="task-runtime__dead-list">
              {deadQueues.filter((queue) => queue.messages_ready > 0).map((queue) => (
                <span key={queue.name}>{queue.name}: <b>{queue.messages_ready}</b></span>
              ))}
            </div>
          )}
          <p className="task-runtime__hint">
            “In coda” proviene da RabbitMQ; “in esecuzione” dai lease PostgreSQL.
          </p>
        </section>

        <section className="task-runtime__panel">
          <div className="task-runtime__panel-head">
            <div><h2>Worker</h2><p>Heartbeat condivisi tra Windows, Linux e container.</p></div>
          </div>
          <div className="task-runtime__table-wrap">
            <table>
              <thead><tr><th>Stato</th><th>Ruolo</th><th>Host / PID</th><th>Code</th><th>Ultimo heartbeat</th></tr></thead>
              <tbody>
                {(data?.workers || []).map((worker) => (
                  <tr key={worker.worker_id}>
                    <td><StatusPill value={worker.status} /></td>
                    <td>{worker.role}</td>
                    <td>{worker.hostname} / {worker.pid}</td>
                    <td>{worker.queues.join(', ') || '—'}</td>
                    <td>{duration(worker.heartbeat_age_seconds)} fa</td>
                  </tr>
                ))}
                {data && !(data.workers || []).length && (
                  <tr><td colSpan="5" className="task-runtime__empty">Nessun worker registrato</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="task-runtime__panel">
          <div className="task-runtime__panel-head">
            <div><h2>Task in esecuzione</h2><p>Processi pandas e task di controllo con lease attivo.</p></div>
          </div>
          <div className="task-runtime__table-wrap">
            <table>
              <thead><tr><th>Stato</th><th>Task</th><th>Actor</th><th>Worker</th><th>Durata</th><th>Progresso</th><th>Messaggio</th></tr></thead>
              <tbody>
                {(data?.running_tasks || []).map((task) => (
                  <tr key={task.task_id}>
                    <td><StatusPill value={task.status === 'lease_expired' ? 'warn' : 'ok'} /></td>
                    <td className="task-runtime__mono" title={task.task_id}>{task.task_id}</td>
                    <td>{task.actor_name}</td>
                    <td className="task-runtime__mono" title={task.worker_id}>{task.worker_id}</td>
                    <td>{duration(task.elapsed_seconds)}</td>
                    <td>{task.progress == null ? '—' : `${task.progress}%`}</td>
                    <td className="task-runtime__message" title={task.message || ''}>{task.message || '—'}</td>
                  </tr>
                ))}
                {data && !(data.running_tasks || []).length && (
                  <tr><td colSpan="7" className="task-runtime__empty">Nessun task in esecuzione</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      {resolveRequest && (
        <ResolveRunDialog
          request={resolveRequest}
          busy={busy.isBusy(resolveRequest.run.run_id)}
          onCancel={() => setResolveRequest(null)}
          onConfirm={resolveRun}
        />
      )}
    </PageLayout>
  );
}
