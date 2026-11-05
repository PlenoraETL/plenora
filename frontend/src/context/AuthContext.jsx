/**
 * AuthContext.jsx — Contesto React per autenticazione JWT
 *
 * Fornisce a tutta l'app: user, token, login(), logout().
 * Il token viene letto/scritto in sessionStorage per persistere tra refresh.
 * Registra authBus.onUnauthorized per ricevere gli eventi 401 da axios.
 *
 * Uso: const { user, token, login, logout } = useAuth();
 */

import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { authBus } from '../utils/authBus';
import { resetSession } from '../utils/session';
import { loginRedirectState } from '../utils/navigation';
import { login as apiLogin, logout as apiLogout } from '../api/auth';
import { getMe } from '../api/users';

const AuthContext = createContext(null);
const TOKEN_KEY = 'urby_token';
const USER_KEY  = 'urby_user';

// Leggi token/user salvati (persistenza tra refresh pagina)
function loadFromStorage() {
  try {
    const token = sessionStorage.getItem(TOKEN_KEY);
    const user  = JSON.parse(sessionStorage.getItem(USER_KEY) || 'null');
    return { token, user };
  } catch {
    return { token: null, user: null };
  }
}

export function AuthProvider({ children }) {
  // R5.9: lo storage si legge una volta sola, al primo render.
  const [token, setToken] = useState(() => loadFromStorage().token);
  const [user,  setUser]  = useState(() => loadFromStorage().user);
  const navigate = useNavigate();

  // UNICO punto che svuota lo storage della sessione (anche sul 401:
  // api/http non lo tocca), e solo DOPO emitBeforeLogout.
  // `{ expired: true }` = uscita per 401: il token e' gia' scaduto, niente revoca.
  const logout = useCallback((options) => {
    const expired = options?.expired === true;
    // Il motivo dell'uscita decide se il rimando a /login ricorda la pagina
    // (utils/navigation.loginRedirectState): mai dopo il logout volontario.
    authBus.markExit(expired ? 'expired' : 'logout');
    // Prima di tutto (anche sul 401): la copia di sicurezza in sospeso si
    // scrive mentre l'utente e' ancora in sessione. La copia resta.
    authBus.emitBeforeLogout();
    // Best-effort: notifica il backend per blacklistare il jti del
    // token. La chiamata e' fire-and-forget perche':
    //   - L'UX deve essere immediata (no spinner su logout).
    //   - Se la rete e' giu' o il backend e' irraggiungibile, il clear
    //     locale (sessionStorage + state) deve comunque avvenire.
    //   - Se il token e' gia' scaduto (401), il client ha comunque gia'
    //     finito il suo lavoro: l'interceptor 401 salta /auth/logout
    //     apposta per non innescare un loop logout→401→logout.
    const currentToken = sessionStorage.getItem(TOKEN_KEY);
    if (currentToken && !expired) {
      try {
        const p = apiLogout(currentToken);
        if (p && typeof p.then === 'function') {
          p.catch(() => {});
        }
      } catch {
        // mock / interceptor sincrono — ignora, procedi al clear locale.
      }
    }
    setToken(null);
    setUser(null);
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    // Svuota TUTTI gli store e le cache dell'utente (registro unico,
    // utils/session): canvas, colonne, registry delle trasformazioni, ...
    resetSession();
  }, []);

  // Registra handler 401 — client.js chiama authBus.onUnauthorized()
  // invece di fare window.location.href (bypassa React Router)
  useEffect(() => {
    authBus.onUnauthorized = () => {
      logout({ expired: true });
      // R7.5: dopo il nuovo login si torna qui (sessione scaduta).
      navigate('/login', { replace: true, state: loginRedirectState(window.location) });
    };
    return () => { authBus.onUnauthorized = null; };
  }, [logout, navigate]);

  const login = useCallback(async (username, password, mfaCode = null) => {
    // Nota: http non ha ancora il token in questo momento,
    // ma l'interceptor lo aggiunge solo se presente — quindi va bene.
    const data = await apiLogin(username, password, mfaCode);

    const userData = {
      user_id:  data.user_id,
      username: data.username,
      group_id: data.group_id,
      role:     data.role,
    };

    authBus.clearExit();
    setToken(data.access_token);
    setUser(userData);
    sessionStorage.setItem(TOKEN_KEY, data.access_token);
    sessionStorage.setItem(USER_KEY,  JSON.stringify(userData));

    return userData;
  }, []);

  // R5.3: ruolo e gruppo vengono dal server (GET /auth/me), non solo dalla
  // copia in sessionStorage: alla ripresa della scheda (ricarica, ritorno sulla
  // finestra) il profilo si riallinea, quindi un admin declassato perde le
  // rotte /admin senza aspettare la scadenza del token. Un 401 qui chiude la
  // sessione nell'interceptor come per ogni altra richiesta.
  useEffect(() => {
    const refresh = () => {
      const sent = sessionStorage.getItem(TOKEN_KEY);
      if (!sent) return;
      Promise.resolve()
        .then(() => getMe())
        .then((me) => {
          // Risposta di una sessione che non c'e' piu': si ignora.
          if (!me || sessionStorage.getItem(TOKEN_KEY) !== sent) return;
          setUser((prev) => {
            if (!prev) return prev;
            const fields = ['username', 'group_id', 'role'];
            const next = { ...prev, ...Object.fromEntries(fields.map((k) => [k, me[k] ?? prev[k]])) };
            if (fields.every((k) => next[k] === prev[k])) return prev;
            sessionStorage.setItem(USER_KEY, JSON.stringify(next));
            return next;
          });
        })
        .catch(() => { /* rete o 401: gestiti altrove, il profilo resta */ });
    };
    refresh();
    const onVisible = () => { if (document.visibilityState === 'visible') refresh(); };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  // R5.9: il value cambia solo quando cambia la sessione.
  const value = useMemo(() => ({ user, token, login, logout }), [user, token, login, logout]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth deve essere dentro AuthProvider');
  return ctx;
}
