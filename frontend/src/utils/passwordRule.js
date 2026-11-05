/**
 * passwordRule.js — la regola unica della password (Fase 5 T1, R5-N1/N2).
 *
 * Uguale al backend: almeno 12 caratteri per cambio password
 * (core/auth/auth.py), creazione utente e reset (core/auth/users_router.py).
 * Un test legge il backend e verifica che il numero coincida
 * (tests/shared/passwordRule.contract.test.js); nessun altro file del
 * frontend controlla la lunghezza di una password.
 *
 * Stessa misura del backend (`len(password)` di Python): la lunghezza si
 * conta in CODE POINT (Array.from), non in unita' UTF-16 (`.length` conta
 * due volte un'emoji). Il backend non tratta gli spazi in modo speciale: una
 * password di soli spazi vale come le altre (corta se sotto il minimo).
 */
export const PASSWORD_MIN_LENGTH = 12;

/** Lunghezza come Python len(): code point. */
const passwordLength = (value) => Array.from(typeof value === 'string' ? value : '').length;

/** Messaggio del problema della password, o null se va bene. */
export function passwordProblem(value) {
  const length = passwordLength(value);
  if (length === 0) return 'Password obbligatoria';
  if (length < PASSWORD_MIN_LENGTH) {
    return `La password deve essere lunga almeno ${PASSWORD_MIN_LENGTH} caratteri`;
  }
  return null;
}
