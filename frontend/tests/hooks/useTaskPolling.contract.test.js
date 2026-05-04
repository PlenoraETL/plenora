/**
 * Contract test — useTaskPolling (Batch 1 FE Reliability, FE-22).
 *
 * Hook condiviso per polling Celery. Garanzie da preservare:
 *   1. startPolling chiama getTaskStatus su ogni tick di setInterval e passa
 *      i dati a onStatus(data).
 *   2. onStatus che ritorna true ferma il polling (clearInterval) e impedisce
 *      altri tick.
 *   3. Re-invocazione di startPolling sullo stesso hook NON lascia due
 *      interval attivi (single-flight: stopPolling auto-chiamato).
 *   4. Cleanup su unmount: clearInterval automatico, niente setState dopo
 *      l'unmount (mountedRef guard).
 *   5. Errore non-404 in getTaskStatus: senza onError -> log e continua;
 *      con onError che ritorna true -> stop.
 *   6. opts.immediate=true: prima poll fire-and-forget prima del setInterval.
 *
 * Usa vi.useFakeTimers() per controllare l'avanzamento del setInterval senza
 * race con i microtask. Ogni `vi.advanceTimersByTimeAsync(ms)` flusha sia i
 * timer che le promise pendenti (necessario perche' il body di poll() e'
 * async e await getTaskStatus produce un microtask).
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { getTaskStatusMock } = vi.hoisted(() => ({
  getTaskStatusMock: vi.fn(),
}));

vi.mock('../../src/api', () => ({
  getTaskStatus: getTaskStatusMock,
}));

import { useTaskPolling } from '../../src/hooks/useTaskPolling';

describe('useTaskPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getTaskStatusMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('chiama onStatus ad ogni tick e si ferma quando onStatus ritorna true', async () => {
    getTaskStatusMock
      .mockResolvedValueOnce({ status: 'pending', progress: 0 })
      .mockResolvedValueOnce({ status: 'pending', progress: 50 })
      .mockResolvedValueOnce({ status: 'success', progress: 100 });

    const onStatus = vi.fn((data) => data.status === 'success');
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-A', onStatus);
    });

    // Tick 1
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenCalledWith({ status: 'pending', progress: 0 });

    // Tick 2
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(2);

    // Tick 3 -> success -> stop
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(3);
    expect(onStatus).toHaveBeenLastCalledWith({ status: 'success', progress: 100 });

    // Nessun tick dopo lo stop
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(3);
  });

  it('startPolling consecutivo non lascia il vecchio interval attivo (single-flight)', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'pending' });

    const onStatusFirst = vi.fn(() => false);
    const onStatusSecond = vi.fn(() => false);
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-old', onStatusFirst);
    });

    // Senza ancora avanzare timers, una seconda startPolling deve rimpiazzare
    // l'interval precedente. Avanzando di 100ms dovrebbe partire SOLO il nuovo.
    act(() => {
      result.current.startPolling('task-new', onStatusSecond);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(onStatusFirst).not.toHaveBeenCalled();
    expect(onStatusSecond).toHaveBeenCalledTimes(1);
    expect(getTaskStatusMock).toHaveBeenCalledTimes(1);
    expect(getTaskStatusMock).toHaveBeenCalledWith('task-new');
  });

  it('cleanup su unmount: clearInterval e nessun onStatus dopo unmount', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'pending' });
    const onStatus = vi.fn(() => false);
    const { result, unmount } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-X', onStatus);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onStatus).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    // mountedRef bloccha onStatus + clearInterval ferma getTaskStatus.
    expect(onStatus).toHaveBeenCalledTimes(1);
  });

  it('errore non-404 senza onError: log su console.error e continua il polling', async () => {
    // Permettiamo console.error in questo test (la setup globale lo intercetta).
    globalThis.__frontendConsolePolicy.allowError = true;

    getTaskStatusMock
      .mockRejectedValueOnce(new Error('500 internal'))
      .mockResolvedValueOnce({ status: 'success' });

    const onStatus = vi.fn((d) => d.status === 'success');
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-err', onStatus);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    // Errore propagato: onStatus NON chiamato
    expect(onStatus).not.toHaveBeenCalled();

    // Tick successivo: torna OK -> onStatus chiamato -> ferma
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onStatus).toHaveBeenCalledTimes(1);
  });

  it('errore 404 ("non trovato"): silenzioso, continua a pollare', async () => {
    getTaskStatusMock
      .mockRejectedValueOnce(Object.assign(new Error('Task non trovato'), { response: { status: 404 } }))
      .mockResolvedValueOnce({ status: 'success' });

    const onStatus = vi.fn((d) => d.status === 'success');
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-not-yet', onStatus);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onStatus).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onStatus).toHaveBeenCalledTimes(1);
  });

  it('opts.onError che ritorna true ferma il polling', async () => {
    getTaskStatusMock.mockRejectedValue(new Error('boom'));

    const onStatus = vi.fn(() => false);
    const onError = vi.fn(() => true);
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-fatal', onStatus, { onError });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onError).toHaveBeenCalledTimes(1);

    // Polling fermato: nessun ulteriore tick anche dopo molto tempo.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onStatus).not.toHaveBeenCalled();
  });

  it('opts.immediate=true: prima poll fire-and-forget prima del setInterval', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'pending' });
    const onStatus = vi.fn(() => false);
    const { result } = renderHook(() => useTaskPolling({ interval: 1000 }));

    act(() => {
      result.current.startPolling('task-fast', onStatus, { immediate: true });
    });

    // Senza avanzare l'interval, la prima poll deve risolversi via microtask.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenCalledTimes(1);

    // Tick successivo: seconda poll dal setInterval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(getTaskStatusMock).toHaveBeenCalledTimes(2);
  });

  it('stopPolling esplicito ferma immediatamente', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'pending' });
    const onStatus = vi.fn(() => false);
    const { result } = renderHook(() => useTaskPolling({ interval: 100 }));

    act(() => {
      result.current.startPolling('task-cancel', onStatus);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(onStatus).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.stopPolling();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(onStatus).toHaveBeenCalledTimes(1);
  });
});
