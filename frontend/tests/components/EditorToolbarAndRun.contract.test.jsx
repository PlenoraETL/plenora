import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import useCanvasStore from '../../src/store/useCanvasStore';
import EditorToolbar, { SaveIndicator } from '../../src/components/editor/EditorToolbar';
import { beforeRunReasons } from '../../src/utils/beforeRun';
import { buildRuntimeInputDefinitions } from '../../src/hooks/useRuntimeInputDefinitions';

/** Contract Fase 4 M7: barra unica, stato del salvataggio, un solo Esegui, input runtime. */
const n = (id, type = 'transform', data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });

beforeEach(() => {
  useCanvasStore.getState().reset();
  useCanvasStore.getState().setReadOnly(false);
  useCanvasStore.getState().loadGraph([n('s', 'project_source'), n('o', 'result')], []);
});

describe('SaveIndicator', () => {
  it('always tells how things stand', () => {
    const { rerender } = render(<SaveIndicator onRetry={() => {}} />);
    expect(screen.getByText('Tutto salvato')).toBeTruthy();
    // Fase 5 T7: «non salvato» = canvas diverso dall'ultimo salvato.
    act(() => useCanvasStore.getState().updateNodeData('s', { label: 'cambiato' }));
    rerender(<SaveIndicator onRetry={() => {}} />);
    expect(screen.getByText('Modifiche non salvate')).toBeTruthy();
    act(() => useCanvasStore.setState({ saveStatus: 'saving' }));
    rerender(<SaveIndicator onRetry={() => {}} />);
    expect(screen.getByText(/Salvataggio…/)).toBeTruthy();
    const retry = vi.fn();
    act(() => useCanvasStore.setState({ saveStatus: 'error', saveError: 'boom' }));
    rerender(<SaveIndicator onRetry={retry} />);
    fireEvent.click(screen.getByText(/non riuscito · riprova/));
    expect(retry).toHaveBeenCalled();
  });
});

describe('EditorToolbar', () => {
  const props = { mode: 'manipola', title: 'Scheda', onBack: () => {}, onSave: vi.fn(), menuItems: [{ key: 'note', icon: '💬', label: 'Nota', onClick: vi.fn() }], theme: 'dark', setTheme: vi.fn() };

  it('one primary "Esegui" that goes through the single run path; disabled without an Output', () => {
    const run = vi.fn();
    useCanvasStore.getState().setEditorActions({ runPipeline: run });
    useCanvasStore.getState().setRunReady(true);
    const { rerender } = render(<EditorToolbar {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /Esegui/ }));
    expect(run).toHaveBeenCalled();
    act(() => useCanvasStore.getState().loadGraph([n('s', 'project_source')], []));
    rerender(<EditorToolbar {...props} />);
    expect(screen.getByRole('button', { name: /Esegui/ }).disabled).toBe(true);
  });

  it('shows "in esecuzione" while starting or executing (no double run)', () => {
    act(() => useCanvasStore.getState().setRunState('starting'));
    render(<EditorToolbar {...props} />);
    expect(screen.getByRole('button', { name: /In esecuzione/ }).disabled).toBe(true);
  });

  it('the "⋯" menu carries the editor actions and the theme', () => {
    render(<EditorToolbar {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Altre azioni' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Nota/ }));
    expect(props.menuItems[0].onClick).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Altre azioni' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Tema chiaro/ }));
    expect(props.setTheme).toHaveBeenCalledWith('light');
  });

  it('read-only: no Salva, indicator says so', () => {
    act(() => useCanvasStore.getState().setReadOnly(true));
    render(<EditorToolbar {...props} />);
    expect(screen.queryByRole('button', { name: /Salva/ })).toBeNull();
    expect(screen.getByText('Sola lettura')).toBeTruthy();
  });
});

describe('run and runtime inputs', () => {
  it('the window opens only for runtime inputs, staff options or an estimate to confirm', () => {
    expect(beforeRunReasons({ inputDefinitions: [], user: { role: 'editor' } })).toEqual([]);
    expect(beforeRunReasons({ inputDefinitions: [{ input_key: 'anno' }], user: { role: 'editor' } })).toEqual(['inputs']);
    expect(beforeRunReasons({ inputDefinitions: [], user: { role: 'superadmin' } })).toEqual(['staff']);
  });

  it('runtime inputs: non-file inputs, ordered, connection options resolved (same for the three editors)', () => {
    const defs = buildRuntimeInputDefinitions([
      n('a', 'project_input', { input_key: 'b', input_kind: 'text', order: 2 }),
      n('f', 'project_input', { input_key: 'file', input_kind: 'file', order: 0 }),
      n('c', 'project_input', { input_key: 'conn', input_kind: 'connection', connection_set_id: 'set1', order: 1 }),
    ], [{ id: 'set1', members: [{ active: true, selector_key: 'k1', label: 'Uno' }, { active: false, selector_key: 'k2', label: 'Due' }] }]);
    expect(defs.map(d => d.input_key)).toEqual(['conn', 'b']);
    // senza tipo = file, come il backend: non chiesto nell'editor
    expect(buildRuntimeInputDefinitions([n('x', 'project_input', { input_key: 'nok' })]).length).toBe(0);
    expect(defs[0].connection_options).toEqual([{ key: 'k1', label: 'Uno' }]);
  });

  it('every save goes through the single writer that drives the indicator', () => {
    const src = require('node:fs').readFileSync(require('node:path').resolve(__dirname, '../../src/store/useCanvasStore.js'), 'utf-8');
    expect((src.match(/updateCanvas\(/g) || []).length).toBe(1);
  });
});
