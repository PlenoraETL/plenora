import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  // Server che non versiona: PUT e GET senza canvas_version (utils/canvasVersion.tokenAfterWrite).
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));
vi.mock('../../src/api/enrichment', async (orig) => ({
  ...(await orig()),
  preflightEnrichment: vi.fn(),
}));

import useCanvasStore from '../../src/store/useCanvasStore';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import { preflightEnrichment } from '../../src/api/enrichment';
import { BEFORE_RUN_REASONS, beforeRunReasons } from '../../src/utils/beforeRun';

/**
 * Contract Blocco A, regola 5 (F3): UNA finestra «Prima di avviare» per i
 * tre editor, un solo percorso (editor/PipelineRunner). Si apre solo se
 * serve (input, stima di Arricchisci da confermare, avvisi, opzioni dello
 * staff); altrimenti Esegui parte con un clic. Ogni editor x ogni caso.
 */
const SRC = path.resolve(__dirname, '../../src');
const files = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(d => {
  const p = path.join(dir, d.name);
  return d.isDirectory() ? files(p) : (/\.(jsx?|tsx?)$/.test(d.name) ? [p] : []);
});
const code = (p) => fs.readFileSync(p, 'utf-8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const rel = (p) => path.relative(SRC, p).split(path.sep).join('/');
const usersOf = (re) => files(SRC).filter(p => re.test(code(p))).map(rel).sort();

const n = (id, type, data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const EDITORS = {
  manipola: [n('s', 'project_source'), n('o', 'result')],
  estrai: [n('t', 'tableNode', { tableName: 'vie' }), n('o', 'resultNode')],
  arricchisci: [n('s', 'project_source'), n('a', 'api_enrich', { title: 'Geocodifica' }), n('o', 'result')],
};
const INPUT = n('i', 'project_input', { input_key: 'anno', label: 'Anno', input_kind: 'text' });
const preflight = (over = {}, node = {}) => ({
  valid: true,
  preparation_fingerprint: 'f1',
  nodes: [{ node_id: 'a', input_rows: 4, input_rows_status: 'exact', sampled: false, sampled_rows: 4, effective_rows: 3, status: 'estimated', api_calls: 3, valid: true, reason: 'Stima senza ripetizioni', ...node }],
  checks: { external_dependencies: 'not_guaranteed' },
  ...over,
});
const changedError = () => Object.assign(new Error('Preparazione cambiata: canvas, input, connessioni o opzioni diversi dal preflight'), {
  response: { status: 409, data: { detail: { error: 'validation_error', reason: 'preparation_changed', message: 'Preparazione cambiata' } } },
});

let onExecute;
function openEditor(mode, extraNodes = []) {
  const st = useCanvasStore.getState();
  st.reset();
  st.setReadOnly(false);
  st.init('p1', mode);
  st.loadGraph([...EDITORS[mode], ...extraNodes], [], { canvasVersion: 'c1:a' });
  onExecute = vi.fn(async () => {});
  st.setEditorActions({ onExecute });
  render(<PipelineRunner user={{ role: 'user' }} />);
}
const run = () => act(async () => { await useCanvasStore.getState().getEditorAction('runPipeline')(); });
const dialog = () => screen.queryByRole('dialog');
const launchBtn = () => screen.getByRole('button', { name: /Conferma e avvia|Avvia l'esecuzione/ });

beforeEach(() => { preflightEnrichment.mockReset(); });

describe('the reasons rule (one table)', () => {
  it('each reason is declared and opens the window; none = one click', () => {
    expect(BEFORE_RUN_REASONS).toEqual(['inputs', 'staff', 'estimate', 'blocking', 'changed']);
    expect(beforeRunReasons({})).toEqual([]);
    expect(beforeRunReasons({ preflight: preflight() })).toEqual(['estimate']);
    expect(beforeRunReasons({ preflight: preflight({}, { api_calls: 0 }) })).toEqual([]);
    // Chiamate non conoscibili: si conferma.
    expect(beforeRunReasons({ preflight: preflight({}, { api_calls: null }) })).toEqual(['estimate']);
    expect(beforeRunReasons({ preflight: preflight({ valid: false }, { api_calls: 0 }) })).toEqual(['blocking']);
    expect(beforeRunReasons({ preflightError: 'rete' })).toEqual(['blocking']);
    expect(beforeRunReasons({ changedMessage: 'x', inputDefinitions: [{ input_key: 'k' }], user: { role: 'superadmin' } })).toEqual(['inputs', 'staff', 'changed']);
  });
});

describe.each(Object.keys(EDITORS))('editor %s', (mode) => {
  const arricchisci = mode === 'arricchisci';

  it('no input: Esegui starts with one click (Arricchisci: only when there is nothing to confirm)', async () => {
    if (arricchisci) preflightEnrichment.mockResolvedValue(preflight({}, { api_calls: 0 }));
    openEditor(mode);
    await run();
    expect(dialog()).toBeNull();
    expect(onExecute).toHaveBeenCalledTimes(1);
    expect(onExecute).toHaveBeenCalledWith('o', arricchisci
      ? { force_recalculate: false, preparation_fingerprint: 'f1' }
      : { force_recalculate: false });
    if (arricchisci) expect(preflightEnrichment).toHaveBeenCalledWith('p1', { inputs: {}, force_recalculate: false });
    else expect(preflightEnrichment).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().runState).toBe('idle');
  });

  it('runtime input: the window asks it, Avvia waits for it, the same inputs go to the estimate and to the run', async () => {
    if (arricchisci) preflightEnrichment.mockResolvedValue(preflight());
    openEditor(mode, [INPUT]);
    await run();
    expect(dialog()).toBeTruthy();
    expect(onExecute).not.toHaveBeenCalled();
    // Una sola esecuzione alla volta: con la finestra aperta resta 'starting'.
    expect(useCanvasStore.getState().runState).toBe('starting');
    expect(launchBtn().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Anno/), { target: { value: '2026' } });
    if (arricchisci) {
      await waitFor(() => expect(preflightEnrichment).toHaveBeenCalledWith('p1', { inputs: { anno: '2026' }, force_recalculate: false }));
      await screen.findByText('3 chiamate stimate');
    }
    await waitFor(() => expect(launchBtn().disabled).toBe(false));
    await act(async () => { fireEvent.click(launchBtn()); });
    expect(onExecute).toHaveBeenCalledWith('o', arricchisci
      ? { force_recalculate: false, inputs: { anno: '2026' }, preparation_fingerprint: 'f1' }
      : { force_recalculate: false, inputs: { anno: '2026' } });
    expect(dialog()).toBeNull();
    expect(useCanvasStore.getState().runState).toBe('idle');
  });

  it('Annulla closes the window and nothing starts', async () => {
    if (arricchisci) preflightEnrichment.mockResolvedValue(preflight());
    openEditor(mode, [INPUT]);
    await run();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Annulla' })); });
    expect(dialog()).toBeNull();
    expect(onExecute).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().runState).toBe('idle');
  });

  it('staff options (superadmin): the same window, intermediate_mode goes to the run', async () => {
    if (arricchisci) preflightEnrichment.mockResolvedValue(preflight({}, { api_calls: 0 }));
    const st = useCanvasStore.getState();
    st.reset(); st.setReadOnly(false); st.init('p1', mode);
    st.loadGraph(EDITORS[mode], []);
    onExecute = vi.fn(async () => {});
    st.setEditorActions({ onExecute });
    render(<PipelineRunner user={{ role: 'superadmin' }} />);
    await run();
    const select = screen.getByLabelText(/intermediate_mode/);
    expect([...select.querySelectorAll('option')].map(o => o.value)).toEqual(['', 'metadata_only', 'full_artifact']);
    fireEvent.change(select, { target: { value: 'full_artifact' } });
    await act(async () => { fireEvent.click(launchBtn()); });
    expect(onExecute.mock.calls[0][1]).toMatchObject({ intermediate_mode: 'full_artifact' });
  });
});

describe('Arricchisci: estimate, confirmation, blocking warnings, 409', () => {
  it('estimate to confirm: per API node rows in (exact/estimated/unknown, sampled), calls and reasons', async () => {
    preflightEnrichment.mockResolvedValue(preflight({}, { input_rows: 10000, input_rows_status: 'estimated', sampled: true, sampled_rows: 10000 }));
    openEditor('arricchisci');
    await run();
    expect(dialog()).toBeTruthy();
    expect(screen.getByText('Geocodifica')).toBeTruthy();
    expect(screen.getByText(/10\.000 righe in ingresso \(stimate, su un campione di 10\.000 righe\)/)).toBeTruthy();
    expect(screen.getByText('3 chiamate stimate')).toBeTruthy();
    expect(screen.getByText('Stima senza ripetizioni')).toBeTruthy();
    expect(onExecute).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(launchBtn()); });
    expect(onExecute).toHaveBeenCalledWith('o', { force_recalculate: false, preparation_fingerprint: 'f1' });
  });

  it('rows that cannot be known before the run are said so; unknown calls are confirmed', async () => {
    preflightEnrichment.mockResolvedValue(preflight({}, { input_rows: null, input_rows_status: 'unknown', api_calls: null }));
    openEditor('arricchisci');
    await run();
    expect(screen.getByText(/righe in ingresso non conoscibili/)).toBeTruthy();
    expect(screen.getByText(/chiamate non conoscibili/)).toBeTruthy();
  });

  it('a preparation that rejects the canvas blocks Avvia (warning in the same window)', async () => {
    preflightEnrichment.mockResolvedValue(preflight({ valid: false }, { valid: false, message: 'Parametro obbligatorio senza colonna' }));
    openEditor('arricchisci');
    await run();
    expect(screen.getByText(/impediscono di avviare/)).toBeTruthy();
    expect(screen.getByText(/Parametro obbligatorio senza colonna/)).toBeTruthy();
    expect(launchBtn().disabled).toBe(true);
  });

  it('a failed estimate is a blocking warning with the server message', async () => {
    preflightEnrichment.mockRejectedValue(new Error('Sorgente non disponibile'));
    globalThis.__frontendConsolePolicy.allowError = true;
    openEditor('arricchisci');
    await run();
    expect(screen.getByText(/Stima non riuscita: Sorgente non disponibile/)).toBeTruthy();
    expect(launchBtn().disabled).toBe(true);
  });

  it('409 preparation_changed: the estimate is redone and the window shows why', async () => {
    preflightEnrichment
      .mockResolvedValueOnce(preflight())
      .mockResolvedValueOnce(preflight({ preparation_fingerprint: 'f2' }, { api_calls: 5 }));
    openEditor('arricchisci');
    await run();
    onExecute.mockRejectedValueOnce(changedError());
    await act(async () => { fireEvent.click(launchBtn()); });
    expect(preflightEnrichment).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/Preparazione cambiata/)).toBeTruthy();
    expect(screen.getByText('5 chiamate stimate')).toBeTruthy();
    expect(useCanvasStore.getState().runState).toBe('starting');
    await act(async () => { fireEvent.click(launchBtn()); });
    expect(onExecute).toHaveBeenLastCalledWith('o', { force_recalculate: false, preparation_fingerprint: 'f2' });
    expect(useCanvasStore.getState().runState).toBe('idle');
  });
});

describe('failed estimate: Italian message and «Riprova» (also without runtime inputs)', () => {
  const FAILURES = {
    rete: [Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' }), /Impossibile contattare il server/],
    '4xx': [Object.assign(new Error('Request failed with status code 422'), { response: { status: 422, data: { detail: { error: 'validation_error', message: 'Colonna della chiave assente' } } } }), /Colonna della chiave assente/],
    '5xx': [Object.assign(new Error('Request failed with status code 500'), { response: { status: 500, data: {} } }), /errore interno/],
  };
  it.each(Object.keys(FAILURES))('%s: message in Italian, Riprova redoes the estimate and Avvia comes back', async (kind) => {
    const [error, message] = FAILURES[kind];
    globalThis.__frontendConsolePolicy.allowError = true;
    preflightEnrichment.mockRejectedValueOnce(error).mockResolvedValueOnce(preflight());
    openEditor('arricchisci');
    await run();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(message);
    expect(alert.textContent).not.toMatch(/Network Error|Request failed/);
    expect(launchBtn().disabled).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Riprova' })); });
    expect(preflightEnrichment).toHaveBeenCalledTimes(2);
    expect(preflightEnrichment).toHaveBeenLastCalledWith('p1', { inputs: {}, force_recalculate: false });
    await screen.findByText('3 chiamate stimate');
    expect(launchBtn().disabled).toBe(false);
  });
});

describe('open window = runState starting, for every path that opens it', () => {
  // onExecute come useExecution: 'running' durante l'avvio, 'idle' se rifiutato.
  const realisticExecute = (outcomes) => vi.fn(async () => {
    const st = useCanvasStore.getState();
    st.setRunState('running');
    const next = outcomes.shift();
    st.setRunState('idle');
    if (next) throw next;
  });
  const PATHS = {
    'Esegui with reasons (runtime input)': async () => {
      openEditor('manipola', [INPUT]);
      await run();
    },
    '409 after a direct launch (nothing to confirm)': async () => {
      preflightEnrichment
        .mockResolvedValueOnce(preflight({}, { api_calls: 0 }))
        .mockResolvedValueOnce(preflight({ preparation_fingerprint: 'f2' }, { api_calls: 5 }));
      openEditor('arricchisci');
      useCanvasStore.getState().setEditorActions({ onExecute: realisticExecute([changedError()]) });
      await run();
    },
    '409 after launching from the window': async () => {
      preflightEnrichment
        .mockResolvedValueOnce(preflight())
        .mockResolvedValueOnce(preflight({ preparation_fingerprint: 'f2' }, { api_calls: 5 }));
      openEditor('arricchisci');
      useCanvasStore.getState().setEditorActions({ onExecute: realisticExecute([changedError()]) });
      await run();
      await act(async () => { fireEvent.click(launchBtn()); });
    },
  };
  it.each(Object.keys(PATHS))('%s', async (name) => {
    await PATHS[name]();
    expect(dialog()).toBeTruthy();
    expect(useCanvasStore.getState().runState).toBe('starting');
    // Esegui nella barra non riparte: un'esecuzione alla volta.
    const calls = useCanvasStore.getState().getEditorAction('onExecute').mock?.calls.length ?? 0;
    await run();
    expect(useCanvasStore.getState().getEditorAction('onExecute').mock?.calls.length ?? 0).toBe(calls);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Annulla' })); });
    expect(useCanvasStore.getState().runState).toBe('idle');
  });

  it('only openWindow opens the window, and it sets starting', () => {
    const src = code(path.join(SRC, 'components/editor/PipelineRunner.jsx'));
    const opens = src.match(/setRequest\((?!null\))/g) || [];
    expect(opens).toHaveLength(1);
    expect(src).toMatch(/const openWindow = useCallback\(\(next\) => \{\s*beginPreparation\(\);\s*setRequest\(next\);/);
    // UNICO punto che mette 'starting'.
    expect(src.match(/setRunState\('starting'\)/g)).toHaveLength(1);
    expect(src).toMatch(/const beginPreparation = \(\) => useCanvasStore\.getState\(\)\.setRunState\('starting'\);/);
  });
});

describe("every preparation of the run keeps runState 'starting' (also the estimate redone after 409)", () => {
  // onExecute come useExecution: 'running' durante l'avvio, 'idle' se rifiutato.
  const rejectingExecute = () => vi.fn(async () => {
    const st = useCanvasStore.getState();
    st.setRunState('running');
    st.setRunState('idle');
    throw changedError();
  });
  const deferred = () => {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
  };
  // Ogni percorso: fino all'attesa della preparazione indicata (pendente).
  const PREPARATIONS = {
    'Esegui: save, validate, estimate': async (pending) => {
      preflightEnrichment.mockReturnValueOnce(pending.promise);
      openEditor('arricchisci');
      let done;
      act(() => { done = useCanvasStore.getState().getEditorAction('runPipeline')(); });
      return () => done;
    },
    'estimate redone after 409 on a direct launch': async (pending) => {
      preflightEnrichment
        .mockResolvedValueOnce(preflight({}, { api_calls: 0 }))
        .mockReturnValueOnce(pending.promise);
      openEditor('arricchisci');
      useCanvasStore.getState().setEditorActions({ onExecute: rejectingExecute() });
      let done;
      act(() => { done = useCanvasStore.getState().getEditorAction('runPipeline')(); });
      return () => done;
    },
    'estimate redone after 409 on a launch from the window': async (pending) => {
      preflightEnrichment
        .mockResolvedValueOnce(preflight())
        .mockReturnValueOnce(pending.promise);
      openEditor('arricchisci');
      useCanvasStore.getState().setEditorActions({ onExecute: rejectingExecute() });
      await run();
      act(() => { fireEvent.click(launchBtn()); });
      return () => Promise.resolve();
    },
  };
  it.each(Object.keys(PREPARATIONS))('%s', async (name) => {
    const pending = deferred();
    const finish = await PREPARATIONS[name](pending);
    await waitFor(() => expect(preflightEnrichment).toHaveBeenCalledTimes(name.startsWith('Esegui') ? 1 : 2));
    // Durante l'attesa: niente finestra, 'starting', Esegui non riparte.
    expect(dialog()).toBeNull();
    expect(useCanvasStore.getState().runState).toBe('starting');
    const calls = useCanvasStore.getState().getEditorAction('onExecute').mock.calls.length;
    await run();
    expect(useCanvasStore.getState().getEditorAction('onExecute').mock.calls.length).toBe(calls);
    expect(useCanvasStore.getState().runState).toBe('starting');
    await act(async () => { pending.resolve(preflight({ preparation_fingerprint: 'f2' }, { api_calls: 5 })); await finish(); });
    expect(dialog()).toBeTruthy();
    expect(useCanvasStore.getState().runState).toBe('starting');
  });
});

describe('enumeration: one window, one path', () => {
  it('only PipelineRunner opens the window and asks for the estimate', () => {
    expect(usersOf(/BeforeRunModal/)).toEqual(['components/editor/BeforeRunModal.jsx', 'components/editor/PipelineRunner.jsx']);
    expect(usersOf(/preflightEnrichment/)).toEqual(['api/enrichment.js', 'api/index.js', 'components/editor/PipelineRunner.jsx']);
  });

  it('the old double paths are gone (confirmation + estimate, config step of the progress window)', () => {
    expect(usersOf(/EnrichmentConfirmModal|useEnrichmentValidation|validateEnrichment|estimateEnrichment|prepareExecute|awaitingLaunch|executionNeedsConfig|inputDefinitions=/)).toEqual([]);
  });

  it('each editor starts the run only through execution.handleExecute (choices already made)', () => {
    ['pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx'].forEach(f => {
      const src = code(path.join(SRC, f));
      expect(src).toMatch(/execution\.handleExecute\(/);
      expect(src).not.toMatch(/useRuntimeInputDefinitions/);
    });
  });
});
