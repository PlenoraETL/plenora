/**
 * timezones.js — l'unico elenco dei fusi orari (Fase 5 T5, R8.13, N13).
 *
 * L'elenco e' quello IANA del browser (`Intl.supportedValuesOf('timeZone')`,
 * gli stessi nomi che il backend passa a pytz), con in testa i fusi piu' usati.
 * Un fuso salvato che non e' in elenco (impostato via API, o un browser con
 * un elenco piu' corto) si mostra COM'E', marcato «non in elenco»: quello che
 * si vede e' quello che si salva (prima si vedeva il primo valore e si
 * salvava l'originale).
 */

export const DEFAULT_TIMEZONE = 'Europe/Rome';

const PREFERRED_TIMEZONES = Object.freeze(['Europe/Rome', 'UTC', 'Europe/London']);

const LABELS = Object.freeze({
  'Europe/Rome': 'Europe/Rome (CET/CEST)',
  UTC: 'UTC',
});

function browserZones() {
  try {
    if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
      return Intl.supportedValuesOf('timeZone');
    }
  } catch {
    // elenco non disponibile: restano i preferiti
  }
  return [];
}

/** Tutti i fusi offerti: preferiti in testa, poi gli altri in ordine. */
export const TIMEZONES = Object.freeze([
  ...PREFERRED_TIMEZONES,
  ...browserZones().filter(z => !PREFERRED_TIMEZONES.includes(z)).sort(),
]);

export const isKnownTimezone = (value) => TIMEZONES.includes(value);

/**
 * Opzioni del select: l'elenco unico, piu' il valore attuale se non c'e'
 * (mostrato com'e', in testa, con la nota).
 */
export function timezoneOptions(current) {
  const options = TIMEZONES.map(value => ({ value, label: LABELS[value] || value }));
  if (current && !isKnownTimezone(current)) {
    return [{ value: current, label: `${current} (non in elenco)`, unknown: true }, ...options];
  }
  return options;
}
