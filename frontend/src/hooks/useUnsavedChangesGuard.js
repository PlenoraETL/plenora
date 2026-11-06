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
 *   - logout (che svuota la sessione PRIMA di navigare): l'unica uscita
 *     volontaria, hooks/useSignOut, chiede con lo stesso dialog UNA volta e
 *     poi va a /login (che il blocker non ferma mai);
 *   - ricarica e chiusura della scheda: `beforeunload`, il prompt del browser
 *     (unica eccezione: i browser non permettono un dialog proprio), UNO per
 *     tutta l'app in utils/leaveGuard (conferma finale G8).
 * Il guard e' attivo solo mentre un editor e' montato (questo hook lo
 * registra). Il dialog ricorda che le modifiche restano nella copia di
 * sicurezza di questo browser (hooks/useLocalDraft) e si ritrovano riaprendo
 * il progetto.
 */
import { useCallback, useEffect } from 'react';
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { useConfirmOrDeny } from '../context/ConfirmContext';
import { confirmLeave, registerLeaveProbe } from '../utils/leaveGuard';

// La regola e il dialog vivono in utils/leaveGuard (senza lo store del
// canvas, UI-4 V1): qui l'editor montato registra la sua domanda.

const editorHasUnsaved = () => hasUnsavedChanges(useCanvasStore.getState());

export function useUnsavedChangesGuard() {
  const confirm = useConfirmOrDeny();

  useEffect(() => {
    // Una domanda per montaggio (closure nuova: due editor non si tolgono a
    // vicenda). Il prompt di chiusura della scheda e' quello unico del
    // registro, che rispetta un'uscita gia' confermata (UI conferma C-K1).
    const unregister = registerLeaveProbe(() => editorHasUnsaved());
    return unregister;
  }, []);

  /** Per le uscite che non sono una navigazione (logout). */
  const confirmNavigation = useCallback(() => confirmLeave(confirm), [confirm]);

  return { confirmNavigation };
}

export default useUnsavedChangesGuard;
