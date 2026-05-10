/**
 * AuthContext.jsx — Contesto React per autenticazione JWT
 *
 * Fornisce a tutta l'app: user, token, login(), logout().
 * Il token viene letto/scritto in sessionStorage per persistere tra refresh.
 * Registra authBus.onUnauthorized per ricevere gli eventi 401 da axios.
 *
 * Uso: const { user, token, login, logout } = useAuth();
 *
 * C3.3 Phase 4: le chiamate a /auth/login e /auth/logout passano ora per
 * `api/auth.js`. Rimosso anche l'helper `authFetch` (wrapper di
 * `fetch()`) perche' non aveva consumer in src/ e violava il contratto
 * "src/api/ unico punto HTTP". Se in futuro serve un wrapper di fetch
 * con auth, esporlo da `api/`.
 */

import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { authBus } from '../utils/authBus';
import { login as apiLogin, logout as apiLogout } from '../api/auth';

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
  const { token: savedToken, user: savedUser } = loadFromStorage();
  const [token, setToken] = useState(savedToken);
  const [user,  setUser]  = useState(savedUser);
  const navigate = useNavigate();

  const logout = useCallback(() => {
    // Best-effort: notifica il backend per blacklistare il jti del
    // token. La chiamata e' fire-and-forget perche':
    //   - L'UX deve essere immediata (no spinner su logout).
    //   - Se la rete e' giu' o il backend e' irraggiungibile, il clear
    //     locale (sessionStorage + state) deve comunque avvenire.
    //   - Se il token e' gia' scaduto (401), il client ha comunque gia'
    //     finito il suo lavoro: l'interceptor 401 salta /auth/logout
    //     apposta per non innescare un loop logout→401→logout.
    const currentToken = sessionStorage.getItem(TOKEN_KEY);
    if (currentToken) {
      try {
        // apiLogout NON e' async: ritorna il Promise grezzo cosi' il
        // pattern fire-and-forget (no await) e' preservato bit-per-bit
        // dal comportamento pre-C3.3.
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
    // Notifica i moduli con cache in-memory (es. TransformNode.registryCache)
    authBus.emitLogout();
  }, []);

  // Registra handler 401 — client.js chiama authBus.onUnauthorized()
  // invece di fare window.location.href (bypassa React Router)
  useEffect(() => {
    authBus.onUnauthorized = () => {
      logout();
      navigate('/login', { replace: true });
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

    setToken(data.access_token);
    setUser(userData);
    sessionStorage.setItem(TOKEN_KEY, data.access_token);
    sessionStorage.setItem(USER_KEY,  JSON.stringify(userData));

    return userData;
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth deve essere dentro AuthProvider');
  return ctx;
}
