import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  // Server che non versiona: PUT e GET senza canvas_version (utils/canvasVersion.tokenAfterWrite).
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));

import useCanvasStore, { hasUnsavedChanges } from '../../src/store/useCanvasStore';
import { updateCanvas } from '../../src/api/projects';
import { saveBeforeServerRead, saveForRun } from '../../src/utils/serverActions';
import EditorToolbar from '../../src/components/editor/EditorToolbar';
import ExecuteActionsPanel from '../../src/components/nodes/result/standard/ExecuteActionsPanel';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import { ReactFlowProvider } from '@xyflow/react';

/**
 * Contract Fase 4 M7, correzione unica della raccolta difetti:
 * le regole del salvataggio esplicito valgono per costruzione. La copia di
 * sicurezza ha il suo contract in due schede: DraftTabs.contract.test.jsx.
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

const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });

beforeEach(() => {
  localStorage.clear();
  // Forma reale dell'utente salvata dal login (AuthContext.login).
  sessionStorage.setItem('urby_user', JSON.stringify({ user_id: 7, username: 'mario', group_id: 3, role: 'user' }));
  useCanvasStore.getState().reset();
  useCanvasStore.getState().setReadOnly(false);
  useCanvasStore.getState().init('p1', 'manipola');
  useCanvasStore.getState().loadGraph([n('s', 'project_source'), n('o', 'result')], []);
  updateCanvas.mockClear();
});
afterEach(() => { vi.useRealTimers(); });

describe('enumeration: who may save, who decides "unsaved"', () => {
  it('only serverActions (and the store that defines it) call handleSaveQuiet', () => {
    expect(usersOf(/handleSaveQuiet/)).toEqual(['store/useCanvasStore.js', 'utils/serverActions.js']);
  });

  it('isDirty is read only through hasUnsavedChanges (one definition)', () => {
    expect(usersOf(/isDirty/)).toEqual(['store/saveState.js', 'store/useCanvasStore.js']);
  });

  it('pendingSync (automatic corrections) never drives a "not saved" message', () => {
    expect(usersOf(/pendingSync/)).toEqual(['hooks/useCanvasSave.js', 'store/useCanvasStore.js']);
  });

  it('automatic corrections are not "unsaved changes"', () => {
    useCanvasStore.getState().updateNodeData('s', { format: 'csv' }, { derived: true });
    expect(hasUnsavedChanges(useCanvasStore.getState())).toBe(false);
    useCanvasStore.getState().updateNodeData('s', { label: 'x' });
    expect(hasUnsavedChanges(useCanvasStore.getState())).toBe(true);
  });
});

describe('server reads ask before saving', () => {
  it('clean canvas: nothing asked, nothing saved', async () => {
    const ask = vi.fn();
    useCanvasStore.getState().setEditorActions({ confirmSaveBeforeRead: ask });
    expect(await saveBeforeServerRead('La validazione')).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    expect(updateCanvas).not.toHaveBeenCalled();
  });

  it('unsaved: "Annulla" saves nothing and stops; "Salva e continua" saves', async () => {
    useCanvasStore.getState().updateNodeData('s', { label: 'x' });
    useCanvasStore.getState().setEditorActions({ confirmSaveBeforeRead: vi.fn(async () => false) });
    expect(await saveBeforeServerRead("L'anteprima")).toBe(false);
    expect(updateCanvas).not.toHaveBeenCalled();
    useCanvasStore.getState().setEditorActions({ confirmSaveBeforeRead: vi.fn(async () => true) });
    expect(await saveBeforeServerRead("L'anteprima")).toBe(true);
    expect(updateCanvas).toHaveBeenCalledTimes(1);
    expect(hasUnsavedChanges(useCanvasStore.getState())).toBe(false);
  });

  it('"Esegui" saves without asking (the button says so)', async () => {
    useCanvasStore.getState().updateNodeData('s', { label: 'x' });
    const ask = vi.fn();
    useCanvasStore.getState().setEditorActions({ confirmSaveBeforeRead: ask });
    await saveForRun();
    expect(ask).not.toHaveBeenCalled();
    expect(updateCanvas).toHaveBeenCalledTimes(1);
  });

  it('a save of another project does not touch the open one', async () => {
    let release;
    updateCanvas.mockImplementationOnce(() => new Promise(r => { release = r; }));
    useCanvasStore.getState().updateNodeData('s', { label: 'x' });
    const p = saveForRun();
    act(() => { useCanvasStore.getState().reset(); useCanvasStore.getState().init('p2', 'manipola'); });
    release({});
    await p;
    expect(useCanvasStore.getState().lastSavedAt).toBe(null);
    expect(useCanvasStore.getState().saveStatus).toBe('idle');
  });
});

describe('one Esegui: shared options and busy state', () => {
  const props = { mode: 'manipola', title: 'T', onBack: () => {}, onSave: vi.fn(), menuItems: [] };

  it('the toolbar is off until the Output registered the run path', () => {
    render(<EditorToolbar {...props} />);
    expect(screen.getByRole('button', { name: /Esegui/ }).disabled).toBe(true);
    act(() => { useCanvasStore.getState().setEditorActions({ runPipeline: vi.fn() }); useCanvasStore.getState().setRunReady(true); });
    expect(screen.getByRole('button', { name: /Esegui/ }).disabled).toBe(false);
  });

  it('"Ricalcola tutto" is shared state; the panel button is off while a run is in progress', () => {
    const onExecute = vi.fn();
    render(<ExecuteActionsPanel data={{}} id="o" nodeVariant="manipola"
      hasExecutionOptions hasOnExecute onExecute={onExecute} onValidate={() => {}} />);
    fireEvent.click(screen.getByLabelText(/Ricalcola tutto/));
    expect(useCanvasStore.getState().runOptions.force_recalculate).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Esegui' }));
    expect(onExecute).toHaveBeenCalledWith();
    act(() => useCanvasStore.getState().setRunState('running'));
    expect(screen.getByRole('button', { name: /In esecuzione/ }).disabled).toBe(true);
  });
});

describe('runtime input kinds per mode (same table as the backend)', () => {
  it('connection only in Estrai; scalars and file everywhere', async () => {
    const { RUNTIME_INPUT_KINDS_BY_MODE, unsupportedInputNodes } = await import('../../src/utils/runtimeInputKinds');
    const scalars = ['text', 'integer', 'number', 'boolean', 'date', 'datetime', 'select'];
    expect(RUNTIME_INPUT_KINDS_BY_MODE).toEqual({
      estrai: ['file', ...scalars, 'connection'],
      manipola: ['file', ...scalars],
      arricchisci: ['file', ...scalars],
    });
    const nodes = [n('c', 'project_input', { input_kind: 'connection' }), n('t', 'project_input', { input_kind: 'text' }), n('f', 'project_input', {})];
    expect(unsupportedInputNodes(nodes, 'estrai')).toEqual([]);
    expect(unsupportedInputNodes(nodes, 'manipola').map(x => x.id)).toEqual(['c']);
    expect(unsupportedInputNodes(nodes, 'arricchisci').map(x => x.id)).toEqual(['c']);
  });
});

describe('the run path lives in the editor, not in the Output panel', () => {
  it('"▶ Esegui" works with the Output closed (no ResultNode mounted): checks, saves, validates, starts', async () => {
    const onExecute = vi.fn();
    useCanvasStore.getState().setEditorActions({ onExecute });
    useCanvasStore.getState().updateNodeData('s', { label: 'x' });
    render(<ReactFlowProvider><PipelineRunner /><EditorToolbar mode="manipola" title="T" onBack={() => {}} onSave={() => {}} menuItems={[]} /></ReactFlowProvider>);
    const btn = screen.getByRole('button', { name: /Esegui/ });
    expect(btn.disabled).toBe(false);
    await act(async () => { fireEvent.click(btn); });
    await vi.waitFor(() => expect(onExecute).toHaveBeenCalledWith('o', { force_recalculate: false }));
    expect(updateCanvas).toHaveBeenCalledTimes(1);
  });

  it('a blocked run shows its reason even with the Output closed', async () => {
    useCanvasStore.getState().loadGraph([n('s', 'project_source', { access_denied: true }), n('o', 'result')], []);
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<ReactFlowProvider><PipelineRunner /></ReactFlowProvider>);
    await act(async () => { await useCanvasStore.getState().getEditorAction('runPipeline')(); });
    expect(screen.getByText(/risorsa non accessibile/)).toBeTruthy();
    expect(updateCanvas).not.toHaveBeenCalled();
  });
});

describe('every exit from the editor asks first', () => {
  it('each logout() in the editors goes through the unsaved-changes question', () => {
    const editorFiles = ['components/panels/EditorSidebar.jsx', 'pages/EstraiEditor.jsx', 'pages/ManipolaEditor.jsx', 'pages/ArricchisciEditor.jsx'];
    const offenders = editorFiles.flatMap(f => code(path.join(SRC, f)).split(/\r?\n/)
      .filter(l => /\blogout\(\)/.test(l) && !/confirm(LeaveWithUnsaved|Navigation)\(\)/.test(l))
      .map(l => `${f}: ${l.trim()}`));
    expect(offenders).toEqual([]);
  });
});

describe('editor start-up order', () => {
  it('the store reset at editor start does not wipe actions registered by child components', async () => {
    vi.resetModules();
    vi.doMock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));
    const { useCanvasCore } = await import('../../src/hooks/useCanvasCore');
    const store = (await import('../../src/store/useCanvasStore')).default;
    const Child = () => {
      React.useEffect(() => { store.getState().setEditorActions({ confirmSaveBeforeRead: () => true }); }, []);
      return null;
    };
    const Page = () => { useCanvasCore('p9', 'manipola'); return <Child />; };
    render(<Page />);
    expect(typeof store.getState().getEditorAction('confirmSaveBeforeRead')).toBe('function');
    vi.doUnmock('../../src/context/AuthContext');
  });
});
