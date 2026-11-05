/**
 * typingAction — la digitazione continua DICHIARATA dal campo di testo
 * (Fase 5, T5+T6, correzione dopo la conferma: undo per azione).
 *
 * REGOLA (store/useCanvasStore, takeSnapshot): un Ctrl+Z annulla un'azione.
 *  - Si raggruppano SOLO:
 *    (a) le mutazioni dello stesso turno sincrono (un gesto composto: un
 *        clic che cambia tipo e collegamenti, il `perform` di una conferma);
 *    (b) una DIGITAZIONE: le mutazioni fatte mentre un campo di testo
 *        (input testuale, textarea, contenteditable) sta ricevendo il suo
 *        evento `input`/`change`, finche' quel campo ha il fuoco. La chiave
 *        e' «questo campo, in questa sessione di fuoco» + nodo e campi della
 *        patch: un altro campo, un altro nodo o un nuovo fuoco sono un'azione
 *        nuova.
 *  - Ogni azione DISCRETA (casella, scelta da un elenco, radio, pulsante,
 *    Applica, Invio) e' una sua azione, anche sullo stesso campo dei dati:
 *    il suo evento non e' la digitazione di un campo di testo.
 *  - Salva (anche Ctrl+S), cambio della selezione, apertura/chiusura di un
 *    pannello, Ctrl+Z/Ctrl+Y e i caricamenti chiudono qualunque gruppo.
 *
 * Un solo punto riconosce la digitazione: questo modulo ascolta (in cattura,
 * sul documento) gli eventi `input`/`change` e `focusin`; lo store chiede
 * `currentTypingKey()` durante la mutazione. La chiave vale solo mentre
 * l'evento e' in consegna (`eventPhase !== NONE`): una mutazione dopo un
 * `await` o un timer non e' digitazione.
 */

// Tipi di <input> che si scrivono a tasti (gli altri sono scelte discrete).
const TEXT_INPUT_TYPES = new Set(['', 'text', 'search', 'email', 'url', 'tel', 'password', 'number']);

/** true se l'elemento e' un campo di testo (si modifica digitando). */
function isTextField(el) {
  if (!el || el.nodeType !== 1) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'TEXTAREA') return true;
  if (tag !== 'INPUT') return false;
  return TEXT_INPUT_TYPES.has(String(el.getAttribute('type') || '').toLowerCase());
}

let seq = 0;
const sessions = new WeakMap();   // campo -> sessione di fuoco corrente
let current = null;               // { el, event } dell'ultimo input testuale

const sessionOf = (el) => {
  if (!sessions.has(el)) sessions.set(el, ++seq);
  return sessions.get(el);
};

function onFocusIn(event) {
  const el = event.target;
  if (isTextField(el)) sessions.set(el, ++seq);
}

function onTextEvent(event) {
  const el = event.target;
  current = isTextField(el) ? { el, event } : null;
}

/**
 * Chiave della digitazione in corso, o null se la mutazione non nasce dalla
 * digitazione di un campo di testo (azione discreta).
 */
export function currentTypingKey() {
  if (!current) return null;
  const { el, event } = current;
  if (event.eventPhase === 0 || !el.isConnected) { current = null; return null; }
  return `typing:${sessionOf(el)}`;
}

// Chi chiude la digitazione (lo store) e chi la fa chiudere (pannelli).
const closers = new Set();
/** Registra chi deve chiudere la digitazione in corso (lo store). */
export function onEndTyping(fn) {
  closers.add(fn);
  return () => closers.delete(fn);
}
/** Chiude la digitazione in corso: apertura/chiusura di un pannello. */
export function endTyping() {
  current = null;
  for (const fn of closers) fn();
}

let installedOn = null;
/** Installa gli ascoltatori (una volta per documento). */
function installTypingTracker(doc = typeof document !== 'undefined' ? document : null) {
  if (!doc || installedOn === doc) return;
  doc.addEventListener('focusin', onFocusIn, true);
  doc.addEventListener('input', onTextEvent, true);
  doc.addEventListener('change', onTextEvent, true);
  installedOn = doc;
}

installTypingTracker();
