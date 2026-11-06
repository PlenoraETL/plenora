/**
 * poller.js — IL polling del client (UI-1, 06/11/2026; audit C19-C23).
 *
 * Unico modulo con un timer che richiama il server a intervalli. Lo usano:
 *  - hooks/useResource (`refreshInterval`: liste e stati aggiornati da soli);
 *  - utils/taskPoller (un task, e i task di Carica con UN poller condiviso).
 * Il test tests/shared/dataPolling.contract.test.js vieta timer di polling
 * altrove (ogni setTimeout/setInterval di src/ ha un motivo dichiarato).
 *
 * Regole (una per caso, uguali per tutti):
 *  - un giro alla volta: il successivo parte dopo la risposta del giro;
 *  - scheda nascosta: nessun giro; quando torna visibile, un giro subito e
 *    l'attesa riparte da `interval`;
 *  - backoff: un giro senza novita' (`tick` risolve `{ changed: false }`)
 *    o fallito allunga l'attesa di BACKOFF_FACTOR fino a `maxInterval`; una
 *    novita' la riporta a `interval`;
 *  - durata massima `maxDurationMs`: scaduta, `onExpire()`. Con
 *    `resumeOnVisible` (dati) il polling si ferma e riparte da capo al
 *    prossimo ritorno sulla scheda; senza (task) finisce;
 *  - `tick` che risolve `{ done: true }` = fine; stop() = fine (smontaggio).
 *
 * I profili sono la tabella dichiarativa di chi aggiorna cosa e quanto
 * spesso: una pagina non sceglie millisecondi, sceglie un profilo.
 */

export const BACKOFF_FACTOR = 1.5;

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

/**
 * Profili di polling. `interval` = attesa dopo un giro con novita';
 * `maxInterval` = tetto del backoff; `maxDurationMs` = durata massima
 * (0 = nessuna). I task finiscono da soli: la durata massima e' la rete di
 * sicurezza oltre il tempo massimo di un'esecuzione (DATA_PLANE_CHILD_TIMEOUT
 * di 65 minuti, piu' l'attesa in coda).
 */
export const POLL_PROFILES = Object.freeze({
  // Task asincroni (anteprima, esecuzione, Carica, eseguibili).
  task: Object.freeze({ interval: 1000, maxInterval: 5000, maxDurationMs: 3 * HOUR }),
  // Schedule con un'esecuzione in corso / senza.
  scheduleActive: Object.freeze({ interval: 3000, maxInterval: 15_000, maxDurationMs: 30 * MINUTE }),
  scheduleIdle: Object.freeze({ interval: 30_000, maxInterval: 2 * MINUTE, maxDurationMs: 30 * MINUTE }),
  // Log ed esecuzioni «in diretta» (RunsTab, RunDetail, LogViewer).
  live: Object.freeze({ interval: 5000, maxInterval: 30_000, maxDurationMs: 30 * MINUTE }),
  // Pagine di sistema.
  systemStatus: Object.freeze({ interval: 30_000, maxInterval: 2 * MINUTE, maxDurationMs: 30 * MINUTE }),
  taskRuntime: Object.freeze({ interval: 5000, maxInterval: 30_000, maxDurationMs: 30 * MINUTE }),
  backupFollow: Object.freeze({ interval: 5000, maxInterval: 30_000, maxDurationMs: 2 * HOUR }),
  // Contatore dei ticket nella barra laterale.
  ticketCounts: Object.freeze({ interval: MINUTE, maxInterval: 5 * MINUTE, maxDurationMs: 2 * HOUR }),
});

/** Profilo per nome (o oggetto profilo gia' fatto); null se assente/0. */
export function pollProfile(spec) {
  if (!spec) return null;
  if (typeof spec === 'string') {
    const p = POLL_PROFILES[spec];
    if (!p) throw new Error(`[poller] profilo sconosciuto: ${spec}`);
    return p;
  }
  // Un numero = intervallo fisso (solo i test: in src/ si sceglie un profilo).
  if (typeof spec === 'number') return { interval: spec, maxInterval: spec, maxDurationMs: 0 };
  if (typeof spec === 'object' && spec.interval >= 0) return spec;
  throw new Error('[poller] profilo non valido: usare un nome di POLL_PROFILES');
}

const pageHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

/**
 * @param {() => Promise<{changed?: boolean, done?: boolean}|void>} tick
 * @param {Object} opts
 * @param {number} opts.interval
 * @param {number} [opts.maxInterval=interval]
 * @param {number} [opts.maxDurationMs=0]
 * @param {boolean} [opts.immediate=false]      primo giro subito
 * @param {boolean} [opts.pauseWhenHidden=true]
 * @param {boolean} [opts.resumeOnVisible=false] dopo la scadenza riparte al ritorno sulla scheda
 * @param {() => void} [opts.onExpire]
 * @returns {() => void} stop
 */
export function startPoller(tick, {
  interval,
  maxInterval = interval,
  maxDurationMs = 0,
  immediate = false,
  pauseWhenHidden = true,
  resumeOnVisible = false,
  onExpire = null,
} = {}) {
  let stopped = false;
  let expired = false;
  let running = false;
  let timer = null;
  let wait = interval;
  let startedAt = Date.now();
  const hidden = () => pauseWhenHidden && pageHidden();

  const clear = () => { if (timer) clearTimeout(timer); timer = null; };
  const schedule = () => {
    clear();
    if (!stopped && !expired) timer = setTimeout(fire, wait);
  };
  const stop = () => {
    stopped = true;
    clear();
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
  };
  const expire = () => {
    expired = true;
    clear();
    onExpire?.();
    if (!resumeOnVisible) stop();
  };

  function fire() {
    timer = null;
    if (stopped || expired || running) return;
    if (maxDurationMs > 0 && Date.now() - startedAt >= maxDurationMs) { expire(); return; }
    // Nascosta: si aspetta il ritorno sulla scheda (onVisibility).
    if (hidden()) return;
    running = true;
    let pending;
    try {
      pending = Promise.resolve(tick());
    } catch (err) {
      pending = Promise.reject(err);
    }
    pending
      .then(
        (r) => {
          if (r?.done) { stop(); return; }
          wait = r?.changed === false ? Math.min(maxInterval, wait * BACKOFF_FACTOR) : interval;
        },
        () => { wait = Math.min(maxInterval, wait * BACKOFF_FACTOR); },
      )
      .then(() => {
        running = false;
        if (!stopped) schedule();
      });
  }

  function onVisibility() {
    if (stopped || pageHidden()) return;
    if (expired) {
      expired = false;
      startedAt = Date.now();
    }
    wait = interval;
    clear();
    fire();
  }

  if (pauseWhenHidden && typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
  if (immediate) fire();
  else schedule();
  return stop;
}

export default startPoller;
