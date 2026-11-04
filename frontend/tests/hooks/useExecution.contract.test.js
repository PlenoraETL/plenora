/**
 * Contract test — useExecution (FE-NEXT-1, FE-T2).
 *
 * handleExecute(opts) parte subito e apre la finestra in isExecuting: input,
 * opzioni e conferme arrivano gia' in opts dalla finestra unica «Prima di
 * avviare» (editor/PipelineRunner, Blocco A regola 5). Un 409
 * preparation_changed torna a chi ha avviato (niente esito di errore).
 *
 * Strategia mock:
 *  - useTaskPolling: stub via vi.mock -> espone startPolling spy che NON
 *    avvia un vero interval. I test invocano la callback registrata
 *    manualmente quando vogliono simulare lo status update.
 *  - api: stub di executeProjectQuery / cancelTask / getTaskStatus /
 *    downloadTaskOutput / downloadProjectOutput.
 *  - useCanvasStore: stub di getState().setLastTaskResult per non
 *    dipendere dal vero store Zustand.
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  startPollingMock,
  stopPollingMock,
  executeProjectQueryMock,
  cancelTaskMock,
  downloadTaskOutputMock,
  downloadProjectOutputMock,
  setLastTaskResultMock,
} = vi.hoisted(() => ({
  startPollingMock: vi.fn(),
  stopPollingMock: vi.fn(),
  executeProjectQueryMock: vi.fn(),
  cancelTaskMock: vi.fn(),
  downloadTaskOutputMock: vi.fn(),
  downloadProjectOutputMock: vi.fn(),
  setLastTaskResultMock: vi.fn(),
}));

vi.mock('../../src/hooks/useTaskPolling', () => ({
  useTaskPolling: () => ({
    mountedRef: { current: true },
    stopPolling: stopPollingMock,
    startPolling: startPollingMock,
  }),
}));

vi.mock('../../src/api', () => ({
  executeProjectQuery: executeProjectQueryMock,
  getTaskStatus: vi.fn(),
  cancelTask: cancelTaskMock,
  downloadTaskOutput: downloadTaskOutputMock,
  downloadProjectOutput: downloadProjectOutputMock,
}));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: {
    getState: () => ({ setLastTaskResult: setLastTaskResultMock }),
  },
}));

import { useExecution } from '../../src/hooks/useExecution';

beforeEach(() => {
  vi.clearAllMocks();
});


describe('useExecution — initial state + return shape', () => {
  it('valori iniziali coerenti', () => {
    const { result } = renderHook(() => useExecution('proj-1'));
    expect(result.current.showModal).toBe(false);
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.result).toBeNull();
    expect(result.current.taskId).toBeNull();
    expect(result.current.progress).toBe(0);
    expect(result.current.taskStatus).toBeNull();
    expect(result.current.caricaTasks).toEqual([]);
    expect(result.current.caricaPolling).toBe(false);
  });

  it('espone metodi pubblici di ciclo vita', () => {
    const { result } = renderHook(() => useExecution('proj-1'));
    expect(typeof result.current.handleExecute).toBe('function');
    expect(result.current.prepareExecute).toBeUndefined();
    expect(result.current.launch).toBeUndefined();
    expect(typeof result.current.closeModal).toBe('function');
    expect(typeof result.current.reset).toBe('function');
    expect(typeof result.current.cancelTask).toBe('function');
    expect(typeof result.current.downloadTaskResult).toBe('function');
  });
});


describe('useExecution — handleExecute (legacy back-compat)', () => {
  it('handleExecute(opts) apre modal in isExecuting + chiama executeFn', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 't10' });
    const { result } = renderHook(() => useExecution('proj-1'));

    await act(async () => { await result.current.handleExecute({ format: 'csv' }); });

    expect(result.current.showModal).toBe(true);
    expect(result.current.isExecuting).toBe(true);
    expect(executeProjectQueryMock).toHaveBeenCalledWith('proj-1', { format: 'csv' });
    expect(startPollingMock).toHaveBeenCalledTimes(1);
    expect(result.current.taskId).toBe('t10');
  });

  it('executeFunction custom override (es. executeEnrichmentAsync)', async () => {
    const customFn = vi.fn().mockResolvedValue({ async: true, task_id: 't11' });
    const { result } = renderHook(() =>
      useExecution('proj-1', { executeFunction: customFn })
    );

    await act(async () => { await result.current.handleExecute({ format: 'xlsx' }); });

    expect(customFn).toHaveBeenCalledWith('proj-1', { format: 'xlsx' });
    expect(executeProjectQueryMock).not.toHaveBeenCalled();
  });

  it('onBeforeExecute hook chiamato prima di executeFn', async () => {
    const before = vi.fn().mockResolvedValue();
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 't12' });
    const { result } = renderHook(() => useExecution('proj-1', { onBeforeExecute: before }));

    await act(async () => { await result.current.handleExecute({}); });

    expect(before).toHaveBeenCalledTimes(1);
    expect(executeProjectQueryMock).toHaveBeenCalled();
    // Order: before chiamato prima di executeFn
    expect(before.mock.invocationCallOrder[0])
      .toBeLessThan(executeProjectQueryMock.mock.invocationCallOrder[0]);
  });

  it('response sincrona (async=false) -> imposta result diretto, niente polling', async () => {
    executeProjectQueryMock.mockResolvedValue({
      success: true, rows_count: 42, output_file: 'out.csv',
    });
    const { result } = renderHook(() => useExecution('proj-1'));

    await act(async () => { await result.current.handleExecute({}); });

    expect(result.current.isExecuting).toBe(false);
    expect(result.current.taskStatus).toBe('success');
    expect(result.current.result).toEqual({
      success: true, rows_count: 42, output_file: 'out.csv',
    });
    expect(startPollingMock).not.toHaveBeenCalled();
  });

  it('executeFn rejecta -> result.success=false + isExecuting=false', async () => {
    // Permettiamo console.error in questo test (la setup globale lo intercetta).
    globalThis.__frontendConsolePolicy.allowError = true;

    executeProjectQueryMock.mockRejectedValue(
      new Error('connection refused')
    );
    const { result } = renderHook(() => useExecution('proj-1'));

    await act(async () => { await result.current.handleExecute({}); });

    expect(result.current.isExecuting).toBe(false);
    expect(result.current.taskStatus).toBe('failure');
    expect(result.current.result?.success).toBe(false);
    expect(result.current.result?.message).toMatch(/connection refused/);
  });

  it('409 preparation_changed: niente esito, finestra chiusa, errore a chi ha avviato', async () => {
    globalThis.__frontendConsolePolicy.allowError = true;
    const err = Object.assign(new Error('Preparazione cambiata'), {
      response: { status: 409, data: { detail: { error: 'validation_error', reason: 'preparation_changed', message: 'Preparazione cambiata' } } },
    });
    executeProjectQueryMock.mockRejectedValue(err);
    const { result } = renderHook(() => useExecution('proj-1'));

    let thrown = null;
    await act(async () => { try { await result.current.handleExecute({ preparation_fingerprint: 'f1' }); } catch (e) { thrown = e; } });

    expect(thrown).toBe(err);
    expect(result.current.showModal).toBe(false);
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.result).toBeNull();
  });
});


describe('useExecution — polling callback effects', () => {
  it('callback con status=success -> isExecuting=false + result populated + setLastTaskResult chiamato', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tp1' });
    const { result } = renderHook(() => useExecution('proj-1'));

    await act(async () => { await result.current.handleExecute({}); });

    // Recupera la callback registrata nello startPolling
    const onStatus = startPollingMock.mock.calls[0][1];
    let done;
    act(() => {
      done = onStatus({
        status: 'success',
        progress: 100,
        message: 'done',
        result: { rows_count: 7, output_file: 'r.csv' },
      });
    });
    expect(done).toBe(true);

    await waitFor(() => {
      expect(result.current.isExecuting).toBe(false);
    });
    expect(result.current.result?.success).toBe(true);
    expect(result.current.result?.rows_count).toBe(7);
    expect(result.current.result?.task_id).toBe('tp1');
    expect(setLastTaskResultMock).toHaveBeenCalledTimes(1);
  });

  it('callback con status=failure -> result.success=false + done=true', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tp2' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    const onStatus = startPollingMock.mock.calls[0][1];
    let done;
    act(() => {
      done = onStatus({ status: 'failure', error: 'boom', progress: 50 });
    });
    expect(done).toBe(true);
    await waitFor(() => { expect(result.current.isExecuting).toBe(false); });
    expect(result.current.result?.success).toBe(false);
    expect(result.current.result?.message).toMatch(/boom/);
  });

  it('callback con status=revoked -> result success=false + messaggio "annullata"', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tp3' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    const onStatus = startPollingMock.mock.calls[0][1];
    let done;
    act(() => { done = onStatus({ status: 'revoked' }); });
    expect(done).toBe(true);
    await waitFor(() => { expect(result.current.isExecuting).toBe(false); });
    expect(result.current.result?.message).toMatch(/annullata/i);
  });

  it('callback con status=pending/progress -> done=false (continua il polling)', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tp4' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    const onStatus = startPollingMock.mock.calls[0][1];
    let done;
    act(() => { done = onStatus({ status: 'progress', progress: 30, message: 'lavorando' }); });
    expect(done).toBe(false);
    expect(result.current.progress).toBe(30);
    expect(result.current.progressMessage).toBe('lavorando');
    expect(result.current.isExecuting).toBe(true);
  });
});


describe('useExecution — closeModal / reset / cancelTask', () => {
  it('closeModal chiude il modal', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tc1' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({ force_recalculate: true }); });
    expect(result.current.showModal).toBe(true);

    act(() => { result.current.closeModal(); });
    expect(result.current.showModal).toBe(false);
  });

  it('reset riporta tutto a zero', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tc2' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    act(() => { result.current.reset(); });
    expect(result.current.showModal).toBe(false);
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.result).toBeNull();
    expect(result.current.progress).toBe(0);
    expect(result.current.taskStatus).toBeNull();
    expect(stopPollingMock).toHaveBeenCalled();
  });

  it('cancelTask richiede la revoca e mantiene il polling fino allo stato terminale', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tc3' });
    cancelTaskMock.mockResolvedValue({ status: 'cancellation_requested' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    await act(async () => { await result.current.cancelTask(); });
    expect(cancelTaskMock).toHaveBeenCalledWith('tc3');
    expect(stopPollingMock).not.toHaveBeenCalled();
    expect(result.current.taskStatus).toBe('pending');
    expect(result.current.isExecuting).toBe(true);
    expect(result.current.progressMessage).toMatch(/annullamento richiesto/i);

    const onStatus = startPollingMock.mock.calls[0][1];
    act(() => { onStatus({ status: 'success', result: { rows_count: 1 } }); });
    await waitFor(() => { expect(result.current.isExecuting).toBe(false); });
    expect(result.current.taskStatus).toBe('success');
    expect(result.current.result?.success).toBe(true);
  });
});
