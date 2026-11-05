import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LOGIN_ERROR_TEXT, isMfaRequired, loginErrorMessage } from '../../src/utils/loginErrors';
import { backendMessage, humanizeError, looksLikeCode } from '../../src/utils/errorMessages';

/**
 * Fase 5, correzione dopo la conferma T1+T3 (punto 6).
 *  - Il testo dell'errore di accesso dipende dal codice del backend
 *    (tabella LOGIN_ERROR_TEXT), non dal solo stato 401.
 *  - Un "messaggio" del backend che e' un codice (snake_case senza spazi,
 *    es. 'mfa_invalid') non e' un messaggio per l'utente: mai mostrato.
 */
const http = (status, data) => ({ isAxiosError: true, response: { status, data } });
// Corpi reali del backend (app/core/auth/disclosure.py + envelope).
const BODY = {
  auth_invalid_credentials: { error: 'auth_invalid_credentials', message: 'Username o password errati' },
  auth_mfa_invalid: { error: 'auth_mfa_invalid', message: 'mfa_invalid' },
  auth_mfa_required: { error: 'auth_mfa_required', message: 'mfa_required' },
  auth_account_disabled: { error: 'auth_account_disabled', message: 'Account disabilitato' },
};

describe('errori di accesso: testo dal codice del backend', () => {
  it('ogni codice della tabella ha il suo testo, anche sullo stesso 401', () => {
    expect(loginErrorMessage(http(401, BODY.auth_invalid_credentials))).toBe(LOGIN_ERROR_TEXT.auth_invalid_credentials);
    expect(loginErrorMessage(http(401, BODY.auth_mfa_invalid))).toBe(LOGIN_ERROR_TEXT.auth_mfa_invalid);
    expect(loginErrorMessage(http(401, BODY.auth_mfa_invalid))).toMatch(/Codice MFA non valido/);
    expect(loginErrorMessage(http(403, BODY.auth_account_disabled))).toBe(LOGIN_ERROR_TEXT.auth_account_disabled);
  });

  it('MFA richiesto: dal codice, non dal testo', () => {
    expect(isMfaRequired(http(403, BODY.auth_mfa_required))).toBe(true);
    expect(isMfaRequired(http(403, { message: 'mfa_required' }))).toBe(false);
    expect(isMfaRequired(http(401, BODY.auth_mfa_invalid))).toBe(false);
  });

  it('troppi tentativi: il messaggio del backend con l\'attesa; 401 senza codice: credenziali', () => {
    expect(loginErrorMessage(http(429, { error: 'auth_rate_limit_exceeded', message: 'Troppi tentativi. Riprova tra 30 secondi.' })))
      .toBe('Troppi tentativi. Riprova tra 30 secondi.');
    expect(loginErrorMessage(http(401, {}))).toBe(LOGIN_ERROR_TEXT.auth_invalid_credentials);
  });

  it('i codici della tabella sono codici del backend', () => {
    const backend = readFileSync(resolve(import.meta.dirname, '../../../backend/app/shared/error_codes.py'), 'utf-8');
    for (const code of Object.keys(LOGIN_ERROR_TEXT)) expect(backend).toContain(`"${code}"`);
  });

  it('LoginPage usa la tabella e non decide il testo dallo stato', () => {
    const page = readFileSync(resolve(import.meta.dirname, '../../src/pages/LoginPage.jsx'), 'utf-8');
    expect(page).toMatch(/loginErrorMessage\(err\)/);
    expect(page).not.toMatch(/status === 401/);
  });
});

describe('un codice non e\' un messaggio per l\'utente', () => {
  it('looksLikeCode riconosce i codici snake_case e non le frasi', () => {
    for (const v of ['mfa_invalid', 'mfa_required', 'auth_mfa_invalid', 'carica_rest_auth_unavailable']) expect(looksLikeCode(v)).toBe(true);
    for (const v of ['Account disabilitato', 'Username o password errati', 'errore', 'Errore_x', 'a b_c']) expect(looksLikeCode(v)).toBe(false);
  });

  it('backendMessage e humanizeError non mostrano mai il letterale', () => {
    expect(backendMessage({ message: 'mfa_invalid' })).toBeNull();
    expect(backendMessage({ detail: 'mfa_required' })).toBeNull();
    const h = humanizeError(http(401, BODY.auth_mfa_invalid));
    expect(h.message).not.toMatch(/mfa_invalid/);
    expect(h.message).toMatch(/MFA/);
  });
});
