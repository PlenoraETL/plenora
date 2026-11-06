/**
 * UI — conferma finale (06/11/2026), elenco docs/audits/ui-conferma-finale-
 * 2026-11-06.md. Una prova per voce, che fallirebbe senza la correzione
 * (F1/F2 in virtualLists.contract, F6 in uiConfermaDataCache.contract, G7
 * anche in ScheduleWizard.contract; il lato server in backend/tests2):
 *
 *  F5  la fine di un task invalida anche se la pagina che l'ha avviato si
 *      smonta: poller di fondo (profilo `taskBackground`) per i task orfani;
 *  F7  pagine del cursore dei file della dimensione massima del server;
 *  F8  una sola richiesta di pulizia (l'ordine e' del server); 202 = in coda;
 *  G1  ogni finestra al bisogno ha la sua boundary: una rotta non tocca le
 *      altre, «Chiudi» chiude davvero e riaprendo si riprova, «Ricarica»
 *      passa dalla domanda delle modifiche non salvate;
 *  G2  «Riordina» non disponibile lo dice all'utente;
 *  G4  pagina nella cache del browser: niente pulizia, solo annotata;
 *  G5  «ha un manifest» = manifest non vuoto (stessa regola del backend);
 *  G6  il frontend accetta cio' che lo scheduler accetta, con l'avviso;
 *  G8  una tabella delle chiavi di storage (prefisso unico, per utente,
 *      pulizia all'uscita, migrazione) e un solo registro «cosa si perde»;
 *  G9  Maiusc+C attiva «Collega» (scelta dichiarata);
 *  G10 la pulizia di chiusura si annota e riparte se non confermata;
 *  G11 un conteggio non disponibile e' «—», non 0.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, act, renderHook, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { resetSession } from '../../src/utils/session';
import { registerTaskLaunch, orphanTaskIds, taskProfileName } from '../../src/utils/taskRegistry';
import { pollTask } from '../../src/utils/taskPoller';
import { POLL_PROFILES } from '../../src/utils/poller';
import { readShared, peekShared, defineSharedResources } from '../../src/utils/sharedCache';
import { SHARED_RESOURCES } from '../../src/utils/sharedResources';
import { FILES_PAGE_SIZE } from '../../src/api/files';
import { lazyWindow } from '../../src/components/common/LazyWindow';
import { reloadAfterLeaveQuestion } from '../../src/utils/chunkReload';
import { registerLeaveProbe, leaveDialog, mustAskBeforeLeaving, LEAVE_DIALOG } from '../../src/utils/leaveGuard';
import { usesRunForm } from '../../src/components/eseguibili/runForm';
import { cronProblem, cronWarning, describeCron, nextCronRuns } from '../../src/utils/cron';
import { STORAGE_KEYS, storageKey } from '../../src/utils/browserStorage';
import { parkExitCleanup, parkedExitCleanups } from '../../src/utils/exitCleanups';
import { matchesShortcut } from '../../src/utils/shortcuts';
import { dashboardCounts, formatCount } from '../../src/components/projects/useProjectListData';
import { ConfirmProvider } from '../../src/context/ConfirmContext';

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8');
const noComments = (c) => c.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory()
  ? walk(path.join(dir, e.name)) : (/\.(jsx?|tsx?)$/.test(e.name) ? [path.join(dir, e.name)] : [])));
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const SOURCES = walk(SRC).map(f => ({ file: rel(f), code: noComments(fs.readFileSync(f, 'utf8')) }));
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const asUser = (id) => window.sessionStorage.setItem('urby_user', JSON.stringify({ user_id: id }));

beforeEach(() => { resetSession(); window.localStorage.clear(); window.sessionStorage.clear(); });
afterEach(() => { vi.useRealTimers(); defineSharedResources(SHARED_RESOURCES); resetSession(); vi.restoreAllMocks(); });

// ── F5 ─────────────────────────────────────────────────────────────────────

describe('F5 — la fine del task invalida anche a pagina smontata', () => {
  it('la pagina smette di seguire il task: lo segue il poller di fondo fino alla fine, poi invalida e toglie la voce', async () => {
    vi.useFakeTimers();
    defineSharedResources({ project: { freshMs: 60_000, load: (id) => Promise.resolve({ id }) },
      latestRunByProject: { freshMs: 60_000, load: () => Promise.resolve({ statuses: {} }) } });
    await readShared('project', ['p1']);
    registerTaskLaunch('post', '/projects/p1/execute', { async: true, task_id: 't1' });
    let status = 'progress';
    const getStatus = vi.fn(() => Promise.resolve({ status, result: {} }));
    const stop = pollTask('t1', { getStatus, onDone: vi.fn() });
    await act(async () => { await flush(); });
    expect(orphanTaskIds()).toEqual([]);
    stop(); // l'utente esce dall'editor durante l'esecuzione
    expect(orphanTaskIds()).toEqual(['t1']);
    status = 'success';
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_PROFILES.taskBackground.interval + 10); await flush(); });
    expect(peekShared('project', ['p1'])).toBeNull();
    expect(taskProfileName('t1')).toBeNull();
    expect(orphanTaskIds()).toEqual([]);
  });

  it('il poller di fondo rispetta la tabella dei profili e lascia il task a una pagina che lo riprende', async () => {
    expect(POLL_PROFILES.taskBackground.maxDurationMs).toBeGreaterThanOrEqual(POLL_PROFILES.enrichTask.maxDurationMs);
    vi.useFakeTimers();
    registerTaskLaunch('post', '/projects/p1/execute', { async: true, task_id: 't2' });
    const background = vi.fn(() => Promise.resolve({ status: 'progress', result: {} }));
    const stop = pollTask('t2', { getStatus: background, onDone: vi.fn() });
    await act(async () => { await flush(); });
    stop();
    const page = vi.fn(() => Promise.resolve({ status: 'progress', result: {} }));
    const stop2 = pollTask('t2', { getStatus: page, onDone: vi.fn() });
    background.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_PROFILES.taskBackground.interval * 3); await flush(); });
    expect(background).not.toHaveBeenCalled();
    stop2();
  });
});

// ── F7, F8 ─────────────────────────────────────────────────────────────────

describe('F7 — cursore dei file a pagine della dimensione massima del server', () => {
  it('FILES_PAGE_SIZE = il `le` di `limit` di GET /files', () => {
    const router = fs.readFileSync(path.resolve(SRC, '../../backend/app/core/files/router.py'), 'utf8');
    const listFiles = router.slice(router.indexOf('def list_files('));
    const max = Number(listFiles.match(/limit:\s*Annotated\[int,\s*Query\(ge=1,\s*le=(\d+)\)\]/)[1]);
    expect(FILES_PAGE_SIZE).toBe(max);
    expect(read('api/files.js')).toMatch(/pageSize:\s*FILES_PAGE_SIZE/);
  });
});

describe('F8 — una richiesta di pulizia, l\'ordine e\' del server', () => {
  it('nessun annullamento separato nella pulizia di chiusura; 202 = in coda, non parziale', async () => {
    const tasks = noComments(read('api/tasks.js'));
    const release = tasks.slice(tasks.indexOf('export const releaseTaskOnPageExit'), tasks.indexOf('export const parkTaskCleanup'));
    expect(release).not.toMatch(/`\/tasks\/\$\{[^}]+\}`/);
    expect((release.match(/sendOnPageExit\(/g) || []).length).toBe(1);
    const { default: http } = await import('../../src/api/http');
    vi.spyOn(http, 'delete').mockResolvedValue({ status: 202, data: { status: 'deferred', task_id: 't' } });
    const { cleanupTaskCompletely } = await import('../../src/api/tasks');
    expect(await cleanupTaskCompletely('t')).toMatchObject({ partialCleanup: false, deferred: true });
  });
});

// ── G1, G2 ─────────────────────────────────────────────────────────────────

describe('G1 — una boundary per finestra al bisogno', () => {
  const EDITORS = ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx'];

  it('le finestre al bisogno passano tutte da lazyWindow; niente boundary unica', () => {
    const offenders = SOURCES.filter(({ file, code }) => file !== 'utils/chunkReload.js'
      && file !== 'components/common/LazyWindow.jsx' && /\b(React\.)?lazy\(/.test(code)).map(({ file }) => file);
    expect(offenders).toEqual([]);
    for (const f of EDITORS) {
      const code = noComments(read(f));
      expect({ f, shared: /ModalErrorBoundary/.test(code) }).toEqual({ f, shared: false });
      expect(code).toMatch(/const ExecutionModal = lazyWindow\(/);
    }
  });

  it('una finestra rotta non porta via le altre; «Chiudi» chiude davvero e riaprendo si riprova', async () => {
    globalThis.__frontendConsolePolicy.allowError = true; // React e la boundary scrivono l'errore
    let fail = true;
    const loader = vi.fn(() => (fail
      ? Promise.reject(new TypeError('Failed to fetch dynamically imported module'))
      : Promise.resolve({ default: () => <p>Versioni aperte</p> })));
    const Versions = lazyWindow(loader, 'Versioni');
    const Run = lazyWindow(() => Promise.resolve({ default: () => <p>Esecuzione aperta</p> }), 'Esecuzione');
    function Page() {
      const [show, setShow] = React.useState(true);
      return (
        <ConfirmProvider>
          <Versions isOpen={show} onClose={() => setShow(false)} />
          <Run isOpen onClose={() => {}} />
          <button type="button" onClick={() => setShow(true)}>Apri versioni</button>
        </ConfirmProvider>
      );
    }
    render(<Page />);
    expect(await screen.findByText('Esecuzione aperta')).toBeTruthy();
    expect(await screen.findByText(/La finestra «Versioni» non si è aperta/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ricarica la pagina' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Chiudi' }));
    expect(screen.queryByText(/non si è aperta/)).toBeNull();
    expect(screen.getByText('Esecuzione aperta')).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Apri versioni' }));
    expect(await screen.findByText('Versioni aperte')).toBeTruthy();
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('«Ricarica» passa dalla domanda delle modifiche non salvate', async () => {
    const win = { location: { reload: vi.fn() } };
    const off = registerLeaveProbe(() => true);
    try {
      const no = vi.fn(() => Promise.resolve(false));
      expect(await reloadAfterLeaveQuestion(no, win)).toBe(false);
      expect(no).toHaveBeenCalledWith(LEAVE_DIALOG);
      expect(win.location.reload).not.toHaveBeenCalled();
      expect(await reloadAfterLeaveQuestion(() => Promise.resolve(true), win)).toBe(true);
      expect(win.location.reload).toHaveBeenCalledTimes(1);
    } finally { off(); }
  });
});

describe('G2 — «Riordina» non disponibile lo dice', () => {
  it('l\'errore del caricamento di dagre apre la domanda con «Ricarica la pagina»; il commento dice il vero', () => {
    const shell = noComments(read('components/editor/EditorShell.jsx'));
    const handler = shell.slice(shell.indexOf('const handleAutoLayout'), shell.indexOf('}, [isReadOnly, editNodes, reactFlow, layoutConfirm]);'));
    expect(handler).toMatch(/import\('\.\.\/canvas\/autoLayout'\)[\s\S]*\.catch\([\s\S]*layoutConfirm\(LAYOUT_UNAVAILABLE_DIALOG\)[\s\S]*reloadAfterLeaveQuestion\(layoutConfirm\)/);
    expect(shell).toMatch(/title: 'Riordina non disponibile'[\s\S]*confirmText: 'Ricarica la pagina'/);
    expect(read('utils/chunkReload.js')).not.toMatch(/\(dagre, js-yaml, Sentry\) mostrano il loro errore/);
  });
});

// ── G4, G10, F8 (pagina) ───────────────────────────────────────────────────

describe('G4 e G10 — pulizia di chiusura annotata, pagina nella cache', () => {
  const api = () => import('../../src/api/tasks');

  it('G10: la pulizia si annota PRIMA di partire; se la keepalive non parte resta annotata e riparte all\'apertura', async () => {
    asUser('esec');
    window.sessionStorage.setItem('urby_token', 'tok');
    const { releaseTaskOnPageExit, flushParkedExitCleanups } = await api();
    vi.stubGlobal('fetch', vi.fn(() => { throw new TypeError('preflight non consentito'); }));
    try {
      expect(releaseTaskOnPageExit('t9')).toBe(false);
    } finally { vi.unstubAllGlobals(); }
    expect(parkedExitCleanups()).toEqual(['t9']);
    const { default: http } = await import('../../src/api/http');
    const del = vi.spyOn(http, 'delete');
    // Rete giu': resta annotata.
    del.mockRejectedValueOnce(new Error('Network Error'));
    expect(await flushParkedExitCleanups()).toBe(0);
    expect(parkedExitCleanups()).toEqual(['t9']);
    // Il server risponde (anche 404: task gia' sparito): tolta.
    del.mockRejectedValueOnce(Object.assign(new Error('404'), { response: { status: 404 } }));
    expect(await flushParkedExitCleanups()).toBe(1);
    expect(parkedExitCleanups()).toEqual([]);
    expect(del).toHaveBeenLastCalledWith('/tasks/t9/full-cleanup');
  });

  it('G4: pagehide con persisted non pulisce (solo `persisted`); al ritorno il risultato resta, o non c\'e\' piu\' se pulito altrove', async () => {
    vi.resetModules();
    vi.doMock('../../src/api', () => ({
      downloadEseguibileOutput: vi.fn(),
      getTaskStatus: vi.fn(() => Promise.resolve({ status: 'success', progress: 100, result: {} })),
      cancelTask: vi.fn(),
    }));
    const { useEseguibileRun } = await import('../../src/components/eseguibili/useEseguibileRun');
    const answers = [true, false];
    const onCleanup = vi.fn((id, opts) => (opts.pageReturn ? answers.shift() : undefined));
    const { result } = renderHook(() => useEseguibileRun({ eseguibileId: 'e1', onCleanup }));
    await act(async () => { await result.current.start(() => Promise.resolve({ async: true, task_id: 't1' })); });
    await waitFor(() => expect(result.current.resultState).toBe('on_server'), { timeout: 3000 });
    const hide = (persisted) => act(() => { window.dispatchEvent(Object.assign(new Event('pagehide'), { persisted })); });
    const show = () => act(() => { window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true })); });
    hide(true);
    expect(onCleanup).toHaveBeenLastCalledWith('t1', { pageExit: true, persisted: true });
    expect(result.current.resultState).toBe('on_server');
    show(); // annotazione ancora li': il risultato resta
    expect(onCleanup).toHaveBeenLastCalledWith('t1', { pageReturn: true });
    expect(result.current.resultState).toBe('on_server');
    hide(true);
    show(); // un'altra apertura di /run l'ha gia' pulito
    expect(result.current.resultState).toBe('gone');
    vi.doUnmock('../../src/api');
    vi.resetModules();
  });

  it('EsecutoreHome: annota con la pagina in cache, toglie al ritorno, rimanda all\'apertura', () => {
    const home = noComments(read('pages/EsecutoreHome.jsx'));
    expect(home).toMatch(/if \(pageReturn\) return unparkExitCleanup\(taskId\)/);
    expect(home).toMatch(/if \(persisted\) parkTaskCleanup\(taskId\);\s*else releaseTaskOnPageExit\(taskId\);/);
    expect(home).toMatch(/useEffect\(\(\) => \{ flushParkedExitCleanups\(\)/);
    // Il commento della keepalive dice il vero sull'origine (G10).
    expect(read('api/http.js')).toMatch(/Stessa ORIGINE solo se apiBaseUrl e' relativo/);
  });
});

// ── G5, G6, G9, G11 ────────────────────────────────────────────────────────

describe('G5 — ha un manifest = manifest non vuoto', () => {
  it.each([
    [{ has_manifest: false, manifest: { inputs: [] } }, false],
    [{ has_manifest: true }, true],
    [{ manifest: {} }, false],
    [{ manifest: null }, false],
    [{ manifest: { inputs: [] } }, true],
  ])('%j -> %s', (e, expected) => { expect(usesRunForm(e)).toBe(expected); });
});

describe('G6 — si accetta cio\' che lo scheduler accetta, con l\'avviso', () => {
  const { cases } = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/cronCases.json'), 'utf8'));
  const warned = cases.filter(c => c.warning);
  it('i casi con avviso sono validi, hanno l\'avviso e niente descrizione ne\' esecuzioni', () => {
    expect(warned.map(c => c.expr).sort()).toEqual(['* * * * 7-7', '0 0 1-L * *']);
    expect(cases.some(c => c.stricter)).toBe(false);
    for (const { expr } of warned) {
      expect({ expr, problem: cronProblem(expr) }).toEqual({ expr, problem: null });
      expect(cronWarning(expr)).toMatch(/\S/);
      expect(describeCron(expr)).toBeNull();
      expect(nextCronRuns(expr)).toEqual([]);
    }
    expect(cronWarning('0 0 * * 0')).toBeNull();
  });
});

describe('G9 — Maiusc+C attiva «Collega» (scelta dichiarata)', () => {
  it('c, C (Maiusc o Blocco maiuscole) sì; Ctrl/⌘+C e Alt+C no', () => {
    const ev = (o) => ({ key: 'c', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...o });
    expect(matchesShortcut('connect', ev({}))).toBe(true);
    expect(matchesShortcut('connect', ev({ key: 'C', shiftKey: true }))).toBe(true);
    expect(matchesShortcut('connect', ev({ ctrlKey: true }))).toBe(false);
    expect(matchesShortcut('connect', ev({ altKey: true }))).toBe(false);
    expect(read('utils/shortcuts.js')).toMatch(/Scelta dichiarata \(conferma finale G9\)/);
  });
});

describe('G11 — un conteggio non disponibile e\' «—», non 0', () => {
  it('unavailable o risposta mancante -> null; formatCount -> «—»', () => {
    expect(dashboardCounts({ api_connections: 4, datasources: 0, schedules_active: 2, schedules_by_project: {}, unavailable: ['schedules'] }))
      .toEqual({ api_connections: 4, datasources: 0, schedules_active: null, schedules_by_project: null });
    expect(dashboardCounts(null)).toEqual({ api_connections: null, datasources: null, schedules_active: null, schedules_by_project: null });
    expect(formatCount(null)).toBe('—');
    expect(formatCount(0)).toBe('0');
    const page = noComments(read('pages/ProjectList.jsx'));
    expect(page).toMatch(/\.filter\(t => t\.val === null \|\| t\.val > 0\)/);
    expect(page).toMatch(/\{formatCount\(val\)\}/);
  });
});

// ── G8 ─────────────────────────────────────────────────────────────────────

describe('G8 — una tabella delle chiavi di storage', () => {
  const DIRECT_ACCESS = {
    'utils/browserStorage.js': 'il modulo delle chiavi',
    'utils/localDrafts.js': 'copie di sicurezza: chiavi e prefissi presi da STORAGE_KEYS',
    'utils/chunkReload.js': 'finestra iniettabile per i test; chiave da STORAGE_KEYS',
    'api/http.js': 'token della sessione (sessionStorage), confine dell\'autenticazione',
    'context/AuthContext.jsx': 'token e utente della sessione (sessionStorage), confine dell\'autenticazione',
  };

  it('nessuna lettura o scrittura dello storage fuori dai file dichiarati', () => {
    const found = SOURCES.filter(({ code }) => /\b(getItem|setItem|removeItem)\s*\(/.test(code)).map(({ file }) => file).sort();
    expect(found).toEqual(Object.keys(DIRECT_ACCESS).sort());
  });

  it('ogni riga: prefisso unico, area, proprietario, uscita e motivo; chiavi solo dalla tabella', () => {
    for (const [name, row] of Object.entries(STORAGE_KEYS)) {
      expect({ name, ok: String(row.key || row.prefix).startsWith('plenora:') }).toEqual({ name, ok: true });
      expect(['local', 'session']).toContain(row.area);
      expect(['browser', 'user']).toContain(row.owner);
      expect(['keep', 'clear']).toContain(row.logout);
      expect(row.why.length).toBeGreaterThan(15);
    }
    // Nessuna chiave di storage scritta a mano fuori dalla tabella.
    const literals = SOURCES.filter(({ file }) => file !== 'utils/browserStorage.js')
      .filter(({ code }) => /['"`]plenora(-theme|-layout:|\.palette\.|:(theme|layout:|palette:|draft:|tab:|saved:|deleted:|tab-id|chunk-reload|exit-cleanup))/.test(code))
      .map(({ file }) => file);
    expect(literals).toEqual([]);
  });

  it('all\'uscita si toglie la memoria d\'uso (palette) di tutti; restano preferenze, copie di sicurezza e pulizie da finire', () => {
    const keep = [storageKey('theme'), storageKey('layout', 'x'), storageKey('draft', 'u1', 'p', 't'), storageKey('exitCleanup', 'u1')];
    const gone = [storageKey('palette', 'u1', 'estrai'), storageKey('palette', 'u2', 'manipola')];
    [...keep, ...gone].forEach(k => window.localStorage.setItem(k, '1'));
    resetSession();
    for (const k of keep) expect({ k, v: window.localStorage.getItem(k) }).toEqual({ k, v: '1' });
    for (const k of gone) expect({ k, v: window.localStorage.getItem(k) }).toEqual({ k, v: null });
  });

  it('le chiavi di prima si spostano una volta (tema e layout)', async () => {
    window.localStorage.setItem('plenora-theme', 'light');
    vi.resetModules();
    const { useTheme } = await import('../../src/hooks/useTheme');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
    expect(window.localStorage.getItem('plenora-theme')).toBeNull();
    expect(window.localStorage.getItem('plenora:theme')).toBe('light');
    window.localStorage.setItem('plenora-layout:editor-panel-width', '480');
    const { useStoredState, LAYOUT_KEYS } = await import('../../src/hooks/useStoredState');
    const width = renderHook(() => useStoredState(LAYOUT_KEYS.editorPanelWidth, 360));
    expect(width.result.current[0]).toBe(480);
    expect(window.localStorage.getItem('plenora-layout:editor-panel-width')).toBeNull();
    vi.resetModules();
  });

  it('un solo registro di «cosa si perde uscendo» e un solo prompt di chiusura', () => {
    const listeners = SOURCES.filter(({ code }) => /addEventListener\(\s*'beforeunload'/.test(code)).map(({ file }) => file).sort();
    // useLocalDraft scrive la copia in sospeso uscendo: non chiede niente.
    expect(listeners).toEqual(['hooks/useLocalDraft.js', 'utils/leaveGuard.js']);
    expect(noComments(read('components/eseguibili/useEseguibileRun.js'))).toMatch(/registerLeaveProbe\(\(\) => atRiskRef\.current, RUN_LEAVE_DIALOG\)/);
    let risky = false;
    const off = registerLeaveProbe(() => risky, { title: 'Esecuzione non conclusa' });
    try {
      expect(mustAskBeforeLeaving()).toBe(false);
      risky = true;
      expect(leaveDialog().title).toBe('Esecuzione non conclusa');
      const ev = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(ev);
      expect(ev.defaultPrevented).toBe(true);
    } finally { off(); }
    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });

  it('le pulizie annotate sono per utente', () => {
    asUser('anna');
    parkExitCleanup('t1');
    asUser('bruno');
    expect(parkedExitCleanups()).toEqual([]);
    asUser('anna');
    expect(parkedExitCleanups()).toEqual(['t1']);
  });
});
