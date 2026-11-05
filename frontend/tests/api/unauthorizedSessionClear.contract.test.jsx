import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import http from '../../src/api/http';
import { AuthProvider } from '../../src/context/AuthContext';
import { authBus } from '../../src/utils/authBus';
import { currentDraftUser } from '../../src/utils/localDrafts';

/**
 * Fase 5, correzione dopo la conferma T1+T3 (punto 4).
 * REGOLA: lo storage della sessione si svuota in UN solo punto,
 * AuthContext.logout(), DOPO authBus.emitBeforeLogout(). Il test passa
 * dall'interceptor REALE di api/http (adapter finto che risponde 401): chi
 * mette al sicuro la copia locale nel beforeLogout vede ancora l'utente,
 * quindi la copia del 401 non finisce sotto 'nouser'.
 */
const srcRoot = path.resolve(import.meta.dirname, '../../src');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectSources(full);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [full] : [];
  });
}

describe('401 dall\'interceptor reale: la copia di sicurezza vede ancora l\'utente', () => {
  let originalAdapter;
  beforeEach(() => {
    sessionStorage.clear();
    globalThis.__frontendConsolePolicy.allowError = true;
    originalAdapter = http.defaults.adapter;
    // Ogni richiesta (anche GET /auth/me alla ripresa) risponde 401.
    http.defaults.adapter = (config) => Promise.reject(Object.assign(new Error('Request failed with status code 401'), {
      isAxiosError: true,
      config,
      response: { status: 401, data: { error: 'auth_token_invalid', message: 'Token invalido o scaduto' }, headers: {}, config },
    }));
  });
  afterEach(() => { http.defaults.adapter = originalAdapter; });

  it('beforeLogout legge l\'utente, poi logout svuota lo storage una volta', async () => {
    sessionStorage.setItem('urby_token', 'tok-scaduto');
    sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 'u-42', username: 'anna', role: 'user' }));
    const seen = [];
    const off = authBus.addBeforeLogoutListener(() => seen.push(currentDraftUser()));
    try {
      render(<MemoryRouter><AuthProvider><div /></AuthProvider></MemoryRouter>);
      // Una seconda richiesta parallela con lo stesso token: niente doppio logout.
      await Promise.allSettled([http.get('/projects'), http.get('/files')]);
      await waitFor(() => expect(sessionStorage.getItem('urby_token')).toBeNull());
      expect(seen).toEqual(['u-42']);
      expect(sessionStorage.getItem('urby_user')).toBeNull();
    } finally {
      off();
    }
  });
});

describe('lo storage della sessione si svuota solo in AuthContext.logout()', () => {
  it('nessun altro file toglie urby_token / urby_user', () => {
    const offenders = [];
    for (const file of collectSources(srcRoot)) {
      const content = fs.readFileSync(file, 'utf-8');
      if (/removeItem\(\s*(TOKEN_KEY|USER_KEY|['"]urby_(token|user)['"])/.test(content) || /sessionStorage\.clear\(/.test(content)) {
        offenders.push(path.relative(srcRoot, file).split(path.sep).join('/'));
      }
    }
    expect(offenders).toEqual(['context/AuthContext.jsx']);
    const auth = fs.readFileSync(path.join(srcRoot, 'context/AuthContext.jsx'), 'utf-8');
    const body = auth.slice(auth.indexOf('const logout = useCallback'));
    expect(body.indexOf('emitBeforeLogout')).toBeGreaterThan(-1);
    expect(body.indexOf('emitBeforeLogout')).toBeLessThan(body.indexOf('removeItem(TOKEN_KEY)'));
    expect(auth.match(/removeItem\(TOKEN_KEY\)/g)).toHaveLength(1);
  });
});
