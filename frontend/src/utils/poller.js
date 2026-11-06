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
 *  - durata massima `maxDurationMs`, in tempo di polling ATTIVO (conferma
 *    A-K5a): la scheda nascosta e la sospensione del computer non contano
 *    (tra due giri conta al piu' l'attesa prevista). La scadenza si
 *    controlla DOPO un giro, quindi c'e' sempre un'ultima lettura: un task
 *    finito mentre la scheda era nascosta risulta finito, non perso.
 *    Scaduta: `onExpire()`. Con `resumeOnVisible` (dati) il polling si
 *    ferma e riparte da capo al ritorno sulla scheda O alla prima attivita'
 *    dell'utente sulla pagina (tasto, clic, rotella: A-K5b), e chi lo usa
 *    lo dice (useResource `paused`); senza (task) finisce;
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
  // Arricchisci (A-K5d): nessun tetto di durata lato server (chiamate API a
  // ritmo limitato, anche ore): il tetto del client e' dichiarato qui, una
  // giornata di polling attivo; oltre, l'esito e' nei Log.
  enrichTask: Object.freeze({ interval: 1000, maxInterval: 15_000, maxDurationMs: 24 * HOUR }),
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

// Attivita' dell'utente che fa ripartire un polling di dati scaduto (A-K5b).
const ACTIVITY_EVENTS = Object.freeze(['pointerdown', 'keydown', 'wheel']);
// Tra due giri conta al piu' l'attesa prevista piu' questo margine: un
// computer sospeso o un timer ritardato non consumano la durata massima.
const ACTIVE_SLACK_MS = 1000;

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
  // Tempo di polling attivo e ultimo istante contato.
  let active = 0;
  let mark = Date.now();
  const hidden = () => pauseWhenHidden && pageHidden();
  const count = () => {
    const now = Date.now();
    active += Math.max(0, Math.min(now - mark, wait + ACTIVE_SLACK_MS));
    mark = now;
  };
  const doc = typeof document !== 'undefined' ? document : null;
  const listenActivity = (on) => {
    if (!doc || !resumeOnVisible) return;
    ACTIVITY_EVENTS.forEach((type) => (on ? doc.addEventListener(type, onActivity, { passive: true, capture: true }) : doc.removeEventListener(type, onActivity, { capture: true })));
  };

  const clear = () => { if (timer) clearTimeout(timer); timer = null; };
  const schedule = () => {
    clear();
    if (!stopped && !expired) timer = setTimeout(fire, wait);
  };
  const stop = () => {
    stopped = true;
    clear();
    if (doc) doc.removeEventListener('visibilitychange', onVisibility);
    listenActivity(false);
  };
  const expire = () => {
    expired = true;
    clear();
    onExpire?.();
    if (!resumeOnVisible) stop();
    else listenActivity(true);
  };

  function fire() {
    timer = null;
    if (stopped || expired || running) return;
    // Nascosta: si aspetta il ritorno sulla scheda (onVisibility).
    if (hidden()) return;
    count();
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
        if (stopped) return;
        count();
        // Dopo l'ultima lettura: oltre la durata massima si scade.
        if (maxDurationMs > 0 && active >= maxDurationMs) { expire(); return; }
        schedule();
      });
  }

  function restart() {
    if (expired) {
      expired = false;
      active = 0;
      listenActivity(false);
    }
    mark = Date.now();
    wait = interval;
    clear();
    fire();
  }

  function onVisibility() {
    if (stopped || pageHidden()) return;
    restart();
  }

  function onActivity() {
    if (stopped || !expired || hidden()) return;
    restart();
  }

  if (pauseWhenHidden && doc) doc.addEventListener('visibilitychange', onVisibility);
  if (immediate) fire();
  else schedule();
  return stop;
}

export default startPoller;
