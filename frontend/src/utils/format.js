/**
 * format — i formattatori unici dell'interfaccia (Fase 5 T7, audit R11, G5).
 *
 * Regole:
 *  - ORARI DEL SERVER: il backend scrive in UTC. Un ISO con fuso (`Z`,
 *    `+00:00`) si legge com'è; un ISO SENZA fuso (log, colonne naive) è UTC,
 *    mai un'ora locale; il timestamp delle esecuzioni degli schedule
 *    (`YYYYMMDD_HHMMSS`) è UTC. Si MOSTRA sempre nel fuso del browser, con
 *    l'anno; `zone: true` aggiunge la sigla del fuso (pagine tecniche).
 *  - Una data sola (`YYYY-MM-DD`) è un giorno del calendario, non un istante.
 *  - Dimensioni: un solo `formatBytes` (B, KB, MB, GB, TB; base 1024).
 *  - Numeri: separatore delle migliaia italiano (`formatNumber`).
 *  - Valori enumerati: etichetta italiana da una tabella (`enumLabel`), il
 *    codice grezzo non si mostra se la tabella lo conosce.
 *
 * Nessun altro file chiama toLocaleString/toLocaleDateString/
 * toLocaleTimeString/Intl.* o scrive un formattatore di dimensioni:
 * tests/shared/formatters.contract.test.js lo vieta.
 */

const LOCALE = 'it-IT';
const EMPTY = '—';

const ISO_NO_ZONE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;
const ISO_DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
const RUN_STAMP = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/;

/**
 * Istante del server come Date (o null). Numeri = millisecondi epoch.
 * @param {string|number|Date|null|undefined} value
 * @returns {Date|null}
 */
export function parseServerDate(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value : null;
  if (typeof value === 'number') return Number.isFinite(value) ? new Date(value) : null;
  const text = String(value).trim();
  let m = RUN_STAMP.exec(text);
  if (m) {
    const [, y, mo, d, h, mi, s] = m.map(Number);
    return new Date(Date.UTC(y, mo - 1, d, h, mi, s));
  }
  m = ISO_DATE_ONLY.exec(text);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  m = ISO_NO_ZONE.exec(text);
  const iso = m ? `${m[1]}T${m[2]}Z` : text;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t) : null;
}

const cache = new Map();
function fmt(kind, options) {
  const key = `${kind}:${JSON.stringify(options)}`;
  if (!cache.has(key)) {
    cache.set(key, kind === 'number'
      ? new Intl.NumberFormat(LOCALE, options)
      : new Intl.DateTimeFormat(LOCALE, options));
  }
  return cache.get(key);
}

// Un valore che non e' un istante leggibile si mostra com'e' (mai nascosto).
const unreadable = (value) => (value === null || value === undefined || value === '' ? EMPTY : String(value));

const DATE_PARTS = { day: '2-digit', month: '2-digit', year: 'numeric' };

/**
 * Data e ora nel fuso del browser: «05/11/2026, 14:03».
 * @param {*} value istante del server
 * @param {{seconds?: boolean, zone?: boolean}} [opts]
 */
export function formatDateTime(value, { seconds = false, zone = false } = {}) {
  const d = parseServerDate(value);
  if (!d) return unreadable(value);
  return fmt('date', {
    ...DATE_PARTS, hour: '2-digit', minute: '2-digit',
    ...(seconds ? { second: '2-digit' } : {}),
    ...(zone ? { timeZoneName: 'short' } : {}),
  }).format(d);
}


/** Giorno e mese nel fuso del browser: «07/11» (home per catene). */
export function formatDayMonth(value) {
  const d = parseServerDate(value);
  if (!d) return unreadable(value);
  return fmt('date', { day: '2-digit', month: '2-digit' }).format(d);
}


/** Solo l'ora: «14:03» (o «14:03:12»). */
export function formatTime(value, { seconds = false } = {}) {
  const d = parseServerDate(value);
  if (!d) return unreadable(value);
  return fmt('date', { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) }).format(d);
}


/** Millisecondi epoch di un istante del server (NaN se non leggibile). */
function serverTimeMs(value) {
  const d = parseServerDate(value);
  return d ? d.getTime() : NaN;
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

/**
 * Dimensione leggibile, base 1024: 512 B, 1,5 KB, 12,3 MB, 1,25 GB.
 * `from`: unità del valore ricevuto (il server dà a volte MB o GB).
 * @param {number|null|undefined} value
 * @param {{from?: 'B'|'KB'|'MB'|'GB'|'TB'}} [opts]
 */
export function formatBytes(value, { from = 'B' } = {}) {
  if (value === null || value === undefined || value === '') return EMPTY;
  const n = Number(value) * 1024 ** Math.max(0, UNITS.indexOf(from));
  if (!Number.isFinite(n) || n < 0) return EMPTY;
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i += 1; }
  const digits = i === 0 ? 0 : (i >= 3 ? 2 : 1);
  return `${fmt('number', { minimumFractionDigits: 0, maximumFractionDigits: digits }).format(v)} ${UNITS[i]}`;
}

/**
 * Numero con separatore delle migliaia italiano. `null`/`undefined` → «—».
 * @param {number|null|undefined} n
 * @param {{maxDecimals?: number, grouping?: boolean}} [opts]
 */
export function formatNumber(n, { maxDecimals = 2, grouping = true } = {}) {
  if (n === null || n === undefined || n === '') return EMPTY;
  const v = Number(n);
  if (!Number.isFinite(v)) return String(n);
  return fmt('number', { maximumFractionDigits: maxDecimals, useGrouping: grouping }).format(v);
}

/** Durata in millisecondi: 850ms, 2,4s, 3m 12s, 1h 5m. */
export function formatDuration(ms) {
  if (ms === null || ms === undefined || ms === '' || !Number.isFinite(Number(ms))) return EMPTY;
  const v = Math.max(0, Number(ms));
  if (v < 1000) return `${Math.round(v)}ms`;
  if (v < 60000) return `${formatNumber(v / 1000, { maxDecimals: 1 })}s`;
  if (v < 3600000) return `${Math.floor(v / 60000)}m ${Math.round((v % 60000) / 1000)}s`;
  return `${Math.floor(v / 3600000)}h ${Math.floor((v % 3600000) / 60000)}m`;
}

/**
 * Durata in secondi (il backend la manda spesso cosi': elapsed_time,
 * elapsed_seconds): stessa forma di formatDuration (UI-3, U17). Il test
 * tests/shared/durations.contract.test.js vieta i secondi grezzi («12.3s»).
 */
export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || seconds === '' || !Number.isFinite(Number(seconds))) return EMPTY;
  return formatDuration(Number(seconds) * 1000);
}

/** Tempo trascorso da un istante del server fino a `now` (ms). */
export function formatElapsed(since, now = Date.now()) {
  const start = serverTimeMs(since);
  if (!Number.isFinite(start)) return null;
  const s = Math.max(0, Math.floor((now - start) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/**
 * Etichette italiane dei valori enumerati del backend. Un valore che la
 * tabella non conosce si mostra com'è (nuovo valore del server): mai vuoto.
 */
export const ENUM_LABELS = {
  // backend/app/core/tickets/service.py: TICKET_STATUSES, TICKET_SEVERITIES,
  // TICKET_KINDS (il test confronta le chiavi). Le categorie arrivano con
  // la loro etichetta da GET /tickets/meta: `optionLabel`.
  ticketStatus: {
    open: 'Aperto',
    in_progress: 'In lavorazione',
    waiting_user: 'Attesa utente',
    resolved: 'Risolto',
    closed: 'Chiuso',
    reopened: 'Riaperto',
  },
  ticketSeverity: {
    low: 'Bassa',
    medium: 'Media',
    high: 'Alta',
    critical: 'Critica',
  },
  ticketKind: {
    tech_debt: 'Debito tecnico',
    user_support: 'Supporto utente',
  },
  role: {
    superadmin: 'Superadmin',
    admin: 'Admin',
    user: 'Utente',
    viewer: 'Lettore',
    esecutore: 'Esecutore',
  },
};

/**
 * Etichetta di un valore enumerato.
 * @param {keyof ENUM_LABELS} table
 * @param {string|null|undefined} value
 */
export function enumLabel(table, value) {
  if (value === null || value === undefined || value === '') return EMPTY;
  const labels = ENUM_LABELS[table];
  if (!labels) throw new Error(`Tabella di etichette sconosciuta: ${table}`);
  return labels[value] ?? String(value);
}

/**
 * Etichetta da un elenco `{ value, label }` del server (es. categorie dei
 * ticket da /tickets/meta); senza elenco o valore sconosciuto: il valore.
 */
export function optionLabel(options, value) {
  if (value === null || value === undefined || value === '') return EMPTY;
  const hit = (options || []).find(o => o?.value === value);
  return hit?.label || String(value);
}

/**
 * Ora locale di un fuso come { y, m, d, h, min } (UI-4, U31: prossime
 * esecuzioni di un cron). Fuso non valido o assente = quello del browser.
 */
export function wallClockIn(date, timeZone) {
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: timeZone || undefined, year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
    }).formatToParts(date);
  } catch {
    return wallClockIn(date, undefined);
  }
  const get = (t) => Number(parts.find(p => p.type === t)?.value);
  return { y: get('year'), m: get('month'), d: get('day'), h: get('hour'), min: get('minute') };
}
