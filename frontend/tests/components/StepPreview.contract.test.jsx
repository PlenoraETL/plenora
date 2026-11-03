import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const { previewProjectMock, saveMock, state } = vi.hoisted(() => ({
  previewProjectMock: vi.fn(),
  saveMock: vi.fn(async () => {}),
  state: { nodes: [] },
}));

vi.mock('../../src/api', () => ({ previewProject: previewProjectMock }));
vi.mock('../../src/hooks/useTaskPolling', () => ({
  useTaskPolling: () => ({ mountedRef: { current: true }, stopPolling: vi.fn(), startPolling: vi.fn() }),
}));
vi.mock('../../src/store/useCanvasStore', () => {
  const hook = (selector) => selector({ ...state, handleSaveQuiet: saveMock });
  hook.getState = () => ({ ...state, handleSaveQuiet: saveMock });
  return { default: hook };
});
vi.mock('../../src/utils/transformRegistry', () => ({ getCachedTransformRegistry: () => null }));

import StepPreview from '../../src/components/canvas/StepPreview';

/** Contract Fase 4 M3: anteprima dei dati dopo il passo selezionato. */
describe('StepPreview', () => {
  it('shows nothing without a selected step', () => {
    state.nodes = [{ id: 'a', type: 'transform', data: {} }];
    const { container } = render(<StepPreview projectId="p1" />);
    expect(container.innerHTML).toBe('');
  });

  it('saves first, then asks the preview for the selected node only', async () => {
    state.nodes = [{ id: 'step-1', type: 'transform', selected: true, data: { title: 'Escludi periodi', transform_op: 'filter' } }];
    previewProjectMock.mockResolvedValueOnce({
      success: true, columns: [{ name: 'ctr_period', type: 'str' }], rows: [{ ctr_period: 'MONTH' }], total_count: 1,
    });
    render(<StepPreview projectId="p1" />);
    expect(screen.getByText(/Dati dopo/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '▶ Anteprima' }));
    await waitFor(() => expect(previewProjectMock).toHaveBeenCalledWith('p1', 50, 'step-1'));
    expect(saveMock).toHaveBeenCalled();
    expect(saveMock.mock.invocationCallOrder[0]).toBeLessThan(previewProjectMock.mock.invocationCallOrder[0]);
    expect(await screen.findByText('MONTH')).toBeTruthy();
  });
});
