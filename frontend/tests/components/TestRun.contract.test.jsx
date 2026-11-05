/**
 * «Esegui di prova» dall'editor con «Utente che esegue» (regola H del
 * backend, app/shared/execution/runtime_identity).
 *
 *  1. Regola dell'utente di prova (testRunIdentity) = backend
 *     RuntimeIdentity.test_run: trim, uno solo, vuoto o due diversi = problema.
 *  2. Percorsi dell'editor (PipelineRunner) x ruoli x modalita':
 *     chi puo' modificare -> finestra «Prima di avviare» SEMPRE, con l'utente
 *     di prova, e avvio con test_run (mai intermediate_mode); viewer ->
 *     blocco con il motivo, niente avvio; Arricchisci: stima di prova e
 *     utente letto dalla risposta del server.
 *  3. API: l'avvio va a /test-run (o /test-run/enrich) senza canvas e senza
 *     utente nel corpo.
 *  4. Finestra di esecuzione e cronologia: la prova si riconosce.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, act, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));
vi.mock('../../src/api/enrichment', async (orig) => ({
  ...(await orig()),
  preflightEnrichment: vi.fn(),
}));
vi.mock('../../src/api/http', () => ({ default: { post: vi.fn(async () => ({ data: { task_id: 't1' } })) } }));

import useCanvasStore from '../../src/store/useCanvasStore';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import BeforeRunModal from '../../src/components/editor/BeforeRunModal';
import ExecutionModal from '../../src/components/modals/ExecutionModal';
import { preflightEnrichment } from '../../src/api/enrichment';
import { executeTestRun } from '../../src/api/projects';
import http from '../../src/api/http';
import { runTriggerLabel, testRunIdentity } from '../../src/utils/runtimeInputKinds';
import { executionResult } from '../../src/hooks/useExecution';
import { can } from '../../src/utils/permissions';

const BACKEND_IDENTITY = fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/app/shared/execution/runtime_identity.py'), 'utf-8');

const n = (id, type, data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const user = (data = {}) => n('u', 'project_input', {
  input_key: 'utente', label: 'Utente', input_kind: 'current_user', required: true, ...data,
});
const EDITORS = {
  manipola: [n('s', 'project_source'), n('o', 'result')],
  estrai: [n('t', 'tableNode', { tableName: 'vie' }), n('o', 'resultNode')],
  arricchisci: [n('s', 'project_source'), n('a', 'api_enrich', { title: 'Geocodifica' }), n('o', 'result')],
};

let onExecute;
function openEditor(mode, extraNodes, role = 'user') {
  const st = useCanvasStore.getState();
  st.reset();
  st.setReadOnly(false);
  st.init('p1', mode);
  st.loadGraph([...EDITORS[mode], ...extraNodes], [], { canvasVersion: 'c1:a' });
  onExecute = vi.fn(async () => {});
  st.setEditorActions({ onExecute });
  render(<PipelineRunner user={{ role }} />);
}
const run = () => act(async () => { await useCanvasStore.getState().getEditorAction('runPipeline')(); });
const dialog = () => screen.queryByRole('dialog');

beforeEach(() => { preflightEnrichment.mockReset(); http.post.mockClear(); });
afterEach(() => { cleanup(); });

describe('1. the test username rule (backend RuntimeIdentity.test_run)', () => {
  it('trims, keeps one, reports missing and conflict', () => {
    expect(testRunIdentity([user({ preview_username: ' prova ' })])).toEqual({ username: 'prova', problem: null });
    expect(testRunIdentity([user()])).toEqual({ username: null, problem: 'missing' });
    expect(testRunIdentity([user({ preview_username: 'a' }), { ...user({ preview_username: 'b' }), id: 'u2' }]))
      .toEqual({ username: null, problem: 'conflict' });
    expect(testRunIdentity([user({ preview_username: 'a' }), { ...user({ preview_username: 'a ' }), id: 'u2' }]))
      .toEqual({ username: 'a', problem: null });
    // Stessa regola nel backend: strip, insieme, vuoto scartato.
    expect(BACKEND_IDENTITY).toMatch(/def test_run\(cls, saved_canvas/);
    expect(BACKEND_IDENTITY).toMatch(/names\.discard\(""\)/);
  });

  it('only who can edit the project can run a test (same roles as the backend)', () => {
    expect(['user', 'admin', 'superadmin'].map(r => can.testRunProject({ role: r }))).toEqual([true, true, true]);
    expect(['viewer', 'esecutore'].map(r => can.testRunProject({ role: r }))).toEqual([false, false]);
  });
});

describe.each(Object.keys(EDITORS))('2. editor %s', (mode) => {
  const arricchisci = mode === 'arricchisci';
  const estimate = (username = 'prova') => ({
    valid: true, preparation_fingerprint: 'f-prova', test_run: { username },
    nodes: [{ node_id: 'a', input_rows: 1, input_rows_status: 'exact', effective_rows: 1, api_calls: 1, valid: true }],
    checks: {},
  });

  it('editor: the window always opens with the test user and the run is a test run', async () => {
    if (arricchisci) preflightEnrichment.mockResolvedValue(estimate());
    openEditor(mode, [user({ preview_username: 'prova' })], 'superadmin');
    await run();
    expect(dialog()).toBeTruthy();
    expect(onExecute).not.toHaveBeenCalled();
    expect(screen.getByRole('region', { name: 'Esecuzione di prova' })).toHaveTextContent(/utente di prova prova/);
    expect(screen.getByLabelText('Utente')).toHaveValue('prova');
    // Le opzioni dello staff non valgono per la prova.
    expect(screen.queryByLabelText(/intermediate_mode/)).toBeNull();
    if (arricchisci) {
      await waitFor(() => expect(preflightEnrichment).toHaveBeenCalledWith('p1', { inputs: {}, force_recalculate: false, test_run: true }));
    }
    const launch = screen.getByRole('button', { name: arricchisci ? /Conferma e avvia la prova/ : /Avvia l'esecuzione di prova/ });
    await waitFor(() => expect(launch.disabled).toBe(false));
    await act(async () => { fireEvent.click(launch); });
    expect(onExecute).toHaveBeenCalledWith('o', arricchisci
      ? { force_recalculate: false, preparation_fingerprint: 'f-prova', test_run: true, mode }
      : { force_recalculate: false, test_run: true, mode });
  });

  it('editor without a test username: the window says it and Avvia stays off', async () => {
    openEditor(mode, [user()]);
    await run();
    expect(screen.getByText(/Indica un nome utente di prova/)).toBeInTheDocument();
    // Nessuna stima di prova senza utente di prova.
    expect(preflightEnrichment).not.toHaveBeenCalled();
    const launch = screen.getByRole('button', { name: /prova/ });
    expect(launch.disabled).toBe(true);
    expect(onExecute).not.toHaveBeenCalled();
  });

  it('viewer: blocked with the reason, nothing starts', async () => {
    openEditor(mode, [user({ preview_username: 'prova' })], 'viewer');
    await run();
    expect(onExecute).not.toHaveBeenCalled();
    expect(preflightEnrichment).not.toHaveBeenCalled();
    expect(screen.getByText(/esecuzione di prova, riservata a chi può modificare il progetto/)).toBeInTheDocument();
  });

  if (arricchisci) {
    it('the user shown is the one the server will use (test estimate)', async () => {
      preflightEnrichment.mockResolvedValue(estimate('dal.server'));
      openEditor(mode, [user({ preview_username: 'prova' })]);
      await run();
      await waitFor(() => expect(screen.getByLabelText('Utente')).toHaveValue('dal.server'));
    });
  }
});

describe('3. API: the server reads the saved canvas', () => {
  it.each([
    ['manipola', '/projects/p1/test-run'],
    ['estrai', '/projects/p1/test-run'],
    ['arricchisci', '/projects/p1/test-run/enrich'],
  ])('%s -> %s, no canvas and no user in the body', async (mode, url) => {
    await executeTestRun('p1', { mode, inputs: { anno: 2024 }, force_recalculate: false, preparation_fingerprint: 'f' });
    const [calledUrl, body] = http.post.mock.calls.at(-1);
    expect(calledUrl).toBe(url);
    expect(Object.keys(body).sort()).toEqual(
      mode === 'arricchisci' ? ['force_recalculate', 'inputs', 'preparation_fingerprint'] : ['force_recalculate', 'inputs']);
    expect(JSON.stringify(body)).not.toMatch(/canvas|preview_username|utente/);
  });
});

describe('4. the test run is recognizable afterwards', () => {
  it('execution window: success and failure keep the mark', () => {
    const ok = executionResult({ phase: 'success', result: { output_file: 'o.csv', test_run: { username: 'prova' } } }, 't1');
    const ko = executionResult({ phase: 'failure', error: 'rotto', result: { test_run: { username: 'prova' } } }, 't1');
    expect(ok.test_run).toEqual({ username: 'prova' });
    expect(ko.test_run).toEqual({ username: 'prova' });
    render(<ExecutionModal isOpen onClose={() => {}} isExecuting={false} result={ok} onDownload={() => {}} />);
    expect(screen.getByText(/Esecuzione di prova \(utente: prova\)/).closest('[role="status"]'))
      .toHaveTextContent(/non è l.Output del progetto.*Carica/);
  });

  // Ogni ramo dell'esito (anche permesso revocato, annullata, persa): il
  // segno dal risultato del task o, senza risultato (task in eccezione), dalla
  // risposta dell'avvio.
  it.each(['success', 'failure', 'permission_revoked', 'revoked', 'lost'])('%s keeps the mark in every branch', (phase) => {
    const fromTask = executionResult({ phase, error: 'x', result: { test_run: { username: 'prova' } } }, 't1');
    const fromLaunch = executionResult({ phase, error: 'x', result: null }, 't1', { username: 'prova' });
    expect(fromTask.test_run).toEqual({ username: 'prova' });
    expect(fromLaunch.test_run).toEqual({ username: 'prova' });
    expect(executionResult({ phase, error: 'x', result: null }, 't1').test_run).toBeUndefined();
  });

  it('history: «Esecuzione di prova (utente: X)», other triggers as before; same prefix as the backend', () => {
    expect(runTriggerLabel('test_run:E0041')).toBe('Esecuzione di prova (utente: E0041)');
    expect(runTriggerLabel('manual')).toBe('manual');
    expect(runTriggerLabel(null)).toBe('—');
    expect(BACKEND_IDENTITY).toMatch(/TEST_RUN_TRIGGER_PREFIX = "test_run:"/);
  });

  it('the window can be opened directly with a test run request', () => {
    render(<BeforeRunModal
      request={{ id: 1, mode: 'manipola', inputDefinitions: [], user: null, values: {}, testRun: { username: 'prova', problem: null } }}
      onLaunch={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/Pianificazioni e catene restano/)).toBeInTheDocument();
  });
});

// Correzione dopo la conferma di G+H (regola madre del backend: la prova non
// scrive nulla fuori dalla sua attivita'): nessun ricalcolo a monte, detto
// nella finestra; la guida ha le sezioni sotto il titolo della finestra.
describe('5. the test run never recalculates (backend guard_upstream_recompute)', () => {
  it('the window says it, and «Ricalcola tutto» is declared ignored', () => {
    render(<BeforeRunModal
      request={{ id: 1, mode: 'manipola', inputDefinitions: [], user: null, values: {},
        runOptions: { force_recalculate: true }, testRun: { username: 'prova', problem: null } }}
      onLaunch={() => {}} onCancel={() => {}} />);
    const notice = screen.getByRole('region', { name: 'Esecuzione di prova' });
    expect(notice).toHaveTextContent(/Non ricalcola i progetti a monte/);
    expect(notice).toHaveTextContent(/eseguire prima il progetto a monte/);
    expect(notice).toHaveTextContent(/«Ricalcola tutto» non vale per la prova/);
    expect(BACKEND_IDENTITY).toMatch(/Esegui prima il progetto a monte/);
  });

  it.each(Object.keys(EDITORS))('%s: with «Ricalcola tutto» on, the test run still sends force_recalculate false', async (mode) => {
    if (mode === 'arricchisci') {
      preflightEnrichment.mockResolvedValue({ valid: true, preparation_fingerprint: 'f', test_run: { username: 'prova' },
        nodes: [], checks: {} });
    }
    openEditor(mode, [user({ preview_username: 'prova' })]);
    useCanvasStore.getState().setRunOptions({ force_recalculate: true });
    await run();
    if (mode === 'arricchisci') {
      await waitFor(() => expect(preflightEnrichment).toHaveBeenCalledWith('p1', { inputs: {}, force_recalculate: false, test_run: true }));
    }
    const launch = screen.getByRole('button', { name: /prova/ });
    await waitFor(() => expect(launch.disabled).toBe(false));
    await act(async () => { fireEvent.click(launch); });
    expect(onExecute.mock.calls.at(-1)[1]).toMatchObject({ force_recalculate: false, test_run: true });
    await executeTestRun('p1', { mode, force_recalculate: true });
    expect(http.post.mock.calls.at(-1)[1].force_recalculate).toBe(false);
  });

  it('the execution window says the upstream projects were not recalculated', () => {
    const ok = executionResult({ phase: 'success', result: { output_file: 'o.csv', test_run: { username: 'prova' } } }, 't1');
    render(<ExecutionModal isOpen onClose={() => {}} isExecuting={false} result={ok} onDownload={() => {}} />);
    expect(screen.getByText(/Esecuzione di prova \(utente: prova\)/).closest('[role="status"]'))
      .toHaveTextContent(/non ha ricalcolato i progetti a monte/);
  });
});
