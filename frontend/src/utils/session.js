/**
 * session.js — fine sessione unica (Fase 5 T1, audit R5.4, R5.5, R1-N4, R5-N3).
 *
 * Ogni modulo che tiene dati dell'utente in memoria (store zustand, cache di
 * modulo) registra QUI come svuotarli; il logout (anche quello del 401)
 * chiama una sola funzione, resetSession(), che li svuota tutti.
 *
 * Il test tests/shared/sessionReset.contract.test.js enumera lo stato di
 * modulo di src/ (let di modulo, Map/Set vuote, store zustand) e verifica che
 * ogni file o sia registrato qui o abbia un motivo scritto per non esserlo.
 *
 * I moduli si registrano da soli quando vengono caricati: un modulo non
 * ancora caricato (pagina lazy mai aperta) non ha dati da svuotare.
 */
const resetters = new Map();

/** Registra lo svuotamento di un modulo. Lo stesso nome sostituisce il precedente. */
export function registerSessionReset(name, reset) {
  resetters.set(name, reset);
}

/** Nomi registrati (per i test e la diagnostica). */
export function registeredSessionResets() {
  return [...resetters.keys()];
}

/** Svuota tutti i dati dell'utente in memoria. Un errore non ferma gli altri. */
export function resetSession() {
  for (const [name, reset] of resetters) {
    try {
      reset();
    } catch (err) {
      console.error(`[session] svuotamento di "${name}" non riuscito:`, err);
    }
  }
}
