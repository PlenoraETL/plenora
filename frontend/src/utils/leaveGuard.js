/**
 * leaveGuard.js — la regola delle uscite con modifiche non salvate, SENZA
 * lo store del canvas (UI-4, V1).
 *
 * Il guard di navigazione (components/common/NavigationGuard) e l'uscita
 * volontaria (hooks/useSignOut) sono nel chunk iniziale: se importassero lo
 * store del canvas, @xyflow arriverebbe gia' su /login e sulle pagine di
 * sistema. Qui c'e' solo il registro degli editor montati: ogni editor
 * (hooks/useUnsavedChangesGuard, che lo store lo importa) registra la sua
 * domanda «ci sono modifiche non salvate?» e la toglie smontandosi. Senza
 * editor montati non c'e' niente da perdere.
 *
 * UN SOLO REGISTRO di «cosa si perde uscendo» (conferma finale G8): anche le
 * finestre degli Eseguibili (components/eseguibili/useEseguibileRun:
 * esecuzione in corso, risultato non scaricato) registrano qui la loro
 * domanda, con il loro dialog. Da questo registro leggono tutti: il guard di
 * navigazione e l'uscita volontaria (`leaveDialog()` = il dialog della prima
 * domanda che ha qualcosa da perdere), la ricarica dopo un deploy
 * (utils/chunkReload) e il prompt nativo di chiusura della scheda, che e'
 * UNO, installato qui finche' c'e' almeno una domanda (prima ogni hook
 * aveva il suo `beforeunload`).
 */

/** Il dialog di OGNI uscita con modifiche non salvate. */
export const LEAVE_DIALOG = {
  title: 'Modifiche non salvate',
  message: 'Ci sono modifiche non salvate. Se esci ora restano nella copia di sicurezza di questo browser '
    + 'e potrai ripristinarle riaprendo il progetto, ma non sono sul server: '
    + 'non le vede nessun altro e non vengono eseguite.',
  confirmText: 'Esci senza salvare',
  cancelText: "Resta nell'editor",
  variant: 'warning',
};

// Domande montate (una per montaggio) -> il loro dialog.
const probes = new Map();

/*
 * Uscita gia' confermata (UI conferma C-K1): l'utente ha risposto «Esci
 * senza salvare» per gli editor montati ADESSO. Vale finche' cambia l'insieme
 * degli editor (uno nuovo montato, o uno smontato a navigazione finita): una
 * risposta non copre l'editor successivo. La leggono la ricarica dopo un
 * deploy (utils/chunkReload) e il prompt nativo di beforeunload
 * (hooks/useUnsavedChangesGuard), che quindi non chiede due volte.
 */
let leaveConfirmed = false;

/*
 * Prompt nativo alla chiusura o ricarica della scheda (i browser non
 * permettono un dialog proprio): uno solo, per tutte le domande.
 */
function onBeforeUnload(event) {
  if (!mustAskBeforeLeaving()) return undefined;
  event.preventDefault();
  // I browser moderni mostrano un testo loro; `returnValue` attiva il prompt.
  event.returnValue = '';
  return '';
}

/**
 * Registra qualcosa che si perderebbe uscendo (un editor montato, una
 * finestra di esecuzione); restituisce la funzione che lo toglie.
 * @param {() => boolean} hasSomething
 * @param {object} [dialog=LEAVE_DIALOG] il dialog della domanda
 */
export function registerLeaveProbe(hasSomething, dialog = LEAVE_DIALOG) {
  if (probes.size === 0 && typeof window !== 'undefined') window.addEventListener('beforeunload', onBeforeUnload);
  probes.set(hasSomething, dialog);
  leaveConfirmed = false;
  return () => {
    if (!probes.delete(hasSomething)) return;
    leaveConfirmed = false;
    if (probes.size === 0 && typeof window !== 'undefined') window.removeEventListener('beforeunload', onBeforeUnload);
  };
}

export const isLeaveGuardActive = () => probes.size > 0;

const probeAtRisk = () => [...probes.entries()].find(([p]) => p());

/** Regola UNICA: c'e' qualcosa da perdere uscendo? */
const hasSomethingToLose = () => Boolean(probeAtRisk());

/** Il dialog della prima domanda che ha qualcosa da perdere (o quello degli editor). */
export const leaveDialog = () => probeAtRisk()?.[1] || LEAVE_DIALOG;

/** L'utente ha confermato l'uscita dagli editor montati. */
export const markLeaveConfirmed = () => { leaveConfirmed = true; };
export const isLeaveConfirmed = () => leaveConfirmed;

/** Uscire ora farebbe perdere qualcosa senza che l'utente lo abbia confermato? */
export const mustAskBeforeLeaving = () => !leaveConfirmed && hasSomethingToLose();

/**
 * Regola del blocker: si chiede solo cambiando pagina (non per query o hash)
 * e solo con modifiche non salvate.
 */
export function shouldBlockNavigation({ currentLocation, nextLocation }) {
  if (!currentLocation || !nextLocation) return false;
  if (currentLocation.pathname === nextLocation.pathname) return false;
  // Verso /login si va solo a sessione chiusa (uscita volontaria gia'
  // confermata da hooks/useSignOut, o 401): nessuna seconda domanda.
  if (nextLocation.pathname === '/login') return false;
  return hasSomethingToLose();
}

/**
 * Chiede (dialog del progetto) se c'e' qualcosa da perdere.
 * @param {(cfg: object) => Promise<boolean>} confirm
 * @returns {Promise<boolean>} true = si puo' uscire
 */
export async function confirmLeave(confirm) {
  if (!hasSomethingToLose()) return true;
  const ok = Boolean(await confirm(leaveDialog()));
  if (ok) markLeaveConfirmed();
  return ok;
}
