/**
 * cron.js — la regola unica dell'espressione cron (Fase 5 T5, R8.13).
 *
 * Stessa regola del backend: lo scheduler accetta un cron solo se ha
 * ESATTAMENTE 5 campi e `croniter.is_valid` lo accetta
 * (backend app/core/scheduler/cron_schedule.py, CronSchedule). Qui la stessa
 * grammatica di croniter per i 5 campi; dove croniter e' piu' largo (per
 * esempio `1-L`) il frontend e' piu' stretto e lo dichiara nella tabella dei
 * casi (`tests/fixtures/cronCases.json`, campo `stricter`). Un test del
 * backend fa girare croniter sugli stessi casi
 * (backend/tests2/shared/test_cron_rule_matches_frontend.py).
 *
 * | campo            | valori                  | speciali (da soli)      |
 * |------------------|-------------------------|-------------------------|
 * | minuto           | 0-59                    |                         |
 * | ora              | 0-23                    |                         |
 * | giorno del mese  | 1-31                    | `L`, `?`, `nW`          |
 * | mese             | 1-12, jan-dec           |                         |
 * | giorno settimana | 0-7 (0 e 7 = domenica), | `?`, `n#k` (k 1-5)      |
 * |                  | sun-sat                 |                         |
 *
 * Ogni campo: elenco separato da virgole di `*`, `v` o `a-b`, con `/passo`
 * facoltativo (passo intero >= 1).
 */
import { wallClockIn } from './format';

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

const CRON_FIELDS = Object.freeze([
  { name: 'minuto', min: 0, max: 59 },
  { name: 'ora', min: 0, max: 23 },
  { name: 'giorno del mese', min: 1, max: 31 },
  { name: 'mese', min: 1, max: 12, names: MONTHS, namesFrom: 1 },
  { name: 'giorno della settimana', min: 0, max: 7, names: DAYS, namesFrom: 0 },
]);

function valueOf(token, field) {
  if (/^\d+$/.test(token)) {
    const n = Number(token);
    return n >= field.min && n <= field.max ? n : null;
  }
  if (field.names) {
    const i = field.names.indexOf(token.toLowerCase());
    if (i !== -1) return i + field.namesFrom;
  }
  return null;
}

function itemValid(item, field) {
  const [base, step, extra] = item.split('/');
  if (extra !== undefined) return false;
  if (step !== undefined && !(/^\d+$/.test(step) && Number(step) >= 1)) return false;
  if (base === '*') return true;
  const bounds = base.split('-');
  if (bounds.length > 2) return false;
  return bounds.every(b => b !== '' && valueOf(b, field) !== null);
}

function fieldValid(text, index) {
  const field = CRON_FIELDS[index];
  if (index === 2 && (text === 'L' || text === '?')) return true;
  if (index === 2 && /^\d+W$/i.test(text)) return valueOf(text.slice(0, -1), field) !== null;
  if (index === 4 && text === '?') return true;
  if (index === 4 && text.includes('#')) {
    const [day, nth] = text.split('#');
    return valueOf(day, field) !== null && /^[1-5]$/.test(nth || '');
  }
  const items = text.split(',');
  return items.every(item => item !== '' && itemValid(item, field));
}

/** Problema dell'espressione cron, o null se lo scheduler la accetta. */
export function cronProblem(expression) {
  const text = String(expression ?? '').trim();
  if (!text) return 'Espressione cron richiesta';
  const parts = text.split(/\s+/);
  if (parts.length !== 5) {
    return `Servono 5 campi (minuto ora giorno mese giorno_settimana), ne hai scritti ${parts.length}`;
  }
  const bad = parts.findIndex((part, i) => !fieldValid(part, i));
  if (bad !== -1) return `Campo «${CRON_FIELDS[bad].name}» non valido: ${parts[bad]}`;
  return null;
}


/** Forma normalizzata (spazi singoli), quella che si salva. */
export const normalizeCron = (expression) => String(expression ?? '').trim().split(/\s+/).join(' ');

/*
 * ── Lettura in parole e prossime esecuzioni (UI-4, U31) ──────────────────
 *
 * Calcolo locale, con la stessa grammatica di sopra (un'espressione che
 * cronProblem rifiuta non ha descrizione ne' esecuzioni). Semantica come
 * croniter e il cron classico: con giorno del mese E giorno della settimana
 * entrambi ristretti basta uno dei due; `?` vale `*`; un intervallo
 * rovesciato (`30-10`, `FEB-JAN`) gira oltre il massimo; 7 = domenica.
 * Gli orari sono quelli del fuso della pianificazione (ora locale di quel
 * fuso; nei cambi d'ora un orario che non esiste non si corregge).
 */

const DAY_NAMES = ['domenica', 'lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato'];
const MONTH_NAMES = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto',
  'settembre', 'ottobre', 'novembre', 'dicembre'];
const ORDINALS = ['', 'primo', 'secondo', 'terzo', 'quarto', 'quinto'];

/** Valori di un campo (Set di interi) da un elenco `*`, `v`, `a-b`, con `/passo`. */
function expandField(text, field) {
  const dow = field === CRON_FIELDS[4];
  const size = field.max - field.min + 1;
  const out = new Set();
  for (const item of text.split(',')) {
    const [base, stepText] = item.split('/');
    const step = stepText === undefined ? 1 : Number(stepText);
    let from; let to;
    if (base === '*' || base === '?') { from = field.min; to = field.max; }
    else {
      const [a, b] = base.split('-');
      from = valueOf(a, field);
      // `5/15` = da 5 fino al massimo, ogni 15.
      to = b !== undefined ? valueOf(b, field) : (stepText !== undefined ? field.max : from);
    }
    const span = to >= from ? to - from : to - from + size;
    for (let i = 0; i <= span; i += step) {
      const v = field.min + ((from - field.min + i) % size);
      out.add(dow && v === 7 ? 0 : v);
    }
  }
  return out;
}

/** Espressione letta: per campo i valori, o la regola speciale del giorno. */
function parseCron(expression) {
  if (cronProblem(expression)) return null;
  const [min, hour, dom, month, dow] = normalizeCron(expression).split(' ');
  const parsed = {
    minutes: expandField(min, CRON_FIELDS[0]),
    hours: expandField(hour, CRON_FIELDS[1]),
    months: expandField(month, CRON_FIELDS[3]),
    raw: { min, hour, dom, month, dow },
  };
  if (dom === 'L') parsed.dom = { last: true };
  else if (/W$/i.test(dom)) parsed.dom = { nearestWeekday: Number(dom.slice(0, -1)) };
  else parsed.dom = { values: expandField(dom, CRON_FIELDS[2]) };
  if (dow.includes('#')) {
    const [d, k] = dow.split('#');
    parsed.dow = { nth: Number(k), day: valueOf(d, CRON_FIELDS[4]) % 7 };
  } else parsed.dow = { values: expandField(dow, CRON_FIELDS[4]) };
  parsed.domAny = dom === '*' || dom === '?';
  parsed.dowAny = dow === '*' || dow === '?';
  return parsed;
}

const daysInMonth = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate(); // m 1-12

function domMatches(spec, y, m, d) {
  if (spec.last) return d === daysInMonth(y, m);
  if (spec.nearestWeekday != null) {
    const last = daysInMonth(y, m);
    let target = Math.min(spec.nearestWeekday, last);
    const wd = new Date(Date.UTC(y, m - 1, target)).getUTCDay();
    if (wd === 6) target = target === 1 ? 3 : target - 1;
    else if (wd === 0) target = target === last ? target - 2 : target + 1;
    return d === target;
  }
  return spec.values.has(d);
}

function dowMatches(spec, y, m, d) {
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  if (spec.nth) return wd === spec.day && Math.ceil(d / 7) === spec.nth;
  return spec.values.has(wd);
}

function dayMatches(p, y, m, d) {
  if (!p.months.has(m)) return false;
  if (p.domAny && p.dowAny) return true;
  if (p.domAny) return dowMatches(p.dow, y, m, d);
  if (p.dowAny) return domMatches(p.dom, y, m, d);
  return domMatches(p.dom, y, m, d) || dowMatches(p.dow, y, m, d);
}

// Quanto avanti si cerca (un'espressione come «31 febbraio» non capita mai).
const SEARCH_DAYS = 366 * 5;

/**
 * Prossime esecuzioni dopo `from`, come ora locale del fuso:
 * [{ y, m, d, h, min, weekday }] (vuoto se l'espressione non e' valida o
 * non capita nei prossimi anni).
 */
export function nextCronRuns(expression, { from = new Date(), count = 5, timeZone } = {}) {
  const p = parseCron(expression);
  if (!p) return [];
  const now = wallClockIn(from, timeZone);
  const hours = [...p.hours].sort((a, b) => a - b);
  const minutes = [...p.minutes].sort((a, b) => a - b);
  const out = [];
  const start = Date.UTC(now.y, now.m - 1, now.d);
  for (let i = 0; i < SEARCH_DAYS && out.length < count; i += 1) {
    const day = new Date(start + i * 86400000);
    const y = day.getUTCFullYear(); const m = day.getUTCMonth() + 1; const d = day.getUTCDate();
    if (!dayMatches(p, y, m, d)) continue;
    for (const h of hours) {
      for (const min of minutes) {
        if (i === 0 && (h < now.h || (h === now.h && min <= now.min))) continue;
        out.push({ y, m, d, h, min, weekday: day.getUTCDay() });
        if (out.length === count) return out;
      }
    }
  }
  return out;
}

const pad = (n) => String(n).padStart(2, '0');

/** Un'esecuzione di nextCronRuns in parole: «venerdì 7 novembre 2026, 03:00». */
export const formatCronRun = (r) => `${DAY_NAMES[r.weekday]} ${r.d} ${MONTH_NAMES[r.m - 1]} ${r.y}, ${pad(r.h)}:${pad(r.min)}`;

const listIt = (items) => (items.length <= 1 ? items.join('')
  : `${items.slice(0, -1).join(', ')} e ${items[items.length - 1]}`);

/** Valori in parole, con gli intervalli consecutivi uniti («dal lunedì al venerdì»). */
function runsOf(values, name, { from = 'dal', to = 'al' } = {}) {
  const v = [...values].sort((a, b) => a - b);
  const groups = [];
  for (const x of v) {
    const g = groups[groups.length - 1];
    if (g && x === g[1] + 1) g[1] = x; else groups.push([x, x]);
  }
  return listIt(groups.map(([a, b]) => (b - a >= 2 ? `${from} ${name(a)} ${to} ${name(b)}` : (a === b ? name(a) : `${name(a)} e ${name(b)}`))));
}

function timeText(p) {
  const { min, hour } = p.raw;
  const hours = [...p.hours].sort((a, b) => a - b);
  const minutes = [...p.minutes].sort((a, b) => a - b);
  const stepOf = (t) => (/^\*\/(\d+)$/.test(t) ? Number(t.split('/')[1]) : null);
  if (min === '*' && hour === '*') return 'ogni minuto';
  if (hour === '*') {
    const s = stepOf(min);
    if (s) return s === 1 ? 'ogni minuto' : `ogni ${s} minuti`;
    if (minutes.length === 1) return minutes[0] === 0 ? 'ogni ora, allo scoccare' : `ogni ora, al minuto ${minutes[0]}`;
    return `ogni ora, ai minuti ${listIt(minutes.map(String))}`;
  }
  if (minutes.length === 1 && hours.length <= 6) return `alle ${listIt(hours.map(h => `${pad(h)}:${pad(minutes[0])}`))}`;
  const hs = stepOf(hour);
  const hourPart = hs ? `ogni ${hs} ore` : `nelle ore ${runsOf(hours, String, { from: 'dalle', to: 'alle' })}`;
  if (minutes.length === 1) return `${hourPart}, al minuto ${minutes[0]}`;
  const ms = stepOf(min);
  return `${ms ? `ogni ${ms} minuti` : `ai minuti ${listIt(minutes.map(String))}`}, ${hourPart}`;
}

function dayText(p) {
  const domPart = () => {
    if (p.dom.last) return "l'ultimo giorno del mese";
    if (p.dom.nearestWeekday != null) return `il giorno feriale più vicino al ${p.dom.nearestWeekday} del mese`;
    const v = [...p.dom.values];
    return v.length === 31 ? 'ogni giorno' : `il giorno ${runsOf(v, String)} del mese`;
  };
  const dowPart = () => {
    if (p.dow.nth) return `il ${ORDINALS[p.dow.nth]} ${DAY_NAMES[p.dow.day]} del mese`;
    const v = [...p.dow.values];
    if (v.length === 7) return 'ogni giorno';
    const t = runsOf(v, (d) => DAY_NAMES[d]);
    return t.startsWith('dal ') ? t : `di ${t}`;
  };
  if (p.domAny && p.dowAny) return 'ogni giorno';
  if (p.domAny) return dowPart();
  if (p.dowAny) return domPart();
  return `${domPart()} oppure ${dowPart()}`;
}

/** L'espressione in parole («ogni giorno alle 03:00»), o null se non valida. */
export function describeCron(expression) {
  const p = parseCron(expression);
  if (!p) return null;
  const months = p.months.size === 12 ? ''
    : `, ${p.months.size === 1 ? 'solo a ' : 'nei mesi '}${runsOf(p.months, (m) => MONTH_NAMES[m - 1], { from: 'da', to: 'a' })}`;
  const text = `${dayText(p)}, ${timeText(p)}${months}`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}
