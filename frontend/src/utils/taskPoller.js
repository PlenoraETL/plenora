/**
 * taskPoller.js — IL polling dei task asincroni (Fase 5 T2, audit R4.11,
 * R4.12, N12; UI-1 06/11/2026, audit C19-C20). Lo usano solo hooks/useTask
 * (un task) e hooks/useExecution (i task di Carica): il test
 * tests/shared/asyncArchitecture.contract.test.js vieta altri poller.
 *
 * Il ritmo e' quello di utils/poller (profilo `task`): un giro alla volta,
 * fermo con la scheda nascosta, backoff quando lo stato non cambia, durata
 * massima. Piu' task insieme (Carica) = UN poller che a ogni giro chiede lo
 * stato di quelli ancora aperti (pollTasks), non un poller per task.
 *
 * Regole per ogni task (una per caso, mai "continua per sempre"):
 *  - stato del task -> utils/taskOutcome; fase finale = fine del polling;
 *  - 404: il task puo' non essere ancora registrato subito dopo l'avvio; dopo
 *    `notFoundLimit` 404 di fila l'esito e' `lost` ("non piu' disponibile");
 *  - 401 e 403: fine subito, esito `lost` (sessione finita o permessi tolti);
 *  - altri errori (rete, 5xx, 503 task_store_unavailable, risposta vuota):
 *    si riprova; dopo `errorLimit` errori di fila l'esito e' `lost` con il
 *    testo unico di utils/errorMessages;
 *  - durata massima del profilo superata: esito `lost` (task_poll_expired);
 *  - stop() (smontaggio, cambio progetto, nuovo avvio) ferma tutto: nessuna
 *    risposta arrivata dopo scrive piu' niente.
 */
import { getTaskStatus } from '../api';
import { requestErrorMessage } from './errorMessages';
import { taskOutcome } from './taskOutcome';
import { POLL_PROFILES, startPoller } from './poller';

const TASK_POLL_DEFAULTS = Object.freeze({ notFoundLimit: 15, errorLimit: 5 });

const LOST_NOT_FOUND_MESSAGE = "L'operazione non è più disponibile sul server: potrebbe essere stata rimossa o scaduta.";
const EXPIRED_MESSAGE = "Lo stato dell'operazione non si segue più da questa pagina: può essere ancora in corso, l'esito è nei Log.";

const lostOutcome = (error, code) => ({
  phase: 'lost', done: true, status: null, progress: 0, message: '', result: null, error, code,
});

const outcomeSignature = (o) => `${o.phase}|${o.status}|${o.progress}|${o.message}`;

/**
 * Un task seguito: `step()` chiede lo stato una volta e applica le regole.
 * @returns {{ step: () => Promise<{changed: boolean, done: boolean}>, expire: () => void, done: () => boolean, stop: () => void }}
 */
function trackTask(taskId, { onUpdate, onDone, notFoundLimit, errorLimit, outcomeOpts, getStatus }) {
  let finished = false;
  let notFound = 0;
  let errors = 0;
  let last = null;

  const finish = (outcome) => {
    if (finished) return;
    finished = true;
    onDone?.(outcome);
  };
  const result = (changed) => ({ changed, done: finished });

  async function step() {
    if (finished) return result(false);
    let data;
    try {
      data = await getStatus(taskId);
    } catch (err) {
      if (finished) return result(false);
      const status = err?.response?.status;
      if (status === 404) {
        notFound += 1;
        if (notFound >= notFoundLimit) finish(lostOutcome(LOST_NOT_FOUND_MESSAGE, 'task_not_found'));
        return result(false);
      }
      notFound = 0;
      if (status === 401 || status === 403) {
        finish(lostOutcome(requestErrorMessage(err), status === 401 ? 'unauthorized' : 'forbidden'));
        return result(true);
      }
      errors += 1;
      if (errors >= errorLimit) finish(lostOutcome(requestErrorMessage(err), 'task_status_unavailable'));
      return result(false);
    }
    if (finished) return result(false);
    if (!data || typeof data !== 'object') {
      errors += 1;
      if (errors >= errorLimit) finish(lostOutcome("Lo stato dell'operazione non è leggibile.", 'task_status_unavailable'));
      return result(false);
    }
    notFound = 0;
    errors = 0;
    const outcome = taskOutcome(data, outcomeOpts);
    const sig = outcomeSignature(outcome);
    const changed = sig !== last;
    last = sig;
    onUpdate?.(outcome, data);
    if (finished) return result(changed);
    if (outcome.done) finish(outcome);
    return result(changed);
  }

  return {
    step,
    expire: () => finish(lostOutcome(EXPIRED_MESSAGE, 'task_poll_expired')),
    done: () => finished,
    stop: () => { finished = true; },
  };
}

const profileOpts = ({ interval, maxInterval, maxDurationMs }) => {
  const base = POLL_PROFILES.task;
  const iv = interval ?? base.interval;
  return {
    interval: iv,
    maxInterval: Math.max(iv, maxInterval ?? base.maxInterval),
    maxDurationMs: maxDurationMs ?? base.maxDurationMs,
  };
};

/**
 * @param {string} taskId
 * @param {Object} opts
 * @param {(outcome, data) => void} [opts.onUpdate]  ogni poll riuscito (anche finale)
 * @param {(outcome) => void} opts.onDone            una volta, con l'esito finale
 * @param {number} [opts.interval]                   attesa base (profilo `task`)
 * @param {number} [opts.maxInterval]
 * @param {number} [opts.maxDurationMs]
 * @param {number} [opts.notFoundLimit]
 * @param {number} [opts.errorLimit]
 * @param {boolean} [opts.immediate=true]            primo poll subito
 * @param {Object} [opts.outcome]                    opzioni di taskOutcome
 * @param {(id) => Promise<Object>} [opts.getStatus] per i test
 * @returns {() => void} stop
 */
export function pollTask(taskId, opts = {}) {
  const stopAll = pollTasks([taskId], {
    ...opts,
    onUpdate: opts.onUpdate ? (_id, o, data) => opts.onUpdate(o, data) : null,
    onDone: (_id, o) => opts.onDone?.(o),
  });
  return stopAll;
}

/**
 * Piu' task con UN poller (Carica): a ogni giro lo stato dei task ancora
 * aperti, in parallelo; il poller finisce quando sono finiti tutti.
 * @param {string[]} taskIds
 * @param {Object} opts  come pollTask, ma onUpdate(taskId, outcome, data) e onDone(taskId, outcome)
 * @returns {() => void} stop
 */
export function pollTasks(taskIds, {
  onUpdate = null,
  onDone,
  interval,
  maxInterval,
  maxDurationMs,
  notFoundLimit = TASK_POLL_DEFAULTS.notFoundLimit,
  errorLimit = TASK_POLL_DEFAULTS.errorLimit,
  immediate = true,
  outcome: outcomeOpts = undefined,
  getStatus = getTaskStatus,
} = {}) {
  let stopped = false;
  const trackers = taskIds.map((id) => trackTask(id, {
    notFoundLimit, errorLimit, outcomeOpts, getStatus,
    onUpdate: onUpdate ? (o, data) => { if (!stopped) onUpdate(id, o, data); } : null,
    onDone: (o) => { if (!stopped) onDone?.(id, o); },
  }));
  const open = () => trackers.filter((t) => !t.done());

  const stopPoller = startPoller(async () => {
    if (stopped) return { done: true };
    const steps = await Promise.all(open().map((t) => t.step()));
    if (stopped) return { done: true };
    return { changed: steps.some((s) => s.changed), done: open().length === 0 };
  }, {
    ...profileOpts({ interval, maxInterval, maxDurationMs }),
    immediate,
    onExpire: () => { open().forEach((t) => t.expire()); },
  });

  return () => {
    stopped = true;
    trackers.forEach((t) => t.stop());
    stopPoller();
  };
}

export default pollTask;
