/**
 * Testo da mostrare per un task fallito (esecuzione, anteprima, Carica).
 *
 * Il backend mette il motivo leggibile in `message` (o nel risultato) e in
 * `error` un codice o un ripiego generico ("Errore sconosciuto"). Regola
 * unica: il primo testo UTILE tra dettaglio del risultato, messaggio del
 * task ed errore; i ripieghi generici non contano (collaudo M7: la finestra
 * diceva "Errore sconosciuto" mentre il task spiegava quale Formula non
 * era valida).
 */
const GENERIC = new Set(['errore sconosciuto', 'errore', 'unknown error', 'error']);

const usable = (value) => typeof value === 'string'
  && value.trim() !== ''
  && !GENERIC.has(value.trim().toLowerCase());

export function taskFailureMessage(task, fallback = 'Errore durante l\'esecuzione') {
  return [task?.result?.error_message, task?.result?.message, task?.message, task?.error]
    .find(usable) || fallback;
}

export default taskFailureMessage;
