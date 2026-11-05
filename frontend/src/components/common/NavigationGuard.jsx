/**
 * NavigationGuard — l'UNICO blocker di navigazione dell'app (Fase 5, T4,
 * R7.1). Montato una volta dentro il data router (App.jsx).
 *
 * Intercetta ogni cambio di pagina (link, navigate(), tasto Indietro/Avanti
 * del browser, Ctrl+K) con `useBlocker` e, se un editor ha modifiche non
 * salvate (regola unica: hooks/useUnsavedChangesGuard.shouldBlockNavigation),
 * chiede con il dialog del progetto (LEAVE_DIALOG). Esci = la navigazione
 * prosegue; Resta = annullata (anche il tasto Indietro torna dov'era).
 */
import { useCallback, useEffect, useRef } from 'react';
import { useBlocker } from 'react-router-dom';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { LEAVE_DIALOG, shouldBlockNavigation } from '../../hooks/useUnsavedChangesGuard';

export default function NavigationGuard() {
  const confirm = useConfirmOrDeny();
  const blocker = useBlocker(useCallback((args) => shouldBlockNavigation(args), []));
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    let live = true;
    Promise.resolve(confirm(LEAVE_DIALOG)).then((ok) => {
      const current = blockerRef.current;
      if (!live || current.state !== 'blocked') return;
      if (ok) current.proceed();
      else current.reset();
    });
    // eslint-disable-next-line consistent-return
    return () => { live = false; };
  }, [blocker.state, confirm]);

  return null;
}
