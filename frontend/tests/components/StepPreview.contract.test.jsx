import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const { previewProjectMock, saveMock, state, listeners } = vi.hoisted(() => ({
  previewProjectMock: vi.fn(),
  saveMock: vi.fn(async () => {}),
  state: { nodes: [], edges: [], isDirty: false, pendingSync: false, isReadOnly: false },
  listeners: new Set(),
}));
// Cambia lo stato del canvas finto e avvisa gli iscritti (come zustand).
const changeCanvas = (patch) => {
  const prev = { ...state };
  Object.assign(state, patch);
  listeners.forEach(fn => fn({ ...state }, prev));
};

vi.mock('../../src/api', () => ({ previewProject: previewProjectMock }));
vi.mock('../../src/hooks/useTaskPolling', () => ({
  useTaskPolling: () => ({ mountedRef: { current: true }, stopPolling: vi.fn(), startPolling: vi.fn() }),
}));
vi.mock('../../src/store/useCanvasStore', () => {
  const hook = (selector) => selector({ ...state, handleSaveQuiet: saveMock });
  hook.getState = () => ({ ...state, handleSaveQuiet: saveMock });
  hook.subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
  return { default: hook };
});
vi.mock('../../src/utils/transformRegistry', () => ({ getCachedTransformRegistry: () => null, isMultiInputOp: () => false }));

import StepPreview from '../../src/components/canvas/StepPreview';

/** Contract Fase 4 M3: anteprima dei dati dopo il passo selezionato. */
describe('StepPreview', () => {
  it('shows nothing without a selected step', () => {
    state.nodes = [{ id: 'a', type: 'transform', data: {} }];
    const { container } = render(<StepPreview projectId="p1" />);
    expect(container.innerHTML).toBe('');
  });

  it('clean canvas: no save, the preview reads the saved version', async () => {
    state.nodes = [{ id: 'step-1', type: 'transform', selected: true, data: { title: 'Escludi periodi', transform_op: 'filter' } }];
    previewProjectMock.mockResolvedValueOnce({ success: true, columns: [], rows: [], total_count: 0 });
    render(<StepPreview projectId="p1" />);
    fireEvent.click(screen.getByRole('button', { name: '▶ Anteprima' }));
    await waitFor(() => expect(previewProjectMock).toHaveBeenCalledWith('p1', 100, 'step-1'));
    expect(saveMock).not.toHaveBeenCalled();
  });

  it('unsaved ("Salva e mostra anteprima"): saves first, then asks the preview for the selected node only', async () => {
    state.isDirty = true;
    saveMock.mockClear();
    previewProjectMock.mockClear();
    state.nodes = [{ id: 'step-1', type: 'transform', selected: true, data: { title: 'Escludi periodi', transform_op: 'filter' } }];
    previewProjectMock.mockResolvedValueOnce({
      success: true, columns: [{ name: 'ctr_period', type: 'str' }], rows: [{ ctr_period: 'MONTH' }], total_count: 1,
    });
    render(<StepPreview projectId="p1" />);
    expect(screen.getByText(/Dati dopo/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Salva e mostra anteprima/ }));
    await waitFor(() => expect(previewProjectMock).toHaveBeenCalledWith('p1', 100, 'step-1'));
    expect(saveMock).toHaveBeenCalled();
    expect(saveMock.mock.invocationCallOrder[0]).toBeLessThan(previewProjectMock.mock.invocationCallOrder[0]);
    expect(await screen.findByText('MONTH')).toBeTruthy();
    state.isDirty = false;
  });

  it('says it will save when there are unsaved changes', () => {
    state.nodes = [{ id: 'step-1', type: 'transform', selected: true, data: { transform_op: 'filter' } }];
    state.isDirty = true;
    render(<StepPreview projectId="p1" />);
    expect(screen.getByRole('button', { name: /Salva e mostra anteprima/ })).toBeTruthy();
    state.isDirty = false;
  });

  it('flags the shown data as out of date when the data change, not when nodes move', async () => {
    state.nodes = [{ id: 'step-1', type: 'transform', selected: true, position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: {} } }];
    previewProjectMock.mockResolvedValueOnce({
      success: true, columns: [{ name: 'v', type: 'real' }], rows: [{ v: 58.333333333336 }], total_count: 1,
    });
    const { rerender } = render(<StepPreview projectId="p1" />);
    fireEvent.click(screen.getByRole('button', { name: '▶ Anteprima' }));
    expect(await screen.findByText('58,3333')).toBeTruthy();
    expect(screen.queryByText(/dati non aggiornati/)).toBeNull();

    act(() => changeCanvas({ nodes: [{ ...state.nodes[0], position: { x: 300, y: 40 } }] }));
    await new Promise(r => setTimeout(r, 300));
    rerender(<StepPreview projectId="p1" />);
    expect(screen.queryByText(/dati non aggiornati/)).toBeNull();

    act(() => changeCanvas({ nodes: [{ ...state.nodes[0], data: { transform_op: 'filter', config: { column: 'x' } } }] }));
    expect(await screen.findByText(/dati non aggiornati/)).toBeTruthy();
  });
});
