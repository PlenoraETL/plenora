import React, { useCallback, useEffect, useState } from 'react';
import AppSidebar from '../components/AppSidebar';
import { getTaskRuntime } from '../api/adminTaskRuntime';
import '../styles/AdminTaskRuntimePage.css';


const REFRESH_MS = 5000;

function duration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
}

function StatusPill({ value }) {
  return <span className={`task-runtime__pill task-runtime__pill--${value}`}>{value}</span>;
}

export default function AdminTaskRuntimePage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getTaskRuntime());
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Stato task non disponibile');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer;
    const poll = async () => {
      await load();
      if (!stopped) timer = setTimeout(poll, REFRESH_MS);
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [load]);

  const totals = data?.totals || {};
  const workQueues = (data?.queues || []).filter((item) => item.kind === 'work');
  const deadQueues = (data?.queues || []).filter((item) => item.kind === 'dead_letter');

  return (
    <div className="task-runtime">
      <AppSidebar />
      <main className="task-runtime__main">
        <header className="task-runtime__header">
          <div>
            <div className="task-runtime__title-row">
              <h1>Code e Worker</h1>
              {data?.status && <StatusPill value={data.status} />}
            </div>
            <p>Stato operativo di RabbitMQ, worker Dramatiq e task in esecuzione.</p>
          </div>
          <button type="button" onClick={load} disabled={loading}>
            {loading ? 'Aggiornamento…' : 'Aggiorna'}
          </button>
        </header>

        {error && <div className="task-runtime__error">{error}</div>}
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
        </section>

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
                {!loading && !(data?.workers || []).length && (
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
                {!loading && !(data?.running_tasks || []).length && (
                  <tr><td colSpan="7" className="task-runtime__empty">Nessun task in esecuzione</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
