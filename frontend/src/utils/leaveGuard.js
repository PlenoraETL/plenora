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

// Domande degli editor montati (una per montaggio).
const probes = new Set();

/*
 * Uscita gia' confermata (UI conferma C-K1): l'utente ha risposto «Esci
 * senza salvare» per gli editor montati ADESSO. Vale finche' cambia l'insieme
 * degli editor (uno nuovo montato, o uno smontato a navigazione finita): una
 * risposta non copre l'editor successivo. La leggono la ricarica dopo un
 * deploy (utils/chunkReload) e il prompt nativo di beforeunload
 * (hooks/useUnsavedChangesGuard), che quindi non chiede due volte.
 */
let leaveConfirmed = false;

/** Registra un editor montato; restituisce la funzione che lo toglie. */
export function registerLeaveProbe(hasUnsaved) {
  probes.add(hasUnsaved);
  leaveConfirmed = false;
  return () => { probes.delete(hasUnsaved); leaveConfirmed = false; };
}

export const isLeaveGuardActive = () => probes.size > 0;

/** Regola UNICA: c'e' qualcosa da perdere uscendo dall'editor? */
const hasSomethingToLose = () => [...probes].some(p => p());

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
  const ok = Boolean(await confirm(LEAVE_DIALOG));
  if (ok) markLeaveConfirmed();
  return ok;
}
