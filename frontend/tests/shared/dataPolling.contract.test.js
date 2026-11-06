/**
 * dataPolling.contract.test.js — UI-1 «Dati e polling» (06/11/2026; audit
 * ui-velocita-chiamate-usabilita C1-C11, C17, C19-C27, V10, V12, M-Estrai).
 *
 *  1. Enumerazione: le funzioni API delle risorse condivise si chiamano SOLO
 *     da utils/sharedResources (fuori: eccezioni dichiarate col motivo).
 *  2. Ogni scrittura di src/api ha la sua riga in WRITE_INVALIDATIONS, e ogni
 *     risorsa condivisa (tranne le statiche) e' invalidata da qualcuna.
 *  3. Cache: deduplica, fresca/scaduta (stale-while-revalidate), force,
 *     invalidazione per voce, risposta tardiva, iscritti, logout.
 *  4. Timer: ogni setTimeout/setInterval di src/ ha un motivo dichiarato; il
 *     polling sta solo in utils/poller; `refreshInterval` solo con un profilo.
 *  5. Poller: scheda nascosta, backoff, durata massima, un poller per N task.
 *  6. /auth/me una volta per ritorno sulla scheda (focus + visibilitychange).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, act, renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SHARED_RESOURCES } from '../../src/utils/sharedResources';
import {
  readShared, peekShared, invalidateShared, invalidateAfterWrite, subscribeShared,
  defineSharedResources, resetSharedCache, STALE_LIMIT_MS,
} from '../../src/utils/sharedCache';
import { WRITE_INVALIDATIONS, TASK_INVALIDATIONS, writeRule, invalidationsForWrite, SHARED_LIST_NAMES } from '../../src/utils/sharedInvalidations';
import { POLL_PROFILES, startPoller, BACKOFF_FACTOR } from '../../src/utils/poller';
import { pollTasks } from '../../src/utils/taskPoller';
import { resetSession } from '../../src/utils/session';
import { useSharedResource } from '../../src/hooks/useSharedResource';

const { getMeMock } = vi.hoisted(() => ({ getMeMock: vi.fn() }));
vi.mock('../../src/api/users', () => ({ getMe: getMeMock }));

const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
  const full = path.join(dir, e.name);
  if (e.isDirectory()) return e.name === 'mocks' ? [] : walk(full);
  return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
});
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const strip = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, p) => p + ' '.repeat(m.length - p.length));
const FILES = walk(SRC).map((f) => ({ file: rel(f), code: strip(fs.readFileSync(f, 'utf8')) }));
const withoutImports = (code) => code.replace(/^\s*import[\s\S]*?from\s*['"][^'"]+['"];?/gm, '');

// ── 1. Le risorse condivise passano dalla cache ────────────────────────────

// Funzioni API delle risorse di SHARED_RESOURCES.
const SHARED_API = [
  'listProjects', 'listConnections', 'listFeatureConnections', 'listApiConnections', 'getFiles',
  'listDataSources', 'listEseguibili', 'listConnectionSets', 'getDashboardCounts', 'getProject',
  'getFile', 'getConnectionTables', 'getTicketsCounts', 'getTicketsMeta', 'getBackupStatus',
  // Conferma A-K3: lo stato dell'ultima esecuzione e' anche lui nella cache.
  'getLatestRunByProject',
];

// Chi puo' chiamarle direttamente, col motivo (lista viva).
const DIRECT_CALL_EXCEPTIONS = {
  'store/useCanvasStore.js': {
    getProject: 'conflitto di versione del canvas: serve la versione del server adesso, mai una copia',
  },
};

describe('UI-1 — risorse condivise: una sola strada', () => {
  it('la tabella copre ogni funzione API dichiarata condivisa', () => {
    const table = fs.readFileSync(path.join(SRC, 'utils/sharedResources.js'), 'utf8');
    for (const name of SHARED_API) expect(table, name).toMatch(new RegExp(`\\b${name}\\(`));
    for (const [name, def] of Object.entries(SHARED_RESOURCES)) {
      expect(typeof def.load, name).toBe('function');
      expect(def.freshMs, name).toBeGreaterThan(0);
    }
  });

  it('le funzioni delle risorse condivise si chiamano solo da utils/sharedResources', () => {
    const found = [];
    for (const { file, code } of FILES) {
      if (file.startsWith('api/') || file === 'utils/sharedResources.js') continue;
      const body = withoutImports(code);
      for (const name of SHARED_API) {
        if (!new RegExp(`\\b${name}\\b`).test(body)) continue;
        if (DIRECT_CALL_EXCEPTIONS[file]?.[name]) continue;
        found.push(`${file}: ${name}`);
      }
    }
    expect(found).toEqual([]);
    // Lista viva: un'eccezione che non serve piu' va tolta.
    const stale = Object.entries(DIRECT_CALL_EXCEPTIONS).flatMap(([file, names]) => Object.keys(names)
      .filter((name) => !new RegExp(`\\b${name}\\b`).test(withoutImports(FILES.find((f) => f.file === file)?.code || '')))
      .map((name) => `${file}: ${name}`));
    expect(stale).toEqual([]);
  });

  it('le liste condivise sono quelle della tabella (piu\' le statiche, mai invalidate)', () => {
    const STATIC = ['ticketsMeta'];
    expect([...SHARED_LIST_NAMES].sort()).toEqual(Object.keys(SHARED_RESOURCES).filter((n) => !STATIC.includes(n)).sort());
  });
});

// ── 2. Ogni scrittura ha la sua riga ───────────────────────────────────────

// Indirizzi delle scritture in src/api: argomento letterale di
// http.post/put/patch/delete o `const url = ...` della stessa funzione.
function writeCalls() {
  const out = [];
  for (const { file, code } of FILES.filter((f) => f.file.startsWith('api/'))) {
    const base = /const API_BASE = '([^']+)'/.exec(code)?.[1] || '';
    const sample = (tpl) => tpl.replace(/\$\{API_BASE\}/g, base).replace(/\$\{[^}]+\}/g, 'x1');
    for (const m of code.matchAll(/http\.(post|put|patch|delete)\(\s*(['`])([^'`]+)\2/g)) out.push({ file, method: m[1], url: sample(m[3]) });
    for (const m of code.matchAll(/const url = ([^;]+);/g)) {
      for (const lit of m[1].matchAll(/`([^`]+)`|'([^']+)'/g)) {
        const tpl = lit[1] || lit[2];
        const method = /http\.(post|put|patch|delete)\(\s*url/.exec(code)?.[1] || 'post';
        out.push({ file, method, url: sample(tpl) });
      }
    }
  }
  return out;
}

describe('UI-1 — scritture e invalidazione (tabella WRITE_INVALIDATIONS)', () => {
  const calls = writeCalls();

  it('trova le scritture di src/api (il rilevatore funziona)', () => {
    expect(calls.length).toBeGreaterThan(60);
    expect(calls).toEqual(expect.arrayContaining([
      expect.objectContaining({ method: 'put', url: '/projects/x1/canvas' }),
      expect.objectContaining({ method: 'post', url: '/projects/x1/versions/x1/restore' }),
      expect.objectContaining({ method: 'delete', url: '/features/connections/x1' }),
    ]));
  });

  it('ogni scrittura di src/api ha una riga esplicita', () => {
    const missing = calls.filter((c) => !writeRule(c.method, c.url)).map((c) => `${c.file}: ${c.method.toUpperCase()} ${c.url}`);
    expect(missing).toEqual([]);
  });

  it('ogni riga ha un motivo e serve a qualche scrittura (lista viva)', () => {
    const used = new Set(calls.map((c) => writeRule(c.method, c.url)?.rule).filter(Boolean));
    const unused = WRITE_INVALIDATIONS.filter((r) => !used.has(r)).map((r) => String(r.url));
    expect(unused).toEqual([]);
    expect(WRITE_INVALIDATIONS.every((r) => typeof r.why === 'string' && r.why.length > 5)).toBe(true);
  });

  it('ogni risorsa condivisa (tranne le statiche) e\' invalidata da almeno una scrittura o dalla fine di un task', () => {
    const taskNames = TASK_INVALIDATIONS.flatMap((r) => (typeof r.invalidates === 'function' ? r.invalidates(['', 'x1']) : r.invalidates));
    const names = new Set([...calls.flatMap((c) => invalidationsForWrite(c.method, c.url)), ...taskNames].map((n) => (Array.isArray(n) ? n[0] : n)));
    expect([...SHARED_LIST_NAMES].filter((n) => !names.has(n))).toEqual([]);
    for (const n of names) expect(Object.keys(SHARED_RESOURCES)).toContain(n);
  });

  it('casi tipici: canvas salvato, letture con POST, scrittura sconosciuta', () => {
    expect(invalidationsForWrite('put', '/projects/p1/canvas')).toEqual(['projects', ['project', 'p1']]);
    expect(invalidationsForWrite('post', '/projects/p1/schemas')).toEqual([]);
    expect(invalidationsForWrite('post', '/projects/p1/query-preview')).toEqual([]);
    expect(invalidationsForWrite('delete', '/connections/c9')).toEqual(['connections', ['connectionTables', 'c9']]);
    expect(invalidationsForWrite('get', '/projects')).toEqual([]);
    // Sconosciuta: tutto (scelta sicura).
    expect(invalidationsForWrite('post', '/nuovo-endpoint')).toEqual([...SHARED_LIST_NAMES]);
    // Indirizzo assoluto o con /api davanti: stessa riga.
    expect(invalidationsForWrite('post', 'http://h/api/tickets?x=1')).toEqual(['ticketsCounts']);
  });

  it('api/http applica la tabella a ogni risposta di una scrittura (anche fallita)', () => {
    const http = fs.readFileSync(path.join(SRC, 'api/http.js'), 'utf8');
    expect(http).toMatch(/invalidateAfterWrite\(/);
    expect((http.match(/afterWrite\((response|error\?)\.config\)/g) || []).length).toBe(2);
  });
});

// ── 3. Il motore della cache ───────────────────────────────────────────────

const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

describe('UI-1 — cache condivisa: regole', () => {
  let load;
  beforeEach(() => {
    resetSharedCache();
    load = vi.fn((id) => Promise.resolve({ id, n: load.mock.calls.length }));
    defineSharedResources({ __t: { load: (...a) => load(...a), freshMs: 1000 }, __u: { load: () => Promise.resolve('u'), freshMs: 1000 } });
  });
  afterEach(() => { vi.useRealTimers(); });

  it('deduplica: N letture contemporanee = UNA richiesta; fresca = nessuna richiesta', async () => {
    const results = await Promise.all([1, 2, 3, 4].map(() => readShared('__t', ['a'])));
    expect(load).toHaveBeenCalledTimes(1);
    expect(new Set(results).size).toBe(1);
    await readShared('__t', ['a']);
    expect(load).toHaveBeenCalledTimes(1);
    // Parametri diversi = voce diversa.
    await readShared('__t', ['b']);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('scaduta: risponde subito la cache e parte UNA richiesta in background; oltre il limite si aspetta', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    const first = await readShared('__t', ['a']);
    vi.setSystemTime(Date.now() + 1500);
    const stale = await readShared('__t', ['a']);
    expect(stale).toBe(first);
    await readShared('__t', ['a']);
    expect(load).toHaveBeenCalledTimes(2);
    await act(async () => { await Promise.resolve(); });
    expect((await readShared('__t', ['a'])).n).toBe(2);
    expect(peekShared('__t', ['a']).fresh).toBe(true);
    vi.setSystemTime(Date.now() + STALE_LIMIT_MS + 1);
    expect(peekShared('__t', ['a'])).toBeNull();
  });

  it('force: dal server, ma riusa una richiesta gia\' in volo', async () => {
    await readShared('__t', ['a']);
    const d = deferred();
    load.mockReturnValueOnce(d.promise);
    const p1 = readShared('__t', ['a'], { force: true });
    const p2 = readShared('__t', ['a'], { force: true });
    expect(load).toHaveBeenCalledTimes(2);
    d.resolve('nuovo');
    expect(await p1).toBe('nuovo');
    expect(await p2).toBe('nuovo');
  });

  it('invalidazione per voce: solo quella; una risposta partita prima non riempie', async () => {
    await readShared('__t', ['a']);
    await readShared('__t', ['b']);
    invalidateShared([['__t', 'a']]);
    expect(peekShared('__t', ['a'])).toBeNull();
    expect(peekShared('__t', ['b'])).not.toBeNull();
    const late = deferred();
    load.mockReturnValueOnce(late.promise);
    const pending = readShared('__t', ['a']);
    invalidateShared('__t');
    late.resolve('vecchio');
    await pending;
    expect(peekShared('__t', ['a'])).toBeNull();
    expect(peekShared('__t', ['b'])).toBeNull();
  });

  it('un errore non resta in cache', async () => {
    load.mockRejectedValueOnce(new Error('rete'));
    await expect(readShared('__t', ['a'])).rejects.toThrow('rete');
    expect((await readShared('__t', ['a'])).id).toBe('a');
  });

  it('iscritti: avvisati all\'invalidazione e all\'arrivo; quelli di una voce solo per quella', async () => {
    const all = vi.fn();
    const onlyA = vi.fn();
    const offAll = subscribeShared(['__t'], all);
    const offA = subscribeShared([['__t', 'a']], onlyA);
    await readShared('__t', ['b']);
    expect(all).toHaveBeenCalledTimes(1);
    expect(onlyA).not.toHaveBeenCalled();
    invalidateShared([['__t', 'a']]);
    expect(onlyA).toHaveBeenCalledTimes(1);
    offAll(); offA();
    invalidateShared('__t');
    expect(all).toHaveBeenCalledTimes(2);
  });

  it('una scrittura invalida le liste della tabella (es. canvas salvato)', async () => {
    defineSharedResources({ projects: { load: () => Promise.resolve({ projects: [] }), freshMs: 1000 }, project: { load: (id) => Promise.resolve({ id }), freshMs: 1000 } });
    await readShared('projects');
    await readShared('project', ['p1']);
    await readShared('project', ['p2']);
    invalidateAfterWrite('put', '/projects/p1/canvas');
    expect(peekShared('projects')).toBeNull();
    expect(peekShared('project', ['p1'])).toBeNull();
    expect(peekShared('project', ['p2'])).not.toBeNull();
    invalidateAfterWrite('post', '/projects/p2/schemas');
    expect(peekShared('project', ['p2'])).not.toBeNull();
    defineSharedResources({ projects: SHARED_RESOURCES.projects, project: SHARED_RESOURCES.project });
  });

  it('logout: resetSession svuota la cache; una risposta della sessione prima non la riempie', async () => {
    await readShared('__t', ['a']);
    const late = deferred();
    load.mockReturnValueOnce(late.promise);
    const pending = readShared('__t', ['z']);
    resetSession();
    expect(peekShared('__t', ['a'])).toBeNull();
    late.resolve('utente-precedente');
    await pending;
    expect(peekShared('__t', ['z'])).toBeNull();
  });

  it('useSharedResource: due componenti = una richiesta; dati in cache subito; invalidata = ricarica', async () => {
    const a = renderHook(() => useSharedResource('__t', { params: ['h'] }));
    const b = renderHook(() => useSharedResource('__t', { params: ['h'] }));
    await waitFor(() => expect(a.result.current.status).toBe('ready'));
    await waitFor(() => expect(b.result.current.status).toBe('ready'));
    expect(load).toHaveBeenCalledTimes(1);
    const c = renderHook(() => useSharedResource('__t', { params: ['h'] }));
    expect(c.result.current.status).toBe('ready');
    expect(c.result.current.initialLoading).toBe(false);
    await act(async () => { invalidateShared('__t'); });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(a.result.current.data.n).toBe(2));
    // reload = dal server.
    await act(async () => { await a.result.current.reload(); });
    expect(load).toHaveBeenCalledTimes(3);
  });
});

// ── 4. Timer e polling: un solo modulo ─────────────────────────────────────

// Ogni file di src/ con setTimeout/setInterval, col motivo. Nessuno e' un
// polling di dati: quello sta in utils/poller (lista viva).
const TIMER_USES = {
  'utils/poller.js': 'IL poller (unico)',
  'components/ChangePasswordModal.jsx': 'chiusura della finestra dopo il messaggio di successo',
  'components/editor/BeforeRunModal.jsx': 'attesa dopo la digitazione prima della stima (debounce)',
  'components/editor/EditorShell.jsx': 'salvataggio al giro dopo e fine dell\'evidenziazione della vista',
  'components/eseguibili/useEseguibileRun.js': 'file scaricato tolto dalla memoria dopo il tempo dichiarato (UI-3, U13)',
  'components/modals/PreviewModal.jsx': 'ridisegno della mappa Leaflet dopo l\'apertura',
  'components/nodes/StaticValuesNode.jsx': 'chiusura del suggerimento dopo il blur',
  'components/panels/QueryPreview.jsx': 'fine del segno «copiato»',
  'context/ToastContext.jsx': 'chiusura dei toast',
  'hooks/useDebouncedValue.js': 'debounce di un valore',
  'hooks/useLocalDraft.js': 'debounce della copia locale',
  'hooks/useNow.js': 'orologio dei contatori di tempo trascorso (nessuna richiesta)',
  'hooks/usePreviewStale.js': 'debounce del controllo «anteprima non aggiornata»',
  'hooks/useSearch.js': 'pausa della digitazione nella ricerca',
  'pages/LoginPage.jsx': 'ridisegno dello sfondo al ridimensionamento',
  'store/useColumnStore.js': 'debounce di /schemas e tempo massimo di attesa delle colonne (non un polling)',
  'utils/downloadBlob.js': 'revoca ritardata dell\'URL del download',
  'utils/hexCanvas.js': 'ridisegno dello sfondo',
  'utils/localDrafts.js': 'battito della scheda per le copie locali (nessuna richiesta)',
  'utils/serverActions.js': 'ricarica della pagina dopo un aggiornamento',
  'index.jsx': 'caricamento di Sentry quando il browser e\' libero (ripiego di requestIdleCallback)',
};

describe('UI-1 — timer: polling solo in utils/poller', () => {
  it('ogni setTimeout/setInterval di src/ ha un motivo dichiarato', () => {
    const users = FILES.filter(({ code }) => /\b(setTimeout|setInterval)\s*\(/.test(code)).map((f) => f.file);
    expect(users.filter((f) => !(f in TIMER_USES))).toEqual([]);
    expect(Object.keys(TIMER_USES).filter((f) => !users.includes(f))).toEqual([]);
  });

  it('nessun timer nei moduli che chiamano il server, tranne il poller', () => {
    const offenders = FILES
      .filter(({ file, code }) => file !== 'utils/poller.js' && /\b(setTimeout|setInterval)\s*\(/.test(code))
      .filter(({ code }) => /\b(getTaskStatus|http\.(get|post)|fetch\s*\()/.test(code))
      .map((f) => f.file);
    expect(offenders).toEqual([]);
  });

  it('refreshInterval solo con un profilo di POLL_PROFILES (mai millisecondi)', () => {
    const bad = [];
    for (const { file, code } of FILES) {
      if (file === 'hooks/useResource.js' || file === 'hooks/useSharedResource.js') continue;
      for (const m of code.matchAll(/refreshInterval:\s*([^,}\n]+)/g)) {
        const literals = [...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]);
        const numbers = m[1].replace(/'[^']*'/g, '').match(/\b\d[\d_]*\b/g) || [];
        if (numbers.some((n) => n !== '0') || literals.some((l) => !(l in POLL_PROFILES)) || literals.length === 0) bad.push(`${file}: ${m[0]}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it('i task passano da utils/taskPoller sopra utils/poller (profilo task)', () => {
    const tp = fs.readFileSync(path.join(SRC, 'utils/taskPoller.js'), 'utf8');
    expect(tp).toMatch(/startPoller\(/);
    expect(strip(tp)).not.toMatch(/\bsetTimeout\s*\(/);
    const ex = fs.readFileSync(path.join(SRC, 'hooks/useExecution.js'), 'utf8');
    expect(strip(ex)).toMatch(/pollTasks\(/);
    expect(strip(ex)).not.toMatch(/\.forEach\([^)]*\)\s*=>\s*{[\s\S]{0,200}pollTask\(/);
  });
});

// ── 5. Il poller ───────────────────────────────────────────────────────────

describe('UI-1 — poller: nascosta, backoff, durata massima', () => {
  let visibility;
  let spy;
  beforeEach(() => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    visibility = 'visible';
    spy = vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  });
  afterEach(() => { spy.mockRestore(); vi.useRealTimers(); });
  const flush = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };

  it('backoff senza novita\' fino a maxInterval; una novita\' torna a interval', async () => {
    let changed = false;
    const tick = vi.fn(() => Promise.resolve({ changed }));
    const stop = startPoller(tick, { interval: 1000, maxInterval: 2000 });
    await act(async () => { vi.advanceTimersByTime(1000); await flush(); });
    expect(tick).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1000 * BACKOFF_FACTOR - 1); await flush(); });
    expect(tick).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1); await flush(); });
    expect(tick).toHaveBeenCalledTimes(2);
    // tetto: 2000
    await act(async () => { vi.advanceTimersByTime(2000); await flush(); });
    expect(tick).toHaveBeenCalledTimes(3);
    changed = true;
    await act(async () => { vi.advanceTimersByTime(2000); await flush(); });
    await act(async () => { vi.advanceTimersByTime(1000); await flush(); });
    expect(tick).toHaveBeenCalledTimes(5);
    stop();
  });

  it('scheda nascosta: nessun giro; al ritorno un giro subito', async () => {
    const tick = vi.fn(() => Promise.resolve({ changed: true }));
    const stop = startPoller(tick, { interval: 1000 });
    visibility = 'hidden';
    await act(async () => { vi.advanceTimersByTime(10_000); await flush(); });
    expect(tick).not.toHaveBeenCalled();
    visibility = 'visible';
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await flush(); });
    expect(tick).toHaveBeenCalledTimes(1);
    stop();
    await act(async () => { vi.advanceTimersByTime(10_000); await flush(); });
    expect(tick).toHaveBeenCalledTimes(1);
  });

  it('durata massima: i dati si fermano e ripartono al ritorno sulla scheda; i task finiscono', async () => {
    const tick = vi.fn(() => Promise.resolve({ changed: true }));
    const onExpire = vi.fn();
    const stop = startPoller(tick, { interval: 1000, maxDurationMs: 3000, resumeOnVisible: true, onExpire });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const calls = tick.mock.calls.length;
    expect(onExpire).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(tick.mock.calls.length).toBe(calls);
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await flush(); });
    expect(tick.mock.calls.length).toBe(calls + 1);
    stop();
  });

  it('ogni profilo ha intervallo, tetto >= intervallo e durata massima', () => {
    for (const [name, p] of Object.entries(POLL_PROFILES)) {
      expect(p.interval, name).toBeGreaterThan(0);
      expect(p.maxInterval, name).toBeGreaterThanOrEqual(p.interval);
      expect(p.maxDurationMs, name).toBeGreaterThan(0);
    }
  });

  it('Carica: N task = UN poller; ogni giro chiede solo quelli aperti; fine quando sono finiti tutti', async () => {
    const status = { t1: 'pending', t2: 'pending', t3: 'pending' };
    const getStatus = vi.fn((id) => Promise.resolve({ status: status[id], result: {} }));
    const onDone = vi.fn();
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    pollTasks(['t1', 't2', 't3'], { getStatus, onDone, interval: 1000 });
    await act(async () => { await flush(); });
    expect(getStatus).toHaveBeenCalledTimes(3);
    const timersAfterFirst = timeoutSpy.mock.calls.length;
    expect(timersAfterFirst).toBe(1);
    status.t1 = 'success';
    await act(async () => { vi.advanceTimersByTime(1000); await flush(); });
    expect(getStatus).toHaveBeenCalledTimes(6);
    expect(onDone).toHaveBeenCalledWith('t1', expect.objectContaining({ phase: 'success' }));
    status.t2 = 'success'; status.t3 = 'failure';
    await act(async () => { vi.advanceTimersByTime(5000); await flush(); });
    expect(getStatus).toHaveBeenCalledTimes(8);
    expect(onDone).toHaveBeenCalledTimes(3);
    await act(async () => { vi.advanceTimersByTime(60_000); await flush(); });
    expect(getStatus).toHaveBeenCalledTimes(8);
    timeoutSpy.mockRestore();
  });

  it('task oltre la durata massima: esito lost (task_poll_expired), mai «per sempre»', async () => {
    const getStatus = vi.fn(() => Promise.resolve({ status: 'progress', progress: 1 }));
    const onDone = vi.fn();
    pollTasks(['t'], { getStatus, onDone, interval: 1000, maxDurationMs: 5000 });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(onDone).toHaveBeenCalledWith('t', expect.objectContaining({ phase: 'lost', code: 'task_poll_expired' }));
  });
});

// ── 6. /auth/me al ritorno sulla scheda ────────────────────────────────────

describe('UI-1 — /auth/me una volta per ritorno sulla scheda (C3, V12)', () => {
  it('focus + visibilitychange insieme = UNA richiesta', async () => {
    const { MemoryRouter } = await import('react-router-dom');
    const { AuthProvider } = await import('../../src/context/AuthContext');
    sessionStorage.setItem('urby_token', 'tok');
    sessionStorage.setItem('urby_user', JSON.stringify({ username: 'a', role: 'admin', group_id: 'g' }));
    getMeMock.mockResolvedValue({ username: 'a', role: 'admin', group_id: 'g' });
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z'), toFake: ['Date'] });
    render(React.createElement(MemoryRouter, null, React.createElement(AuthProvider, null, 'x')));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(getMeMock).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 60_000);
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });
    expect(getMeMock).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
    sessionStorage.clear();
  });
});
