/**
 * Contract del cron in parole e delle prossime esecuzioni (UI-4, U31), per
 * enumerazione sulla tabella dei casi della regola unica
 * (tests/fixtures/cronCases.json, la stessa che il backend fa girare su
 * croniter).
 *
 *  1. Ogni espressione valida ha una descrizione in italiano (senza i
 *     simboli del cron) e le sue prossime esecuzioni; ogni espressione
 *     rifiutata da cronProblem non ha ne' l'una ne' le altre.
 *  2. Le prossime esecuzioni sono quelle dello scheduler: per ogni caso
 *     valido, fuso e partenza (anche nei cambi d'ora) le stesse della
 *     fotografia di croniter (tests/fixtures/cronRunsGolden.json), crescenti
 *     e dopo la partenza (UI conferma C-K2).
 *  3. Casi in parole per le forme comuni (preimpostazioni del wizard).
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { cronProblem, describeCron, nextCronRuns, formatCronRun } from '../../src/utils/cron';

const { cases } = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/cronCases.json'), 'utf-8'));
// Le forme con avviso (conferma finale G6) sono valide ma senza descrizione
// ne' prossime esecuzioni: hanno il loro test (uiConfermaFinale.contract).
const VALID = cases.filter(c => c.valid && !c.warning).map(c => c.expr);
const INVALID = cases.filter(c => !c.valid).map(c => c.expr);
// Venerdì 6 novembre 2026, 10:17 a Roma (09:17 UTC).
const FROM = new Date(Date.UTC(2026, 10, 6, 9, 17, 30));
const TZ = 'Europe/Rome';

// ── Lo scheduler vero: fotografia di croniter (CronSchedule.next_runs) ───
// Generata da backend/tests2/scheduler/generate_cron_runs_golden.py; il
// backend verifica che sia ancora quella di croniter
// (tests2/scheduler/unit/test_cron_runs_golden_contract.py). Prima qui c'era
// un oracolo scritto a mano, che sbagliava come il codice (MON/2 = anche la
// domenica; nessun cambio d'ora): UI conferma C-K2.
const GOLDEN = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/cronRunsGolden.json'), 'utf-8'));
const pad = (n) => String(n).padStart(2, '0');
const wall = (r) => `${r.y}-${pad(r.m)}-${pad(r.d)} ${pad(r.h)}:${pad(r.min)}`;
const GOLDEN_CASES = Object.entries(GOLDEN.runs).flatMap(([expr, zones]) => Object.entries(zones)
  .flatMap(([zone, starts]) => Object.entries(starts).map(([start, runs]) => [expr, zone, start, runs])));

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

  it('la fotografia dello scheduler copre esattamente i casi validi', () => {
    expect(Object.keys(GOLDEN.runs).sort()).toEqual([...VALID].sort());
    expect(GOLDEN.zones).toEqual(expect.arrayContaining(['Europe/Rome', 'UTC', 'America/New_York']));
  });

  it.each(GOLDEN_CASES)('«%s» nel fuso %s da %s: le esecuzioni dello scheduler', (expr, zone, start, runs) => {
    const got = nextCronRuns(expr, { from: new Date(start), count: GOLDEN.count, timeZone: zone });
    expect(got.map(wall)).toEqual(runs);
    // Istanti crescenti e dopo la partenza (anche con l'ora ripetuta).
    got.forEach((r, i) => expect(r.at).toBeGreaterThan(i ? got[i - 1].at : new Date(start).getTime()));
    got.forEach(r => expect(formatCronRun(r)).toMatch(/^\p{L}+ \d{1,2} \p{L}+ \d{4}, \d{2}:\d{2}$/u));
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

  it('le esecuzioni sono nell\'ora del fuso dato (quello dello scheduler)', () => {
    const rome = nextCronRuns('0 3 * * *', { from: FROM, count: 1, timeZone: TZ })[0];
    const tokyo = nextCronRuns('0 3 * * *', { from: FROM, count: 1, timeZone: 'Asia/Tokyo' })[0];
    expect(formatCronRun(rome)).toBe('sabato 7 novembre 2026, 03:00');
    // A Tokyo sono gia' le 18:17 del 6: la prossima e' comunque il 7 alle 03:00.
    expect(formatCronRun(tokyo)).toBe('sabato 7 novembre 2026, 03:00');
    const roma10 = nextCronRuns('30 10 * * *', { from: FROM, count: 1, timeZone: TZ })[0];
    expect(formatCronRun(roma10)).toBe('venerdì 6 novembre 2026, 10:30');
  });
});
