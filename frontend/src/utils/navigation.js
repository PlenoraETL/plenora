/**
 * navigation — piccole regole di navigazione condivise (Fase 5, T4, R7.5).
 */
import { authBus } from './authBus';

/**
 * Lo `state` del rimando a /login (regola unica, punto 2 della correzione
 * dopo la conferma di T2+T4): la pagina richiesta (`from`) si ricorda SOLO
 * dopo un 401 (sessione scaduta) o per una pagina chiesta senza sessione;
 * MAI dopo un'uscita volontaria (il prossimo utente non torna sulla pagina
 * di chi e' uscito).
 */
export function loginRedirectState(location) {
  if (authBus.exitReason() === 'logout') return undefined;
  if (!location?.pathname || location.pathname.startsWith('/login')) return undefined;
  const { pathname, search = '', hash = '' } = location;
  return { from: { pathname, search, hash } };
}


/**
 * Dove tornare dopo il login: la pagina richiesta prima del rimando a /login
 * (`location.state.from`), se l'utente puo' vederla; altrimenti `landing`.
 */
export function returnPathAfterLogin(from, landing, { esecutore = false } = {}) {
  const path = typeof from === 'string' ? from : (from?.pathname ? `${from.pathname}${from.search || ''}${from.hash || ''}` : null);
  if (!path || !path.startsWith('/') || path.startsWith('//') || path.startsWith('/login')) return landing;
  // L'esecutore vede solo /run (ProtectedRoute lo rimanderebbe comunque).
  if (esecutore && !path.startsWith('/run')) return landing;
  return path;
}
