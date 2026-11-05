/**
 * connectionTestStatus — come si mostra l'esito del «Test connessione»
 * (Fase 5, voce S3).
 *
 * Il backend salva l'esito di ogni test di una connessione salvata
 * (app/shared/connection_tests/results.py) e lo allega agli elementi delle
 * liste in `last_test`: { success, message, tested_at (UTC), tested_by,
 * duration_ms, stale }. Una modifica salvata lo azzera; `stale` e' vero
 * oltre il limite dichiarato nel backend (STALE_AFTER, 30 giorni).
 *
 * Una sola regola per tutte le liste (connessioni database e ArcGIS, Data
 * Source, connettori API):
 *  - nessun esito          → «Mai verificata»
 *  - esito scaduto         → «Da riverificare» (l'ultimo esito nel dettaglio)
 *  - esito riuscito        → «Verificata il <data> da <utente>»
 *  - esito fallito         → «Ultimo test fallito: <motivo>»
 * Date e durate con utils/format.
 */
import { formatDateTime, formatDuration } from './format';

const CONNECTION_TEST_LABELS = Object.freeze({
  never: 'Mai verificata',
  stale: 'Da riverificare',
  okPrefix: 'Verificata il',
  failedPrefix: 'Ultimo test fallito:',
});

const SUCCESS_FALLBACK = 'Connessione riuscita';
const FAILURE_FALLBACK = 'Test non riuscito';

const by = (t) => (t.tested_by ? ` da ${t.tested_by}` : '');
const when = (t) => `il ${formatDateTime(t.tested_at)}${by(t)}`;
const duration = (t) => (t.duration_ms == null ? '' : ` (${formatDuration(t.duration_ms)})`);

/**
 * @param {object|null|undefined} lastTest `last_test` dell'elemento della lista
 * @returns {{tone: 'none'|'stale'|'ok'|'err', label: string, detail: string}}
 */
export function describeConnectionTest(lastTest) {
  if (!lastTest || !lastTest.tested_at) {
    return { tone: 'none', label: CONNECTION_TEST_LABELS.never, detail: 'Premi «Test» per verificarla' };
  }
  const outcome = lastTest.success ? 'riuscito' : `fallito: ${lastTest.message || FAILURE_FALLBACK}`;
  if (lastTest.stale) {
    return { tone: 'stale', label: CONNECTION_TEST_LABELS.stale, detail: `Ultimo test ${when(lastTest)}, ${outcome}` };
  }
  if (lastTest.success) {
    return {
      tone: 'ok',
      label: `${CONNECTION_TEST_LABELS.okPrefix} ${formatDateTime(lastTest.tested_at)}${by(lastTest)}`,
      detail: `${lastTest.message || SUCCESS_FALLBACK}${duration(lastTest)}`,
    };
  }
  return {
    tone: 'err',
    label: `${CONNECTION_TEST_LABELS.failedPrefix} ${lastTest.message || FAILURE_FALLBACK}`,
    detail: `Test ${when(lastTest)}${duration(lastTest)}`,
  };
}

/**
 * L'esito appena ricevuto, nella stessa forma di `last_test`, da mostrare
 * subito (la lista ricaricata porta poi quello salvato dal backend).
 * @param {{success?: boolean, message?: string, error?: string}} result risposta del test
 * @param {string|null|undefined} username utente corrente
 */
export function lastTestFromResult(result, username, now = new Date()) {
  const success = result?.success === true;
  return {
    success,
    message: result?.message || result?.error || (success ? SUCCESS_FALLBACK : FAILURE_FALLBACK),
    tested_at: now.toISOString(),
    tested_by: username || null,
    duration_ms: null,
    stale: false,
  };
}

/**
 * Elenco con l'esito appena ricevuto sull'elemento `id` (per resource.setData).
 * @template T
 * @param {T[]} items
 */
export function withLastTest(items, id, lastTest) {
  return (items || []).map((item) => (item?.id === id ? { ...item, last_test: lastTest } : item));
}
