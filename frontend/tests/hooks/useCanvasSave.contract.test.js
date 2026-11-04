import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useCanvasSave } from '../../src/hooks/useCanvasSave';
import useCanvasStore from '../../src/store/useCanvasStore';

const toastMock = vi.hoisted(() => globalThis.__frontendTestHelpers.createToastMock(vi));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

describe('useCanvasSave', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Salva parte solo con modifiche non salvate (Fase 4, M7).
    useCanvasStore.setState({ isDirty: true, isReadOnly: false, pendingSync: false });
  });

  it('niente da salvare: nessuna chiamata, lo dice (niente finto "salvato!")', async () => {
    useCanvasStore.setState({ isDirty: false });
    const save = vi.fn();
    const { result } = renderHook(() => useCanvasSave(save));
    await act(async () => { await result.current.handleSaveCanvas(); });
    expect(save).not.toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('salva il draft e mostra il toast di successo', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useCanvasSave(save));

    await act(async () => {
      await result.current.handleSaveCanvas();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(toastMock.success).toHaveBeenCalledWith('Progetto salvato!');
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(result.current.saving).toBe(false);
  });

  it('propaga il fallimento come toast uniforme senza rilanciare', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const save = vi.fn().mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useCanvasSave(save));

    try {
      await act(async () => {
        await result.current.handleSaveCanvas();
      });

      expect(save).toHaveBeenCalledTimes(1);
      expect(toastMock.error).toHaveBeenCalledWith('Errore durante il salvataggio');
      expect(toastMock.success).not.toHaveBeenCalled();
      expect(result.current.saving).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });

  it('ignora richieste concorrenti mentre il salvataggio e in corso', async () => {
    let resolveSave;
    const save = vi.fn(() => new Promise((resolve) => {
      resolveSave = resolve;
    }));
    const { result } = renderHook(() => useCanvasSave(save));

    let firstSave;
    await act(async () => {
      firstSave = result.current.handleSaveCanvas();
      await result.current.handleSaveCanvas();
    });

    expect(save).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSave();
      await firstSave;
    });

    expect(toastMock.success).toHaveBeenCalledTimes(1);
  });
});
