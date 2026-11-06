/**
 * NavigationGuard — l'UNICO blocker di navigazione dell'app (Fase 5, T4,
 * R7.1). Montato una volta dentro il data router (App.jsx).
 *
 * Intercetta ogni cambio di pagina (link, navigate(), tasto Indietro/Avanti
 * del browser, Ctrl+K) con `useBlocker` e, se un editor ha modifiche non
 * salvate (regola unica: utils/leaveGuard.shouldBlockNavigation),
 * chiede con il dialog del progetto (LEAVE_DIALOG). Esci = la navigazione
 * prosegue; Resta = annullata (anche il tasto Indietro torna dov'era).
 */
import { useCallback, useEffect, useRef } from 'react';
import { useBlocker } from 'react-router-dom';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { leaveDialog, markLeaveConfirmed, shouldBlockNavigation } from '../../utils/leaveGuard';

export default function NavigationGuard() {
  const confirm = useConfirmOrDeny();
  const blocker = useBlocker(useCallback((args) => shouldBlockNavigation(args), []));
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    let live = true;
    Promise.resolve(confirm(leaveDialog())).then((ok) => {
      const current = blockerRef.current;
      if (!live || current.state !== 'blocked') return;
      // Uscita confermata: la ricarica dopo un deploy (utils/chunkReload) e il
      // prompt di beforeunload non chiedono di nuovo (UI conferma C-K1).
      if (ok) { markLeaveConfirmed(); current.proceed(); } else current.reset();
    });
    // eslint-disable-next-line consistent-return
    return () => { live = false; };
  }, [blocker.state, confirm]);

  return null;
}
