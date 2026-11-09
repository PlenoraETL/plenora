/**
 * P1 (09/11/2026): «Esegui» dalla home su un progetto gia' aperto prima.
 *
 * Lo store del canvas conserva il progetto della visita precedente fino
 * all'azzeramento dell'editor (useCanvasCore, effetto di layout). La
 * richiesta della home (utils/pendingRun) si prende solo quando il canvas
 * e' caricato IN QUESTA APERTURA e passa dall'azione del pulsante Esegui:
 * stessa finestra «Prima di avviare», stessi input.
 */
import React, { useLayoutEffect } from 'react';
import { render, screen, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('../../src/api/projects', async (orig) => ({
  ...(await orig()),
  updateCanvas: vi.fn(async () => ({ status: 'ok' })),
  getProject: vi.fn(async () => ({ metadata: {}, canvas_version: null })),
  validateProjectCanvas: vi.fn(async () => ({ valid: true, has_errors: false, issues: [] })),
}));
vi.mock('../../src/api/http', () => ({ default: { post: vi.fn(async () => ({ data: { task_id: 't1' } })) } }));

import useCanvasStore from '../../src/store/useCanvasStore';
import PipelineRunner from '../../src/components/editor/PipelineRunner';
import { validateProjectCanvas } from '../../src/api/projects';
import { requestRunOnOpen, takeRunOnOpen } from '../../src/utils/pendingRun';

const n = (id, type, data = {}) => ({ id, type, position: { x: 0, y: 0 }, data });
const NODES = [
  n('s', 'project_source'),
  n('u', 'project_input', { input_key: 'utente', label: 'Utente', input_kind: 'current_user', required: true, preview_username: 'prova' }),
  n('o', 'result'),
];

const onExecute = vi.fn(async () => {});
/** L'editor come lo monta EditorShell: azzeramento di layout, poi i figli. */
function Editor({ projectId }) {
  useLayoutEffect(() => {
    const st = useCanvasStore.getState();
    st.reset();
    st.setReadOnly(false);
    st.init(projectId, 'manipola');
    st.setEditorActions({ onExecute });
  }, [projectId]);
  return <PipelineRunner user={{ role: 'superadmin' }} />;
}

afterEach(() => { cleanup(); takeRunOnOpen('p1'); });

describe('P1: Esegui dalla home su un progetto gia\' visitato', () => {
  it('non parte sul canvas della visita precedente; al caricamento apre «Prima di avviare»', async () => {
    // Visita precedente di p1: lo store resta pieno e «caricato».
    const st = useCanvasStore.getState();
    st.reset();
    st.init('p1', 'manipola');
    st.loadGraph(NODES, [], { canvasVersion: 'c1:a' });
    validateProjectCanvas.mockClear();

    // Home: Esegui su p1, l'editor si apre.
    requestRunOnOpen('p1');
    await act(async () => { render(<Editor projectId="p1" />); });
    expect(validateProjectCanvas).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().runState).toBe('idle');
    expect(screen.queryByRole('dialog')).toBeNull();

    // Il canvas di questa apertura arriva: stessa via del pulsante.
    await act(async () => { useCanvasStore.getState().loadGraph(NODES, [], { canvasVersion: 'c1:b' }); });
    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(screen.getByLabelText('Utente')).toHaveValue('prova');
    expect(onExecute).not.toHaveBeenCalled();
    // Presa una volta sola.
    expect(takeRunOnOpen('p1')).toBe(false);
  });

  it('con una copia locale ripristinata non parte da solo', async () => {
    requestRunOnOpen('p1');
    await act(async () => { render(<Editor projectId="p1" />); });
    await act(async () => {
      const s = useCanvasStore.getState();
      s.loadGraph(NODES, [], { canvasVersion: 'c1:b' });
      s.loadUnsavedGraph([...NODES, n('x', 'filter')], []);
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onExecute).not.toHaveBeenCalled();
  });
});
