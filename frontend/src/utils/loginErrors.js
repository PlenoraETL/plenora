/**
 * loginErrors.js — il testo dell'errore di accesso dipende dal CODICE del
 * backend (Fase 5, correzione dopo la conferma T1+T3).
 *
 * Il login non passa dal percorso di uscita della sessione (api/http): un
 * 401 qui non e' "sessione scaduta" ma un esito dell'accesso. Tabella
 * codice -> testo (backend app/core/auth/disclosure.py):
 *
 * | codice                    | stato | testo                                   |
 * |---------------------------|-------|-----------------------------------------|
 * | auth_invalid_credentials  | 401   | credenziali errate                      |
 * | auth_mfa_invalid          | 401   | codice MFA non valido                   |
 * | auth_mfa_required         | 403   | chiede il codice MFA (gestito a parte)  |
 * | auth_account_disabled     | 403   | account disabilitato                    |
 * | auth_rate_limit_exceeded  | 429   | il messaggio del backend (con l'attesa) |
 *
 * Un altro codice (o nessuno) segue la regola unica di utils/errorMessages,
 * che non mostra mai un codice come testo; un 401 senza codice noto resta
 * "credenziali errate".
 */
import { getErrorCode, humanizeError } from './errorMessages';

const MFA_REQUIRED_CODE = 'auth_mfa_required';

export const LOGIN_ERROR_TEXT = Object.freeze({
  auth_invalid_credentials: 'Credenziali errate: username o password non corretti.',
  auth_mfa_invalid: 'Codice MFA non valido: controlla il codice dell’app di autenticazione e riprova.',
  auth_account_disabled: 'Account disabilitato: contatta l’amministratore.',
});

const codeOf = (err) => getErrorCode(err);

/** True se il backend chiede il codice MFA per completare l'accesso. */
export const isMfaRequired = (err) => codeOf(err) === MFA_REQUIRED_CODE;

/** Testo da mostrare per un accesso non riuscito. */
export function loginErrorMessage(err) {
  const code = codeOf(err);
  if (code && LOGIN_ERROR_TEXT[code]) return LOGIN_ERROR_TEXT[code];
  if (err?.response?.status === 401) return LOGIN_ERROR_TEXT.auth_invalid_credentials;
  return humanizeError(err).message;
}
