/**
 * passwordRule.js — la regola unica della password (Fase 5 T1, R5-N1/N2).
 *
 * Uguale al backend: almeno 12 caratteri per cambio password
 * (core/auth/auth.py), creazione utente e reset (core/auth/users_router.py).
 * Un test legge il backend e verifica che il numero coincida
 * (tests/shared/passwordRule.contract.test.js); nessun altro file del
 * frontend controlla la lunghezza di una password.
 */
export const PASSWORD_MIN_LENGTH = 12;

/** Messaggio del problema della password, o null se va bene. */
export function passwordProblem(value) {
  const text = typeof value === 'string' ? value : '';
  if (!text.trim()) return 'Password obbligatoria';
  if (text.length < PASSWORD_MIN_LENGTH) {
    return `La password deve essere lunga almeno ${PASSWORD_MIN_LENGTH} caratteri`;
  }
  return null;
}
