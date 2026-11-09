/**
 * Conferma UI-1..UI-4 (06/11/2026), area «dati, cache, poller, liste server»
 * (docs/audits/ui-conferma-2026-11-06.md). Un test per difetto, che
 * fallirebbe senza la correzione:
 *
 *  A-K2  la fine di un task invalida per tabella (TASK_INVALIDATIONS): il
 *        progetto e i progetti a valle, l'ultima esecuzione, il catalogo
 *        tabelle della connessione di Carica; api/http registra ogni avvio;
 *  A-K3  la dashboard a cache vuota chiede latest-by-project UNA volta;
 *  A-K4  l'editor non apre mai una copia scaduta del progetto;
 *  A-K5  poller: tempo attivo e ultima lettura (a); i dati ripartono con
 *        l'attivita' e `paused` lo dice (b); un poll con risorse condivise
 *        non e' superato dal suo stesso avviso (c); profilo Arricchisci (d);
 *  A-K7  le mappe /schemas dipendono dalla versione delle sorgenti;
 *        ensure() non usa mappe vecchie;
 *  A-K8  /auth/me con gruppo o ruolo cambiati invalida le liste;
 *  A-K9  generazione per voce; errore di rivalidazione = «non aggiornati»;
 *  C-K10 conteggi della dashboard non disponibili detti, gli altri restano.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, act, renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SHARED_RESOURCES } from '../../src/utils/sharedResources';
import {
  readShared, peekShared, invalidateShared, defineSharedResources, resetSharedCache, invalidationCount,
} from '../../src/utils/sharedCache';
import { TASK_INVALIDATIONS, taskEffects } from '../../src/utils/sharedInvalidations';
import { registerTaskLaunch, taskProfileName } from '../../src/utils/taskRegistry';
import { POLL_PROFILES, startPoller } from '../../src/utils/poller';
import { pollTasks } from '../../src/utils/taskPoller';
import { useResource } from '../../src/hooks/useResource';
import { useSharedResource } from '../../src/hooks/useSharedResource';
import { resetSession } from '../../src/utils/session';
import { createColumnStore, startColumnSync, derivedColumnsForSave, SCHEMA_FRESH_MS } from '../../src/store/useColumnStore';
import { createCanvasStore } from '../../src/store/useCanvasStore';

const { getMeMock } = vi.hoisted(() => ({ getMeMock: vi.fn() }));
vi.mock('../../src/api/users', () => ({ getMe: getMeMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => ({ info() {}, warning() {}, error() {}, success() {} }) }));

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8');
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

// Le definizioni di SHARED_RESOURCES tornano quelle vere dopo ogni test.
const restoreResources = () => defineSharedResources(SHARED_RESOURCES);
const fake = (table) => defineSharedResources(Object.fromEntries(Object.entries(table).map(([n, d]) => [n, { freshMs: 60_000, ...d }])));

beforeEach(() => { resetSession(); });
afterEach(() => { vi.useRealTimers(); restoreResources(); resetSession(); vi.restoreAllMocks(); });

// ── A-K2 ───────────────────────────────────────────────────────────────────

describe('A-K2 — la fine di un task invalida quello che ha cambiato', () => {
  it('ogni riga di TASK_INVALIDATIONS ha profilo, invalidazioni di risorse vere e motivo', () => {
    for (const row of TASK_INVALIDATIONS) {
      expect(row.profile in POLL_PROFILES, String(row.url)).toBe(true);
      expect(row.why.length, String(row.url)).toBeGreaterThan(5);
      const names = (typeof row.invalidates === 'function' ? row.invalidates(['', 'x1']) : row.invalidates).map(n => (Array.isArray(n) ? n[0] : n));
      for (const n of names) expect(Object.keys(SHARED_RESOURCES)).toContain(n);
    }
    // Avvii tipici e il caso sconosciuto (tutto).
    expect(taskEffects('post', '/projects/p1/execute').invalidates).toEqual(['project', 'latestRunByProject', 'dashboardGraph']);
    expect(taskEffects('post', '/api/projects/p1/enrich-async').profile).toBe('enrichTask');
    expect(taskEffects('get', '/projects/p1/preview').invalidates).toEqual([['project', 'p1']]);
    expect(taskEffects('post', '/qualcosa/di-nuovo').known).toBe(false);
  });

  it('api/http registra OGNI risposta con task_id; il poller chiama la fine del task', () => {
    const http = read('api/http.js');
    expect(http).toMatch(/registerTaskLaunch\(response\?\.config\?\.method, response\?\.config\?\.url, response\?\.data\)/);
    expect(read('utils/taskPoller.js')).toMatch(/taskEnded\(taskId, outcome\)/);
  });

  it('esecuzione -> progetto e progetti a valle, ultima esecuzione; i suoi Carica -> tabelle della LORO connessione', async () => {
    fake({
      project: { load: (id) => Promise.resolve({ id }) },
      latestRunByProject: { load: () => Promise.resolve({ statuses: {} }) },
      connectionTables: { load: (id) => Promise.resolve({ id }) },
    });
    await readShared('project', ['p1']);
    await readShared('project', ['valle']);
    await readShared('latestRunByProject');
    await readShared('connectionTables', ['k1']);
    await readShared('connectionTables', ['k2']);
    registerTaskLaunch('post', '/projects/p1/execute', { async: true, task_id: 't1' });
    let status = 'progress';
    const getStatus = vi.fn((id) => Promise.resolve(id === 't1'
      ? { status, result: { carica: { tasks: [{ task_id: 'c1', node_id: 'n', connection_id: 'k1' }] } } }
      : { status: 'success', result: {} }));
    pollTasks(['t1'], { getStatus, interval: 10 });
    await act(async () => { await flush(); });
    expect(peekShared('project', ['valle'])).not.toBeNull();
    status = 'success';
    await act(async () => { await new Promise(r => setTimeout(r, 30)); await flush(); });
    expect(peekShared('project', ['p1'])).toBeNull();
    expect(peekShared('project', ['valle'])).toBeNull();
    expect(peekShared('latestRunByProject')).toBeNull();
    expect(peekShared('connectionTables', ['k1'])).not.toBeNull();
    // Il task di Carica avviato dall'esecuzione.
    await readShared('latestRunByProject');
    pollTasks(['c1'], { getStatus });
    await act(async () => { await flush(); });
    expect(peekShared('connectionTables', ['k1'])).toBeNull();
    expect(peekShared('connectionTables', ['k2'])).not.toBeNull();
    expect(peekShared('latestRunByProject')).toBeNull();
  });

  it('Arricchisci si segue con il suo profilo (tetto dichiarato, A-K5d)', () => {
    registerTaskLaunch('post', '/projects/p1/enrich-async', { async: true, task_id: 'e1' });
    expect(taskProfileName('e1')).toBe('enrichTask');
    expect(POLL_PROFILES.enrichTask.maxDurationMs).toBeGreaterThan(POLL_PROFILES.task.maxDurationMs);
  });
});

// ── A-K3 ───────────────────────────────────────────────────────────────────

describe('A-K3 — dashboard: latest-by-project una volta a cache vuota', () => {
  it('le liste che arrivano una per una non richiedono latest-by-project', async () => {
    const { useProjectListData } = await import('../../src/components/projects/useProjectListData');
    const latest = vi.fn(() => Promise.resolve({ statuses: { p1: { status: 'success' } } }));
    const waits = {};
    const slow = (name, value) => ({ load: () => { const d = deferred(); waits[name] = () => d.resolve(value); return d.promise; } });
    fake({
      projects: slow('projects', { projects: [{ id: 'p1', project_type: 'estrai' }] }),
      connections: slow('connections', { connections: [] }),
      dashboardCounts: slow('dashboardCounts', {}),
      files: slow('files', { files: [] }),
      arcgisConnections: slow('arcgisConnections', { connections: [] }),
      eseguibili: slow('eseguibili', []),
      latestRunByProject: { load: latest },
    });
    const { result } = renderHook(() => useProjectListData());
    for (const name of ['files', 'connections', 'projects', 'arcgisConnections', 'eseguibili', 'dashboardCounts']) {
      await act(async () => { waits[name](); await flush(); });
    }
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    expect(latest).toHaveBeenCalledTimes(1);
    expect(result.current.runStatusFor('p1')).toEqual({ status: 'success' });
  });
});

// ── A-K4 ───────────────────────────────────────────────────────────────────

describe('A-K4 — l\'editor non apre una copia scaduta del progetto', () => {
  it('copia scaduta in cache: l\'editor aspetta il server e mostra la versione nuova', async () => {
    const { useEditorData } = await import('../../src/hooks/useEditorData');
    let version = 1;
    fake({
      project: { load: (id) => Promise.resolve({ id, canvas_version: version, canvas: { nodes: [] } }), freshMs: 5000 },
      connections: { load: () => Promise.resolve({ connections: [] }) },
    });
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z'), toFake: ['Date'] });
    await readShared('project', ['p1']);
    version = 2;
    vi.setSystemTime(Date.now() + 60_000);
    const { result } = renderHook(() => useEditorData('p1'));
    await waitFor(() => expect(result.current.project).not.toBeNull());
    expect(result.current.project.canvas_version).toBe(2);
    expect(read('hooks/useEditorData.js')).toMatch(/readShared\('project', \[projectId\], \{ force, allowStale: false \}\)/);
  });
});

// ── A-K5 ───────────────────────────────────────────────────────────────────

describe('A-K5 — poller', () => {
  let visibility;
  beforeEach(() => {
    visibility = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  });

  it('(a) 4 ore a scheda nascosta: al ritorno un\'ultima lettura, il task risulta finito (non perso)', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    let status = 'progress';
    const onDone = vi.fn();
    pollTasks(['t'], { getStatus: () => Promise.resolve({ status, progress: 1 }), onDone, interval: 1000 });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    visibility = 'hidden';
    await act(async () => { await vi.advanceTimersByTimeAsync(4 * 3600_000); });
    status = 'success';
    visibility = 'visible';
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(10); });
    expect(onDone).toHaveBeenCalledWith('t', expect.objectContaining({ phase: 'success' }));
  });

  it('(a) computer sospeso: il salto dell\'orologio non consuma la durata massima', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    const onExpire = vi.fn();
    const tick = vi.fn(() => Promise.resolve({ changed: true }));
    const stop = startPoller(tick, { interval: 1000, maxDurationMs: 10_000, onExpire });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    vi.setSystemTime(Date.now() + 5 * 3600_000);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(onExpire).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(onExpire).toHaveBeenCalledTimes(1);
    stop();
  });

  it('(b) dati scaduti: `paused` lo dice e la prima attivita\' sulla pagina li fa ripartire', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    const loader = vi.fn(() => Promise.resolve({ n: loader.mock.calls.length }));
    const profile = { interval: 1000, maxInterval: 1000, maxDurationMs: 3000 };
    const { result } = renderHook(() => useResource(loader, { key: 'x', refreshInterval: profile }));
    for (let i = 0; i < 10; i += 1) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.paused).toBe(true);
    const calls = loader.mock.calls.length;
    await act(async () => { document.dispatchEvent(new Event('pointerdown')); await vi.advanceTimersByTimeAsync(10); });
    expect(loader.mock.calls.length).toBe(calls + 1);
    expect(result.current.paused).toBe(false);
    expect(read('components/common/ListState.jsx')).toMatch(/PollPausedNote resource=\{resource\}/);
  });

  it('(c) poll di una risorsa condivisa che cambia: niente backoff (il suo avviso non lo supera)', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z') });
    let n = 0;
    const load = vi.fn(() => Promise.resolve({ n: (n += 1) }));
    fake({ __poll: { load, freshMs: 60_000 } });
    const profile = { interval: 1000, maxInterval: 8000, maxDurationMs: 0 };
    renderHook(() => useSharedResource('__poll', { refreshInterval: profile }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    for (let i = 0; i < 10; i += 1) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    // Ogni giro porta un dato nuovo: ~1 richiesta al secondo, non 1 ogni 8 s.
    expect(load.mock.calls.length).toBeGreaterThanOrEqual(8);
  });
});

// ── A-K7 ───────────────────────────────────────────────────────────────────

describe('A-K7 — mappe /schemas e sorgenti a monte', () => {
  const response = { schemas: { e: [{ id: 'i', name: 'c', type: 'str', source_name: 'c' }] }, schema_states: {} };
  function setup(now) {
    const canvasStore = createCanvasStore();
    canvasStore.getState().init('p1', 'manipola');
    const columnStore = createColumnStore();
    const fetchSchemas = vi.fn(async () => response);
    const stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 50, ...(now ? { now } : {}) });
    canvasStore.getState().loadGraph(
      [{ id: 's', type: 'project_source', position: { x: 0, y: 0 }, data: { source_project_id: 'monte' } },
        { id: 'e', type: 'filter', position: { x: 300, y: 0 }, data: {} }],
      [{ id: 'x', source: 's', target: 'e' }],
    );
    return { canvasStore, columnStore, fetchSchemas, stop };
  }

  it('progetto a monte salvato o rieseguito: la mappa si richiede; il proprio salvataggio no', async () => {
    vi.useFakeTimers();
    const { fetchSchemas, stop } = setup();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    invalidateShared([['project', 'p1']]);
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    invalidateShared([['project', 'monte']]);
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    invalidateShared([['file', 'f1']]);
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchSchemas).toHaveBeenCalledTimes(3);
    stop();
  });

  it('riapertura dopo un cambio a monte (editor chiuso): la mappa in cache non vale', async () => {
    vi.useFakeTimers();
    const columnStore = createColumnStore();
    const canvasStore = createCanvasStore();
    canvasStore.getState().init('p1', 'manipola');
    const fetchSchemas = vi.fn(async () => response);
    const graph = () => canvasStore.getState().loadGraph([{ id: 'e', type: 'filter', position: { x: 0, y: 0 }, data: {} }], []);
    let stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 50 });
    graph();
    await vi.advanceTimersByTimeAsync(0);
    stop();
    invalidateShared([['connectionTables', 'k1']]);
    stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 50 });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    stop();
  });

  it('salvataggio: una mappa vecchia (oltre SCHEMA_FRESH_MS) non si allega e non si aspetta (conferma finale F6)', async () => {
    vi.useFakeTimers();
    let t = 0;
    const { canvasStore, columnStore, fetchSchemas, stop } = setup(() => t);
    await vi.advanceTimersByTimeAsync(0);
    const st = canvasStore.getState();
    const canvas = { nodes: st.nodes, edges: st.edges, mode: st.mode };
    const known = columnStore.getState().schemas;
    expect(derivedColumnsForSave('p1', canvas, { columnStore })).toBe(known);
    t = SCHEMA_FRESH_MS + 1;
    // Stessa firma, stesse sorgenti, solo vecchia: niente attesa di /schemas,
    // il salvataggio parte subito senza colonne derivate (le ricalcola il backend).
    expect(derivedColumnsForSave('p1', canvas, { columnStore })).toBeNull();
    // La mappa si rinnova in sottofondo.
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    stop();
  });
});

// ── A-K8 ───────────────────────────────────────────────────────────────────

describe('A-K8 — /auth/me con gruppo o ruolo cambiati', () => {
  it('le liste condivise si invalidano', async () => {
    const { MemoryRouter } = await import('react-router-dom');
    const { AuthProvider } = await import('../../src/context/AuthContext');
    fake({ projects: { load: () => Promise.resolve({ projects: [{ id: 'gruppo-a' }] }) } });
    sessionStorage.setItem('urby_token', 'tok');
    sessionStorage.setItem('urby_user', JSON.stringify({ username: 'a', role: 'admin', group_id: 'g1' }));
    await readShared('projects');
    getMeMock.mockResolvedValue({ username: 'a', role: 'admin', group_id: 'g2' });
    render(React.createElement(MemoryRouter, null, React.createElement(AuthProvider, null, 'x')));
    await act(async () => { await flush(); });
    expect(peekShared('projects')).toBeNull();
    // Profilo invariato: nessuna invalidazione.
    await readShared('projects');
    const before = invalidationCount('projects');
    getMeMock.mockResolvedValue({ username: 'a', role: 'admin', group_id: 'g2' });
    await act(async () => { window.dispatchEvent(new Event('focus')); await flush(); });
    expect(invalidationCount('projects')).toBe(before);
    sessionStorage.clear();
  });
});

// ── A-K9 ───────────────────────────────────────────────────────────────────

describe('A-K9 — generazione per voce; errore di rivalidazione visibile', () => {
  it('una scrittura su un\'altra risorsa non scarta la lettura in volo', async () => {
    const d = deferred();
    fake({ __a: { load: () => d.promise }, __b: { load: () => Promise.resolve('b') } });
    const pending = readShared('__a');
    invalidateShared('__b');
    d.resolve('a');
    await pending;
    expect(peekShared('__a')?.data).toBe('a');
  });

  it('aggiornamento in background fallito: dati restano con «non aggiornati» (stale)', async () => {
    vi.useFakeTimers({ now: new Date('2026-11-06T10:00:00Z'), toFake: ['Date'] });
    let fail = false;
    fake({ __r: { load: () => (fail ? Promise.reject(Object.assign(new Error('rete'), { response: { status: 500, data: {} } })) : Promise.resolve(['x'])), freshMs: 1000 } });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await readShared('__r');
    fail = true;
    vi.setSystemTime(Date.now() + 5000);
    const { result } = renderHook(() => useSharedResource('__r'));
    await waitFor(() => expect(result.current.stale).toBe(true));
    expect(result.current.data).toEqual(['x']);
  });

  it('la generazione globale non esiste piu\'', () => {
    expect(read('utils/sharedCache.js')).not.toMatch(/\blet generation\b/);
  });
});

// ── C-K10 (client) ─────────────────────────────────────────────────────────

describe('C-K10 — conteggi non disponibili detti, gli altri restano', () => {
  it('unavailable -> tra i dati non caricati; i numeri presenti si usano', async () => {
    const { useProjectListData } = await import('../../src/components/projects/useProjectListData');
    fake({
      projects: { load: () => Promise.resolve({ projects: [] }) },
      connections: { load: () => Promise.resolve({ connections: [] }) },
      dashboardCounts: { load: () => Promise.resolve({ api_connections: null, datasources: 3, schedules_active: 1, schedules_by_project: { p: 1 }, unavailable: ['api_connections'] }) },
      files: { load: () => Promise.resolve({ files: [] }) },
      arcgisConnections: { load: () => Promise.resolve({ connections: [] }) },
      eseguibili: { load: () => Promise.resolve([]) },
      latestRunByProject: { load: () => Promise.resolve({ statuses: {} }) },
    });
    const { result } = renderHook(() => useProjectListData());
    await waitFor(() => expect(result.current.loading).toBe(false));
    // Conferma finale G11: non disponibile = null («—»), mai 0.
    expect(result.current.counts).toMatchObject({ api_connections: null, datasources: 3, schedules_active: 1 });
    expect(result.current.failures).toEqual(['conteggio delle API esterne']);
  });
});

// Le cache del motore usate nei test non restano tra un file e l'altro.
afterEach(() => resetSharedCache());
