/**
 * navigation — piccole regole di navigazione condivise (Fase 5, T4, R7.5).
 */

/**
 * "Indietro" dentro l'app: torna alla pagina precedente SOLO se c'e' una
 * pagina precedente di questa app (react-router tiene l'indice in
 * `history.state.idx`); aperto da un link diretto va a `fallback` invece di
 * uscire dall'app.
 */
export function goBack(navigate, fallback = '/') {
  const idx = typeof window !== 'undefined' ? window.history?.state?.idx : undefined;
  if (typeof idx === 'number' && idx > 0) navigate(-1);
  else navigate(fallback, { replace: true });
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
