/**
 * Fase 5 T5 — un solo convertitore per tipo di campo (R8.4, N12, R8.13, R8.23).
 *
 *  1. Tabella dei convertitori (utils/fieldCodecs): testo -> valore o errore,
 *     mai un default silenzioso.
 *  2. Cron: la tabella unica dei casi (tests/fixtures/cronCases.json), la
 *     stessa che il backend fa girare con lo scheduler vero.
 *  3. Statico: in src niente `parseInt`/`parseFloat`, `Number(e.target.value)`
 *     o `Number(x) || default` fuori dalle eccezioni dichiarate qui sotto.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import * as codecs from '../../src/utils/fieldCodecs';
import { cronProblem } from '../../src/utils/cron';
import { TIMEZONES, timezoneOptions, DEFAULT_TIMEZONE } from '../../src/utils/timezones';
import { runtimeInputCodec } from '../../src/utils/runtimeInputKinds';
import { runInputErrors, runInputs } from '../../src/utils/beforeRun';

const srcRoot = path.resolve(import.meta.dirname, '../../src');
const cronCases = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/cronCases.json'), 'utf-8')).cases;

const ok = (codec, text, value) => expect(codec.parse(text)).toEqual({ value, error: null });
const bad = (codec, text) => {
  const r = codec.parse(text);
  expect(r.value).toBeUndefined();
  expect(typeof r.error).toBe('string');
};

describe('convertitori unici dei campi', () => {
  it('intero: limiti, vuoto obbligatorio o null, niente default', () => {
    const c = codecs.integer({ min: 1, max: 100 });
    ok(c, '5', 5);
    ok(c, ' 42 ', 42);
    bad(c, '');        // prima: tornava il default
    bad(c, '0');       // fuori limite: errore, non 1
    bad(c, '1005');
    bad(c, '5a');
    bad(c, '1.5');
    ok(codecs.integer({ optional: true }), '', null);
    expect(c.format(null)).toBe('');
    expect(c.format(7)).toBe('7');
  });

  it('decimale: punto o virgola, limiti', () => {
    const c = codecs.decimal({ min: 0, max: 10 });
    ok(c, '1.5', 1.5);
    ok(c, '1,5', 1.5);
    bad(c, '11');
    bad(c, 'abc');
  });

  it('booleano: stesse parole del backend', () => {
    const c = codecs.boolean();
    ['true', '1', 'sì', 'si', 'yes'].forEach(t => ok(c, t, true));
    ['false', '0', 'no'].forEach(t => ok(c, t, false));
    ok(c, true, true);
    bad(c, 'forse');
  });

  it('elenco: tiene il testo, scarta i vuoti, segnala doppioni ed elementi non validi', () => {
    const c = codecs.list({ item: codecs.text() });
    ok(c, 'a, b,', ['a', 'b']);
    ok(c, '', []);
    bad(c, 'a, a');
    const ints = codecs.list({ item: codecs.integer({ min: 0 }), unique: false });
    ok(ints, '60, 0, 60', [60, 0, 60]);   // prima lo 0 spariva
    bad(ints, '60, abc');                   // prima "abc" spariva
    expect(c.format(['a', 'b'])).toBe('a, b');
  });

  it('chiave: normalizza al commit, stessa regola del backend', () => {
    const k = codecs.key();
    ok(k, 'File_Clienti ', 'file_clienti');
    ok(k, 'file_', 'file');
    ok(k, 'Città 2', 'citta_2');
    bad(k, '123');
    const sel = codecs.key({ allowed: '_.-', startsWithLetter: false, maxLength: 120 });
    ok(sel, 'cliente-01.a_', 'cliente-01.a');
    ok(sel, '01', '01');
  });

  it('SRID, intervallo, nome file, espressione regolare', () => {
    const s = codecs.srid();
    ok(s, '4326', 4326);
    ok(s, 'EPSG:4326', 4326);
    ok(s, 'epsg 32633', 32633);
    ok(s, '', null);
    bad(s, 'EPSG:abc');
    const r = codecs.numberPair();
    ok(r, '1, 5', '1,5');
    bad(r, '5,1');
    bad(r, '1');
    bad(r, 'a,b');
    const f = codecs.fileStem({ extensions: ['csv', 'xlsx'], empty: 'output' });
    ok(f, 'x.csv', 'x');
    ok(f, 'report.2024', 'report.2024');
    ok(f, '', 'output');
    bad(f, 'a/b');
    bad(f, 'a\\b');
    ok(codecs.regex(), '[A-Z]{3}', '[A-Z]{3}');
    bad(codecs.regex(), '[A-Z');
  });

  it('fuso orario: elenco unico; un fuso salvato fuori elenco si mostra com\'e\' (N13)', () => {
    expect(TIMEZONES[0]).toBe(DEFAULT_TIMEZONE);
    expect(TIMEZONES).toContain('UTC');
    expect(new Set(TIMEZONES).size).toBe(TIMEZONES.length);
    const opts = timezoneOptions('Mars/Olympus');
    expect(opts[0]).toEqual(expect.objectContaining({ value: 'Mars/Olympus', unknown: true }));
    expect(timezoneOptions('Europe/Rome')[0].value).toBe('Europe/Rome');
    ok(codecs.timezone({ keep: 'Mars/Olympus' }), 'Mars/Olympus', 'Mars/Olympus');
    bad(codecs.timezone(), 'Mars/Olympus');
    ok(codecs.timezone(), 'UTC', 'UTC');
  });
});

describe('cron: la regola dello scheduler (tabella condivisa col backend)', () => {
  it.each(cronCases)('%o', ({ expr, valid }) => {
    expect(cronProblem(expr) === null).toBe(valid);
  });

  it('il codec cron normalizza gli spazi e spiega l\'errore', () => {
    ok(codecs.cron(), ' 0  3 * * * ', '0 3 * * *');
    expect(cronProblem('0 3 * *')).toMatch(/5 campi/);
    expect(cronProblem('0 24 * * *')).toMatch(/ora/);
  });
});

describe('input runtime: stessa regola del backend (R8.10)', () => {
  const defs = [
    { input_key: 'n', input_kind: 'integer', required: true, validation: { minimum: 1, maximum: 10 } },
    { input_key: 'x', input_kind: 'number', required: false },
    { input_key: 'b', input_kind: 'boolean', required: false },
    { input_key: 't', input_kind: 'text', required: false, validation: { pattern: '[A-Z]{3}', max_length: 3 } },
    { input_key: 's', input_kind: 'select', required: false, validation: { options: ['a', 'b'] } },
    { input_key: 'd', input_kind: 'date', required: false },
  ];

  it('errori per tipo, limiti, espressione regolare e opzioni', () => {
    expect(runInputErrors(defs, { n: '' })).toEqual({ n: 'Campo obbligatorio' });
    const errs = runInputErrors(defs, { n: '50', x: 'abc', b: 'forse', t: 'abcd', s: 'z', d: '05/11/2026' });
    expect(Object.keys(errs).sort()).toEqual(['b', 'd', 'n', 's', 't', 'x']);
    expect(runInputErrors(defs, { n: '7', x: '1,5', b: 'true', t: 'ABC', s: 'a', d: '2026-11-05' })).toEqual({});
  });

  it('il valore inviato e\' quello convertito', () => {
    expect(runInputs(defs, { n: '7', x: '1,5', b: 'true', t: 'ABC', s: 'a', d: '' }, { omitEmptyOptional: true }))
      .toEqual({ n: 7, x: 1.5, b: true, t: 'ABC', s: 'a' });
    expect(runtimeInputCodec({ input_kind: 'integer' }).parse('')).toEqual({ value: null, error: null });
  });
});

// ── statico ─────────────────────────────────────────────────────────────────

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectSources(full);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [full] : [];
  });
}
const rel = (p) => path.relative(srcRoot, p).split(path.sep).join('/');
const stripComments = (code) => code
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');

// Eccezioni dichiarate: NON testo di un campo, oppure di un'altra tranche.
const PARSE_ALLOWED = {
  'components/modals/PreviewModal.jsx': 'esadecimale -> byte (radice 16) di un contenuto binario, nessun campo',
  'components/nodes/AnnotationNode.jsx': 'colore esadecimale (radice 16) dal selettore di colore',
  'components/panels/response-config/responseConfigEngine.js': 'motore di trasformazione dei valori della risposta API (dati, non campi del form)',
  'components/nodes/CaricanArcGISNode.jsx': 'T6 (nodo Carica ArcGIS): source_srid di un input numerico, vuoto = null (nessun default silenzioso); passa a codecs.srid con il resto del nodo',
};
const NUMBER_OR_ALLOWED = {
  'utils/chunkReload.js': 'istante letto dal sessionStorage, nessun campo',
  'utils/taskOutcome.js': 'progresso dal server, nessun campo',
};

describe('statico: niente conversioni sparse del testo dei campi', () => {
  const files = collectSources(srcRoot).filter(f => rel(f) !== 'utils/fieldCodecs.js');

  it('parseInt/parseFloat solo nelle eccezioni dichiarate', () => {
    const offenders = files
      .filter(f => /\bparse(?:Int|Float)\s*\(/.test(stripComments(fs.readFileSync(f, 'utf-8'))))
      .map(rel)
      .filter(name => !(name in PARSE_ALLOWED));
    expect(offenders).toEqual([]);
  });

  it('nessun Number(e.target.value) / parseInt(e.target.value)', () => {
    const re = /(?:Number|parseInt|parseFloat)\(\s*(?:e|ev|evt|event)\.target\.value/;
    const offenders = files
      .filter(f => re.test(stripComments(fs.readFileSync(f, 'utf-8'))))
      .map(rel)
      .filter(name => !(name in PARSE_ALLOWED));
    expect(offenders).toEqual([]);
  });

  it('nessun Number(x) || default fuori dalle eccezioni', () => {
    const re = /\bNumber\([^()]*(?:\([^()]*\))?[^()]*\)\s*\|\|/;
    const offenders = files
      .filter(f => re.test(stripComments(fs.readFileSync(f, 'utf-8'))))
      .map(rel)
      .filter(name => !(name in NUMBER_OR_ALLOWED));
    expect(offenders).toEqual([]);
  });

  it('le eccezioni dichiarate esistono ancora (l\'elenco resta esatto)', () => {
    const contents = Object.fromEntries(files.map(f => [rel(f), stripComments(fs.readFileSync(f, 'utf-8'))]));
    Object.keys(PARSE_ALLOWED).forEach(name => expect(contents[name], name).toMatch(/\bparse(?:Int|Float)\s*\(/));
    Object.keys(NUMBER_OR_ALLOWED).forEach(name => expect(contents[name], name).toMatch(/\bNumber\(/));
  });
});
