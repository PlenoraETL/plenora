/**
 * Contract test — useExecution (FE-NEXT-1, FE-T2).
 *
 * handleExecute(opts) parte subito e apre la finestra in isExecuting: input,
 * opzioni e conferme arrivano gia' in opts dalla finestra unica «Prima di
 * avviare» (editor/PipelineRunner, Blocco A regola 5). Un 409
 * preparation_changed torna a chi ha avviato (niente esito di errore).
 *
 * Strategia mock:
 *  - utils/taskPoller: stub via vi.mock -> pollTask spy che NON avvia un
 *    vero polling; `emit(data)` simula una risposta di GET /tasks/{id} con
 *    la regola vera (utils/taskOutcome) e la consegna a useTask (Fase 5 T2).
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

vi.mock('../../src/utils/taskPoller', () => ({
  pollTask: (...args) => { startPollingMock(...args); return stopPollingMock; },
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

import { useExecution, executionModalProps, executionToolbarProps } from '../../src/hooks/useExecution';
import { taskOutcome } from '../../src/utils/taskOutcome';

// Risposta di GET /tasks/{id} consegnata al poll registrato (l'ultimo, o il n-esimo).
function emit(data, call = -1) {
  const calls = startPollingMock.mock.calls;
  const [, opts] = calls.at(call);
  const o = data.phase ? data : taskOutcome(data, opts.outcome);
  opts.onUpdate?.(o, data);
  if (o.done) opts.onDone?.(o);
  return o.done;
}

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

    let done;
    act(() => {
      done = emit({
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

    let done;
    act(() => {
      done = emit({ status: 'failure', error: 'boom', progress: 50 });
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

    let done;
    act(() => { done = emit({ status: 'revoked' }); });
    expect(done).toBe(true);
    await waitFor(() => { expect(result.current.isExecuting).toBe(false); });
    expect(result.current.result?.message).toMatch(/annullata/i);
  });

  it('callback con status=pending/progress -> done=false (continua il polling)', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tp4' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });

    let done;
    act(() => { done = emit({ status: 'progress', progress: 30, message: 'lavorando' }); });
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
    expect(result.current.cancelling).toBe(true);

    act(() => { emit({ status: 'success', result: { rows_count: 1 } }); });
    await waitFor(() => { expect(result.current.isExecuting).toBe(false); });
    expect(result.current.taskStatus).toBe('success');
    expect(result.current.result?.success).toBe(true);
  });
});


// Fase 5 T2 (R4.6, R4.11, R4.12, R9.2, R9.4, R9.8, R9.14, N4): stesso ciclo
// per i tre editor, esiti veri, finestra chiudibile e riapribile.
describe('useExecution — Fase 5 T2', () => {
  it('Annulla e progresso per ogni editor (non solo Arricchisci): le props della finestra sono uguali', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz1' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });
    act(() => { emit({ status: 'progress', progress: 40, message: 'passo 2' }); });
    const props = executionModalProps(result.current);
    expect(typeof props.onCancel).toBe('function');
    expect(props.progress).toBe(40);
    expect(props.progressMessage).toBe('passo 2');
    expect(props.isExecuting).toBe(true);
  });

  it('task perso (404 ripetuti o errori del server): esito di errore, mai "completata"', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz2' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });
    act(() => { emit({ phase: 'lost', done: true, error: 'non più disponibile', code: 'task_not_found', result: null }); });
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.result?.success).toBe(false);
    expect(result.current.result?.message).toMatch(/non più disponibile/);
    expect(result.current.taskStatus).toBe('failure');
  });

  it('permission_revoked: banner, nessun successo ne\' download', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz3' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });
    act(() => { emit({ status: 'success', result: { status: 'permission_revoked', success: false, message: 'Ruolo cambiato' } }); });
    expect(result.current.result?.success).toBe(false);
    expect(result.current.result?.permissionRevoked).toBe(true);
    expect(setLastTaskResultMock).not.toHaveBeenCalled();
  });

  it('finestra chiusa durante l\'esecuzione: la barra la riapre; esito arrivato a finestra chiusa = "Vedi l\'esito"', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz4' });
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });
    // onClose riceve l'evento del clic: non azzera niente.
    act(() => { result.current.closeModal({ type: 'click' }); });
    expect(result.current.showModal).toBe(false);
    expect(typeof executionToolbarProps(result.current).onShowExecution).toBe('function');
    act(() => { emit({ status: 'success', result: { rows_count: 3 } }); });
    expect(executionToolbarProps(result.current).executionUnseen).toBe(true);
    act(() => { executionToolbarProps(result.current).onShowExecution(); });
    expect(result.current.showModal).toBe(true);
    expect(result.current.result?.rows_count).toBe(3);
    expect(executionToolbarProps(result.current).onShowExecution).toBeNull();
  });

  it('cambio di progetto: il polling di A si ferma e niente di A resta su B', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz5' });
    const { result, rerender } = renderHook(({ id }) => useExecution(id), { initialProps: { id: 'proj-A' } });
    await act(async () => { await result.current.handleExecute({}); });
    rerender({ id: 'proj-B' });
    expect(stopPollingMock).toHaveBeenCalled();
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.showModal).toBe(false);
    expect(result.current.taskId).toBeNull();
  });

  it('download fallito (410 scaduto): il messaggio arriva alla finestra, nessuna promessa persa', async () => {
    executeProjectQueryMock.mockResolvedValue({ async: true, task_id: 'tz6' });
    downloadTaskOutputMock.mockRejectedValue(Object.assign(new Error("Il file di output non è più disponibile: scaduto per la policy di retention."), { status: 410 }));
    const { result } = renderHook(() => useExecution('proj-1'));
    await act(async () => { await result.current.handleExecute({}); });
    act(() => { emit({ status: 'success', result: { output_file: 'x.csv' } }); });
    await act(async () => { await result.current.downloadTaskResult(); });
    expect(result.current.downloadError).toMatch(/non è più disponibile/i);
    expect(result.current.downloading).toBe(false);
  });
});
