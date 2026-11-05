/**
 * useSignOut — l'UNICA uscita volontaria dalla sessione (Fase 5, correzione
 * dopo la conferma di T2+T4, punti 2 e 6). Ogni pulsante «Esci» (barra
 * dell'app, sidebar degli editor, kiosk dell'esecutore) chiama questa.
 *
 *  1. con modifiche non salvate in un editor chiede UNA volta (stesso dialog
 *     del guard di navigazione, hooks/useUnsavedChangesGuard.confirmLeave);
 *  2. AuthContext.logout(): motivo 'logout' (il rimando a /login NON ricorda
 *     la pagina: utils/navigation.loginRedirectState), sessione svuotata
 *     (anche il canvas: niente da perdere, il guard non richiede);
 *  3. /login senza `state.from`: al login successivo si va alla home.
 */
import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useConfirmOrDeny } from '../context/ConfirmContext';
import { confirmLeave } from './useUnsavedChangesGuard';

export function useSignOut() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const confirm = useConfirmOrDeny();
  return useCallback(async () => {
    if (!(await confirmLeave(confirm))) return false;
    logout();
    navigate('/login', { replace: true });
    return true;
  }, [logout, navigate, confirm]);
}

export default useSignOut;
