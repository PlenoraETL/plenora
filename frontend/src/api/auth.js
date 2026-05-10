/**
 * auth.js — API per login/logout JWT
 *
 * Lasciato fuori da questo modulo: tutto il flow MFA/utente/gruppi che
 * vive gia' in `api/users.js`. Qui solo i due endpoint del flow di
 * sessione.
 */

import http from './http';

/**
 * Login: ritorna il payload backend grezzo
 *   { access_token, user_id, username, group_id, role }
 * Il chiamante (AuthContext) si occupa di salvare token + user in
 * sessionStorage.
 *
 * @param {string} username
 * @param {string} password
 * @param {string|null} mfaCode
 * @returns {Promise<Object>}
 */
export const login = async (username, password, mfaCode = null) => {
  const payload = { username, password };
  if (mfaCode) payload.mfa_code = mfaCode;
  const { data } = await http.post('/auth/login', payload);
  return data;
};

/**
 * Logout best-effort: blacklista il jti del token lato backend.
 *
 * **Non e' `async`**: ritorna il Promise grezzo di `http.post`. Cosi' il
 * caller puo' fare fire-and-forget (`logout(t).catch(() => {})`) senza
 * attendere la rete prima di clearare lo stato locale.
 *
 * Il token va passato come Authorization header esplicito perche' il
 * caller puo' aver gia' rimosso il token da sessionStorage al momento
 * della chiamata (race con il clear locale).
 *
 * @param {string} token - JWT da revocare
 * @returns {Promise} - axios Promise; ignorabile con .catch
 */
export const logout = (token) => {
  return http.post('/auth/logout', null, {
    headers: { Authorization: `Bearer ${token}` },
  });
};
