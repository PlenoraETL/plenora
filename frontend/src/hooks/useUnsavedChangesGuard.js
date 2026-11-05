/**
 * useUnsavedChangesGuard.js — guard UNICO contro la perdita di modifiche non
 * salvate (Fase 5, T4: R7.1, nota CommandPalette).
 *
 * Una sola regola, `hasUnsavedChanges` (store/saveState), per ogni uscita
 * dall'editor:
 *   - link interni, tasto Indietro/Avanti del browser, Ctrl+K (CommandPalette)
 *     e ogni `navigate()`: `useBlocker` di react-router nel NavigationGuard
 *     montato una volta nell'app (components/common/NavigationGuard), con il
 *     dialog del progetto. Gli editor navigano e basta: chiede il guard;
 *   - logout (che svuota la sessione PRIMA di navigare): `confirmNavigation()`
 *     con lo stesso dialog, poi logout;
 *   - ricarica e chiusura della scheda: `beforeunload`, il prompt del browser
 *     (unica eccezione: i browser non permettono un dialog proprio).
 * Il guard e' attivo solo mentre un editor e' montato (questo hook lo
 * registra). Il dialog ricorda che le modifiche restano nella copia di
 * sicurezza di questo browser (hooks/useLocalDraft) e si ritrovano riaprendo
 * il progetto.
 */
import { useCallback, useEffect } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { useConfirmOrDeny } from '../context/ConfirmContext';

export const UNSAVED_MESSAGE = 'Hai modifiche non salvate. Vuoi davvero uscire?';

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

// Editor montati (il guard di navigazione agisce solo con un editor aperto).
let activeEditors = 0;

export const isLeaveGuardActive = () => activeEditors > 0;

/** Regola UNICA: c'e' qualcosa da perdere uscendo dall'editor? */
export const hasSomethingToLose = () => (
  isLeaveGuardActive() && hasUnsavedChanges(useCanvasStore.getState())
);

/**
 * Regola del blocker: si chiede solo cambiando pagina (non per query o hash)
 * e solo con modifiche non salvate.
 */
export function shouldBlockNavigation({ currentLocation, nextLocation }) {
  if (!currentLocation || !nextLocation) return false;
  if (currentLocation.pathname === nextLocation.pathname) return false;
  return hasSomethingToLose();
}

/**
 * Chiede (dialog del progetto) se c'e' qualcosa da perdere.
 * @param {(cfg: object) => Promise<boolean>} confirm
 * @returns {Promise<boolean>} true = si puo' uscire
 */
export async function confirmLeave(confirm) {
  if (!hasSomethingToLose()) return true;
  return Boolean(await confirm(LEAVE_DIALOG));
}

export function useUnsavedChangesGuard() {
  const confirm = useConfirmOrDeny();

  useEffect(() => {
    activeEditors += 1;
    const handler = (event) => {
      if (!hasUnsavedChanges(useCanvasStore.getState())) return undefined;
      event.preventDefault();
      // I browser moderni ignorano la stringa custom (mostrano un messaggio
      // generico) ma `returnValue` resta il modo standard per attivare il
      // prompt nativo cross-browser.
      event.returnValue = UNSAVED_MESSAGE;
      return UNSAVED_MESSAGE;
    };
    window.addEventListener('beforeunload', handler);
    return () => {
      activeEditors = Math.max(0, activeEditors - 1);
      window.removeEventListener('beforeunload', handler);
    };
  }, []);

  /** Per le uscite che non sono una navigazione (logout). */
  const confirmNavigation = useCallback(() => confirmLeave(confirm), [confirm]);

  return { confirmNavigation };
}

export default useUnsavedChangesGuard;
