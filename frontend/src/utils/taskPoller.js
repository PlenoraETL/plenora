/**
 * taskPoller.js — IL polling di un task asincrono (Fase 5 T2, audit R4.11,
 * R4.12, N12). Lo usano solo hooks/useTask (un task) e hooks/useExecution
 * (i task di Carica, piu' d'uno in parallelo): il test
 * tests/shared/asyncArchitecture.contract.test.js vieta altri poller.
 *
 * Regole (una per caso, mai "continua per sempre"):
 *  - un poll alla volta: il successivo parte `interval` ms dopo la risposta;
 *  - stato del task -> utils/taskOutcome; fase finale = fine del polling;
 *  - 404: il task puo' non essere ancora registrato subito dopo l'avvio; dopo
 *    `notFoundLimit` 404 di fila l'esito e' `lost` ("non piu' disponibile");
 *  - 401 e 403: fine subito, esito `lost` (sessione finita o permessi tolti);
 *  - altri errori (rete, 5xx, 503 task_store_unavailable, risposta vuota):
 *    si riprova; dopo `errorLimit` errori di fila l'esito e' `lost` con il
 *    testo unico di utils/errorMessages;
 *  - stop() (smontaggio, cambio progetto, nuovo avvio) ferma tutto: nessuna
 *    risposta arrivata dopo scrive piu' niente.
 */
import { getTaskStatus } from '../api';
import { requestErrorMessage } from './errorMessages';
import { taskOutcome } from './taskOutcome';

export const TASK_POLL_DEFAULTS = Object.freeze({ interval: 1000, notFoundLimit: 15, errorLimit: 5 });

export const LOST_NOT_FOUND_MESSAGE = "L'operazione non è più disponibile sul server: potrebbe essere stata rimossa o scaduta.";

const lostOutcome = (error, code) => ({
  phase: 'lost', done: true, status: null, progress: 0, message: '', result: null, error, code,
});

/**
 * @param {string} taskId
 * @param {Object} opts
 * @param {(outcome, data) => void} [opts.onUpdate]  ogni poll riuscito (anche finale)
 * @param {(outcome) => void} opts.onDone            una volta, con l'esito finale
 * @param {number} [opts.interval]
 * @param {number} [opts.notFoundLimit]
 * @param {number} [opts.errorLimit]
 * @param {boolean} [opts.immediate=true]            primo poll subito
 * @param {Object} [opts.outcome]                    opzioni di taskOutcome
 * @param {(id) => Promise<Object>} [opts.getStatus] per i test
 * @returns {() => void} stop
 */
export function pollTask(taskId, {
  onUpdate = null,
  onDone,
  interval = TASK_POLL_DEFAULTS.interval,
  notFoundLimit = TASK_POLL_DEFAULTS.notFoundLimit,
  errorLimit = TASK_POLL_DEFAULTS.errorLimit,
  immediate = true,
  outcome: outcomeOpts = undefined,
  getStatus = getTaskStatus,
} = {}) {
  let stopped = false;
  let timer = null;
  let notFound = 0;
  let errors = 0;

  const finish = (outcome) => {
    if (stopped) return;
    stopped = true;
    onDone?.(outcome);
  };
  const schedule = () => {
    if (!stopped) timer = setTimeout(poll, interval);
  };

  async function poll() {
    timer = null;
    if (stopped) return;
    let data;
    try {
      data = await getStatus(taskId);
    } catch (err) {
      if (stopped) return;
      const status = err?.response?.status;
      if (status === 404) {
        notFound += 1;
        if (notFound >= notFoundLimit) finish(lostOutcome(LOST_NOT_FOUND_MESSAGE, 'task_not_found'));
        else schedule();
        return;
      }
      notFound = 0;
      if (status === 401 || status === 403) {
        finish(lostOutcome(requestErrorMessage(err), status === 401 ? 'unauthorized' : 'forbidden'));
        return;
      }
      errors += 1;
      if (errors >= errorLimit) finish(lostOutcome(requestErrorMessage(err), 'task_status_unavailable'));
      else schedule();
      return;
    }
    if (stopped) return;
    if (!data || typeof data !== 'object') {
      errors += 1;
      if (errors >= errorLimit) finish(lostOutcome("Lo stato dell'operazione non è leggibile.", 'task_status_unavailable'));
      else schedule();
      return;
    }
    notFound = 0;
    errors = 0;
    const outcome = taskOutcome(data, outcomeOpts);
    onUpdate?.(outcome, data);
    if (stopped) return;
    if (outcome.done) finish(outcome);
    else schedule();
  }

  if (immediate) poll();
  else schedule();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
  };
}

export default pollTask;
