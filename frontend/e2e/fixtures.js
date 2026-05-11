// Fixture di test condiviso: `authedPage` = pagina con sessionStorage
// gia' popolato con un JWT admin valido.
//
// Necessario perche' AuthContext (src/context/AuthContext.jsx) usa
// `sessionStorage`, che NON viene serializzato da Playwright via
// storageState (e' tab-scoped per design del browser). La via pulita
// e' chiamare /api/auth/login una volta per worker e poi iniettare
// chiave/valore con addInitScript prima di ogni page.goto.

import { test as base, request as pwRequest } from '@playwright/test';

const API = process.env.PLENORA_API_URL || 'http://localhost:5004';
const ADMIN_USER = process.env.PLENORA_TEST_USER || 'admin_plenora';
const ADMIN_PASS = process.env.PLENORA_TEST_PASS || 'admin_plenora_2026';

// Chiavi sessionStorage usate da AuthContext. Devono restare allineate.
const TOKEN_KEY = 'urby_token';
const USER_KEY = 'urby_user';

/**
 * @typedef {{
 *   access_token: string,
 *   user: { user_id: string, username: string, group_id: string, role: string }
 * }} AuthSession
 */

/**
 * Login via API, ritorna token + user. Worker-scoped: la sessione e'
 * riusata da tutti i test dello stesso worker per evitare hash bcrypt
 * ripetuti.
 * @returns {Promise<AuthSession>}
 */
async function fetchAdminSession() {
  const api = await pwRequest.newContext({ baseURL: API });
  try {
    const resp = await api.post('/api/auth/login', {
      data: { username: ADMIN_USER, password: ADMIN_PASS },
    });
    if (!resp.ok()) {
      throw new Error(
        `login admin fallito ${resp.status()}: ${await resp.text()}`,
      );
    }
    const data = await resp.json();
    return {
      access_token: data.access_token,
      user: {
        user_id: data.user_id,
        username: data.username,
        group_id: data.group_id,
        role: data.role,
      },
    };
  } finally {
    await api.dispose();
  }
}

export const test = base.extend({
  // Worker-scoped: una sola chiamata di login per worker.
  adminSession: [
    async ({}, use) => {
      const session = await fetchAdminSession();
      await use(session);
    },
    { scope: 'worker' },
  ],

  // Test-scoped: per ogni test inietta il token in sessionStorage
  // prima della prima navigazione. NOTE: addInitScript viene eseguito
  // dopo che la nuova page e' creata ma prima di qualunque script
  // della pagina, quindi AuthProvider trova subito i valori al mount.
  page: async ({ page, adminSession }, use) => {
    await page.addInitScript(
      ({ token, user, tk, uk }) => {
        try {
          sessionStorage.setItem(tk, token);
          sessionStorage.setItem(uk, JSON.stringify(user));
        } catch {
          // sessionStorage puo' essere indisponibile in pagine about:blank
          // pre-navigazione; verra' riprovato al goto reale dato che
          // addInitScript persiste sulla page.
        }
      },
      {
        token: adminSession.access_token,
        user: adminSession.user,
        tk: TOKEN_KEY,
        uk: USER_KEY,
      },
    );
    await use(page);
  },
});

export { expect } from '@playwright/test';
export { ADMIN_USER, ADMIN_PASS, API };
