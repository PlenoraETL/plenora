/**
 * Contract del cron in parole e delle prossime esecuzioni (UI-4, U31), per
 * enumerazione sulla tabella dei casi della regola unica
 * (tests/fixtures/cronCases.json, la stessa che il backend fa girare su
 * croniter).
 *
 *  1. Ogni espressione valida ha una descrizione in italiano (senza i
 *     simboli del cron) e le sue prossime esecuzioni; ogni espressione
 *     rifiutata da cronProblem non ha ne' l'una ne' le altre.
 *  2. Le prossime esecuzioni sono quelle di un controllo minuto per minuto
 *     scritto qui a parte (oracolo indipendente), crescenti e dopo l'ora di
 *     partenza, nel fuso della pianificazione.
 *  3. Casi in parole per le forme comuni (preimpostazioni del wizard).
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { cronProblem, describeCron, nextCronRuns, formatCronRun } from '../../src/utils/cron';

const { cases } = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/cronCases.json'), 'utf-8'));
const VALID = cases.filter(c => c.valid).map(c => c.expr);
const INVALID = cases.filter(c => !c.valid).map(c => c.expr);
// Venerdì 6 novembre 2026, 10:17 a Roma (09:17 UTC).
const FROM = new Date(Date.UTC(2026, 10, 6, 9, 17, 30));
const TZ = 'Europe/Rome';

// ── Oracolo: un minuto alla volta, regole del cron scritte da capo ────────
const NAMES = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
const num = (t) => (t.toLowerCase() in NAMES ? NAMES[t.toLowerCase()] : Number(t));
function fieldHas(text, value, lo, hi) {
  return text.split(',').some((item) => {
    const [base, stepText] = item.split('/');
    const step = Number(stepText || 1);
    const [a, b] = base === '*' || base === '?' ? [lo, hi] : base.split('-').map(num);
    const end = b === undefined ? (stepText ? hi : a) : b;
    const seq = [];
    if (end >= a) for (let v = a; v <= end; v += 1) seq.push(v);
    else { for (let v = a; v <= hi; v += 1) seq.push(v); for (let v = lo; v <= end; v += 1) seq.push(v); }
    return seq.filter((_, i) => i % step === 0).some(v => v === value || (hi === 7 && v === 7 && value === 0));
  });
}
const lastDay = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();
function oracleDay(dom, dow, y, m, d) {
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  const domAny = dom === '*' || dom === '?';
  const dowAny = dow === '*' || dow === '?';
  const domOk = () => {
    if (dom === 'L') return d === lastDay(y, m);
    if (/W$/.test(dom)) {
      // Il feriale piu' vicino al giorno n, nello stesso mese.
      const n = Math.min(Number(dom.slice(0, -1)), lastDay(y, m));
      const cands = [n, n - 1, n + 1, n - 2, n + 2].filter(x => x >= 1 && x <= lastDay(y, m));
      const wdOf = (x) => new Date(Date.UTC(y, m - 1, x)).getUTCDay();
      const best = cands.find(x => wdOf(x) !== 0 && wdOf(x) !== 6);
      return d === best;
    }
    return fieldHas(dom, d, 1, 31);
  };
  const dowOk = () => {
    if (dow.includes('#')) { const [a, k] = dow.split('#'); return wd === num(a) % 7 && Math.ceil(d / 7) === Number(k); }
    return fieldHas(dow, wd, 0, 7);
  };
  if (domAny && dowAny) return true;
  if (domAny) return dowOk();
  if (dowAny) return domOk();
  return domOk() || dowOk();
}
function oracle(expr, count, days) {
  const [mi, h, dom, mo, dow] = expr.trim().split(/\s+/);
  const out = [];
  // Ora di Roma a novembre 2026 = UTC+1: partenza 10:17.
  const start = Date.UTC(2026, 10, 6, 10, 18);
  for (let t = start; t < start + days * 86400000 && out.length < count; t += 60000) {
    const x = new Date(t);
    const [y, m, d, hh, mm] = [x.getUTCFullYear(), x.getUTCMonth() + 1, x.getUTCDate(), x.getUTCHours(), x.getUTCMinutes()];
    if (fieldHas(mi, mm, 0, 59) && fieldHas(h, hh, 0, 23) && fieldHas(mo, m, 1, 12) && oracleDay(dom, dow, y, m, d)) {
      out.push(`${y}-${m}-${d} ${hh}:${mm}`);
    }
  }
  return out;
}
const key = (r) => `${r.y}-${r.m}-${r.d} ${r.h}:${r.min}`;

describe('cron in parole e prossime esecuzioni: ogni caso della regola unica', () => {
  it('la tabella dei casi e\' letta', () => {
    expect(VALID.length).toBeGreaterThan(40);
    expect(INVALID.length).toBeGreaterThan(20);
  });

  it.each(VALID)('valida «%s»: descrizione in parole', (expr) => {
    expect(cronProblem(expr)).toBeNull();
    const text = describeCron(expr);
    expect(typeof text).toBe('string');
    // Niente simboli del cron nel testo per l'utente.
    expect({ expr, text, symbols: /[*?#/]|\bL\b(?!')|\d+W\b/.test(text) }).toMatchObject({ expr, symbols: false });
  });

  it.each(VALID)('valida «%s»: prossime 5 esecuzioni come l\'oracolo', (expr) => {
    const runs = nextCronRuns(expr, { from: FROM, count: 5, timeZone: TZ });
    // L'oracolo guarda 400 giorni: si confrontano le esecuzioni in quella finestra.
    const horizon = Date.UTC(2026, 10, 6) + 400 * 86400000;
    const inWindow = runs.filter(r => Date.UTC(r.y, r.m - 1, r.d) < horizon);
    expect({ expr, runs: inWindow.map(key) }).toEqual({ expr, runs: oracle(expr, 5, 400) });
    expect(runs.length).toBe(expr.includes('31 2') ? 0 : 5);
    runs.forEach(r => expect(formatCronRun(r)).toMatch(/^\p{L}+ \d{1,2} \p{L}+ \d{4}, \d{2}:\d{2}$/u));
  });

  it.each(INVALID)('rifiutata «%s»: nessuna descrizione ne\' esecuzione', (expr) => {
    expect(cronProblem(expr)).not.toBeNull();
    expect(describeCron(expr)).toBeNull();
    expect(nextCronRuns(expr, { from: FROM })).toEqual([]);
  });
});

describe('forme comuni in parole', () => {
  const WORDS = {
    '*/15 * * * *': 'Ogni giorno, ogni 15 minuti',
    '0 * * * *': 'Ogni giorno, ogni ora, allo scoccare',
    '0 3 * * *': 'Ogni giorno, alle 03:00',
    '0 0 * * 1': 'Di lunedì, alle 00:00',
    '0 8 * * mon-fri': 'Dal lunedì al venerdì, alle 08:00',
    '0 0 1 * *': 'Il giorno 1 del mese, alle 00:00',
    '0 0 L * *': "L'ultimo giorno del mese, alle 00:00",
    '0 0 * * 1#2': 'Il secondo lunedì del mese, alle 00:00',
    '30 6 * jan *': 'Ogni giorno, alle 06:30, solo a gennaio',
    '0 0 31 2 *': 'Il giorno 31 del mese, alle 00:00, solo a febbraio',
  };
  it.each(Object.entries(WORDS))('%s', (expr, words) => {
    expect(describeCron(expr)).toBe(words);
  });

  it('un\'espressione che non capita mai non ha esecuzioni', () => {
    expect(nextCronRuns('0 0 31 2 *', { from: FROM })).toEqual([]);
  });

  it('le esecuzioni sono nell\'ora del fuso della pianificazione', () => {
    const rome = nextCronRuns('0 3 * * *', { from: FROM, count: 1, timeZone: TZ })[0];
    const tokyo = nextCronRuns('0 3 * * *', { from: FROM, count: 1, timeZone: 'Asia/Tokyo' })[0];
    expect(formatCronRun(rome)).toBe('sabato 7 novembre 2026, 03:00');
    // A Tokyo sono gia' le 18:17 del 6: la prossima e' comunque il 7 alle 03:00.
    expect(formatCronRun(tokyo)).toBe('sabato 7 novembre 2026, 03:00');
    const roma10 = nextCronRuns('30 10 * * *', { from: FROM, count: 1, timeZone: TZ })[0];
    expect(formatCronRun(roma10)).toBe('venerdì 6 novembre 2026, 10:30');
  });
});
