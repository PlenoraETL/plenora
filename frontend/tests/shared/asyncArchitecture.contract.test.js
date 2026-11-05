/**
 * asyncArchitecture.contract.test.js — Fase 5 T2 (audit R4, R9): dati,
 * task asincroni, download e azioni per elemento passano dagli hook comuni.
 *
 *  1. Nessun useEffect che chiama l'API a mano (import da src/api, http,
 *     fetch, axios, `.then(`, o una funzione locale load..., fetch...,
 *     refresh..., reload...) fuori dagli hook comuni: i dati si caricano con
 *     hooks/useResource (id di richiesta, stati distinti, Riprova).
 *  2. Nessun setInterval nelle pagine, nei componenti e negli hook: gli
 *     aggiornamenti periodici sono `refreshInterval` di useResource, i task
 *     sono hooks/useTask.
 *  3. Il polling dei task (getTaskStatus, pollTask) solo in utils/taskPoller,
 *     hooks/useTask e hooks/useExecution (task di Carica).
 *  4. createObjectURL/revokeObjectURL solo in utils/downloadBlob (revoca
 *     ritardata).
 *  5. Ogni chiamata a una funzione download* dell'API in pagine e componenti
 *     sta dentro `run(` di hooks/useDownload o hooks/useBusy (errore mostrato).
 *  6. Niente "busy a id singolo" (`setTestingId(null)` e simili): azioni per
 *     elemento con hooks/useBusy.
 *
 * Eccezioni: dichiarate qui sotto, una per file, con il motivo.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const SRC = path.resolve(import.meta.dirname, '../../src');

// Gli hook comuni: possono fare quello che vietiamo agli altri.
const COMMON = new Set([
  'hooks/useResource.js',
  'hooks/useTask.js',
  'hooks/useExecution.js',
  'hooks/usePreview.js',
  'hooks/useInlinePreview.js',
  'hooks/useDownload.js',
  'hooks/useBusy.js',
  'hooks/useTransformRegistry.js',
  // Orologio dei contatori di tempo trascorso (non un polling di dati).
  'hooks/useNow.js',
  'utils/taskPoller.js',
]);

// Effetti con chiamate al server che NON sono un caricamento di dati.
// file -> motivo. Una voce senza violazione fa fallire il test (lista viva).
export const EFFECT_EXCEPTIONS = {
  // Avvio della sessione: verifica del token salvato (GET /auth/me) prima
  // di montare le pagine; e' il confine della sessione (T1/T3), non un dato
  // di pagina, e ha la sua regola unica per 401 e fine sessione.
  'context/AuthContext.jsx': 'verifica della sessione all avvio (getMe)',
  // Guard di navigazione unico (T4): il `.then(` e' la risposta del dialog
  // di conferma (Esci/Resta) su una navigazione bloccata, non il server.
  'components/common/NavigationGuard.jsx': 'risposta del dialog di conferma su useBlocker, nessuna chiamata al server',
};

const INTERVAL_EXCEPTIONS = {
};

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [full] : [];
  });
}

const rel = (file) => path.relative(SRC, file).split(path.sep).join('/');
const SCANNED = ['pages', 'components', 'hooks', 'context', 'store']
  .flatMap((d) => (fs.existsSync(path.join(SRC, d)) ? walk(path.join(SRC, d)) : []))
  .map((f) => ({ file: rel(f), src: fs.readFileSync(f, 'utf8') }));
const ALL = walk(SRC).map((f) => ({ file: rel(f), src: fs.readFileSync(f, 'utf8') }));

// Toglie commenti e stringhe semplici (le regex non devono vedere il testo).
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, p) => p + ' '.repeat(m.length - p.length));
}

/** Argomenti di ogni chiamata `name(`: testo tra le parentesi bilanciate. */
export function callBodies(src, name) {
  const out = [];
  const re = new RegExp(`\\b${name}\\s*\\(`, 'g');
  let m;
  while ((m = re.exec(src))) {
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (i < src.length && depth > 0) {
      const c = src[i];
      if (c === '(') depth += 1;
      else if (c === ')') depth -= 1;
      i += 1;
    }
    out.push({ body: src.slice(start, i - 1), index: m.index });
  }
  return out;
}

/** Nomi importati da moduli dell'API (src/api/*). */
export function apiImports(src) {
  const names = new Set();
  const re = /import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s*['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(src))) {
    const from = m[3];
    if (!/(^|\/)api(\/|$)/.test(from)) continue;
    if (m[1]) names.add(m[1]);
    (m[2] || '').split(',').map((s) => s.trim()).filter(Boolean).forEach((spec) => {
      const parts = spec.split(/\s+as\s+/);
      names.add((parts[1] || parts[0]).trim());
    });
  }
  return names;
}

const LOADER_CALL = /\b(load|fetch|refresh|reload)[A-Z]?\w*\s*\(/g;
// Azioni dello store con un nome da "caricatore" che non chiamano il server.
const NOT_LOADERS = new Set(['loadGraph']);

/** Motivo per cui il corpo di un useEffect chiama il server, o null. */
export function effectFetchReason(body, api) {
  for (const name of api) {
    if (new RegExp(`\\b${name}\\s*\\(`).test(body)) return `chiama ${name}()`;
  }
  if (/\bfetch\s*\(/.test(body)) return 'chiama fetch()';
  if (/\baxios\b|\bhttp\.(get|post|put|patch|delete)\b/.test(body)) return 'chiama http/axios';
  if (/\.then\s*\(/.test(body)) return 'usa .then()';
  for (const m of body.matchAll(LOADER_CALL)) {
    const name = m[0].replace(/\s*\($/, '');
    if (!NOT_LOADERS.has(name)) return `chiama ${name}()`;
  }
  return null;
}

function effectViolations() {
  const found = {};
  for (const { file, src } of SCANNED) {
    if (COMMON.has(file)) continue;
    const code = stripComments(src);
    const api = apiImports(code);
    for (const { body } of callBodies(code, 'useEffect')) {
      const reason = effectFetchReason(body, api);
      if (reason) (found[file] ||= []).push(reason);
    }
  }
  return found;
}

describe('T2 — dati e task dagli hook comuni', () => {
  it('nessun useEffect con chiamate al server fuori dagli hook comuni (useResource)', () => {
    const found = effectViolations();
    const undeclared = Object.keys(found).filter((f) => !(f in EFFECT_EXCEPTIONS)).map((f) => `${f}: ${found[f].join(', ')}`);
    expect(undeclared).toEqual([]);
    // Lista viva: un'eccezione senza violazione va tolta.
    expect(Object.keys(EFFECT_EXCEPTIONS).filter((f) => !(f in found))).toEqual([]);
  });

  it('nessun setInterval: aggiornamenti periodici con useResource, task con useTask', () => {
    const offenders = SCANNED
      .filter(({ file, src }) => !COMMON.has(file) && /\bsetInterval\s*\(/.test(stripComments(src)))
      .map(({ file }) => file)
      .filter((f) => !(f in INTERVAL_EXCEPTIONS));
    expect(offenders).toEqual([]);
  });

  it('il polling dei task solo in taskPoller, useTask e useExecution', () => {
    const allowed = new Set(['utils/taskPoller.js', 'hooks/useTask.js', 'hooks/useExecution.js', 'api/tasks.js', 'api/index.js']);
    const offenders = ALL
      .filter(({ file, src }) => !allowed.has(file) && /\b(getTaskStatus|pollTask)\b/.test(stripComments(src)))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('createObjectURL e revokeObjectURL solo in utils/downloadBlob (revoca ritardata)', () => {
    const offenders = ALL
      .filter(({ file, src }) => file !== 'utils/downloadBlob.js' && /\b(createObjectURL|revokeObjectURL)\b/.test(stripComments(src)))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('i download dell\'API in pagine e componenti passano da useDownload/useBusy (run)', () => {
    const offenders = [];
    for (const { file, src } of SCANNED) {
      if (COMMON.has(file) || file.startsWith('hooks/')) continue;
      const code = stripComments(src);
      const api = [...apiImports(code)].filter((n) => /^download[A-Z]/.test(n));
      for (const name of api) {
        const re = new RegExp(`\\b${name}\\s*\\(`, 'g');
        // Dentro gli argomenti di una chiamata `run(` (useDownload/useBusy).
        const runs = callBodies(code, 'run');
        let m;
        while ((m = re.exec(code))) {
          const at = m.index;
          if (!runs.some(({ index, body }) => at > index && at < index + body.length + 8)) offenders.push(`${file}: ${name}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('niente busy a id singolo: azioni per elemento con useBusy', () => {
    const re = /\bset\w*(Testing|Deleting|Saving|Resolving|Cloning|Busy|Running|Renaming)\w*Id\s*\(\s*null\s*\)/;
    const offenders = SCANNED
      .filter(({ src }) => re.test(stripComments(src)))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('il rilevatore riconosce i casi tipici (positivi e negativi)', () => {
    const api = apiImports("import { getTicket } from '../api/tickets';\nimport http from '../api/http';");
    expect([...api].sort()).toEqual(['getTicket', 'http']);
    expect(effectFetchReason('() => { getTicket(id).then(setT); }', api)).toBe('chiama getTicket()');
    expect(effectFetchReason('() => { loadItems(); }', new Set())).toBe('chiama loadItems()');
    expect(effectFetchReason('() => { x.then(f); }', new Set())).toBe('usa .then()');
    expect(effectFetchReason('() => { setOpen(false); }', api)).toBeNull();
    expect(callBodies('useEffect(() => { a(b(c)); }, [d]);', 'useEffect')[0].body).toBe('() => { a(b(c)); }, [d]');
  });
});
