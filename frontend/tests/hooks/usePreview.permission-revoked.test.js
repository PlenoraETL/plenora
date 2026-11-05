/**
 * Contract tests for usePreview / useInlinePreview (Fase 5 T2 sopra useTask).
 *
 * Backend (`pipeline_task.py`) propaga `result.status='permission_revoked'`
 * quando il ruolo utente viene declassato mid-task: il hook espone
 * `permissionRevoked: true` (banner nel PreviewModal), mai un successo.
 *
 * Fase 5 T2 (R4.12, R1-N2): annullata e task perso hanno un esito; chiudere
 * la finestra ferma il polling; due anteprime in fila -> vince l'ultima
 * (la risposta della prima arrivata dopo non scrive).
 *
 * Strategia: utils/taskPoller mockato, `emit(data)` consegna una risposta di
 * GET /tasks/{id} al poll registrato con la regola vera (utils/taskOutcome).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { startPollingMock, stopPollingMock, previewProjectMock } = vi.hoisted(() => ({
  startPollingMock: vi.fn(),
  stopPollingMock: vi.fn(),
  previewProjectMock: vi.fn(),
}));

vi.mock('../../src/utils/taskPoller', () => ({
  pollTask: (...args) => { startPollingMock(...args); return stopPollingMock; },
}));

vi.mock('../../src/api', () => ({
  previewProject: previewProjectMock,
  cancelTask: vi.fn(),
}));

import { usePreview } from '../../src/hooks/usePreview';
import { useInlinePreview } from '../../src/hooks/useInlinePreview';
import { taskOutcome } from '../../src/utils/taskOutcome';

function emit(data, call = -1) {
  const [, opts] = startPollingMock.mock.calls.at(call);
  const o = data.phase ? data : taskOutcome(data, opts.outcome);
  opts.onUpdate?.(o, data);
  if (o.done) opts.onDone?.(o);
}

function setupHook() {
  return renderHook(() => usePreview('proj-1'));
}

describe('usePreview — permission_revoked detection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    previewProjectMock.mockResolvedValue({ async: true, task_id: 'task-1', message: 'in coda' });
  });

  it('task.status=success + result.status=permission_revoked -> permissionRevoked=true', async () => {
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => {
      emit({ status: 'success', result: { status: 'permission_revoked', message: 'Ruolo declassato a viewer mid-preview' } });
    });
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.permissionRevoked).toBe(true);
    expect(result.current.error).toMatch(/viewer/i);
    expect(result.current.data).toBeNull();
  });

  it('task.status=failure + result.status=permission_revoked -> permissionRevoked=true', async () => {
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => {
      emit({ status: 'failure', result: { status: 'permission_revoked', message: 'Utente disabilitato' } });
    });
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.permissionRevoked).toBe(true);
    expect(result.current.error).toMatch(/disabilitato/i);
  });

  it('task.status=success classico -> permissionRevoked=false (no-op default)', async () => {
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => { emit({ status: 'success', result: { success: true, rows: [{ a: 1 }], columns: [{ name: 'a' }] } }); });
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.permissionRevoked).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.data?.rows).toHaveLength(1);
  });
});

describe('usePreview / useInlinePreview — Fase 5 T2', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('revoked: "Anteprima annullata", niente spinner per sempre', async () => {
    previewProjectMock.mockResolvedValue({ async: true, task_id: 't-r' });
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => { emit({ status: 'revoked' }); });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toMatch(/annullata/i);
  });

  it('task perso: errore con il motivo, non un\'attesa infinita', async () => {
    previewProjectMock.mockResolvedValue({ async: true, task_id: 't-l' });
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => { emit({ phase: 'lost', done: true, error: 'Operazione non più disponibile', result: null }); });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toMatch(/non più disponibile/);
  });

  it('chiudere la finestra ferma il polling', async () => {
    previewProjectMock.mockResolvedValue({ async: true, task_id: 't-c' });
    const { result } = setupHook();
    await act(async () => { await result.current.handlePreview(); });
    act(() => { result.current.closeModal(); });
    expect(stopPollingMock).toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it('"Aggiorna" con limiti diversi: la risposta della prima richiesta arrivata dopo non scrive', async () => {
    let resolveFirst;
    previewProjectMock
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }))
      .mockResolvedValueOnce({ async: true, task_id: 't-second' });
    const { result } = setupHook();
    let first;
    await act(async () => { first = result.current.handlePreview(10); await Promise.resolve(); });
    await act(async () => { await result.current.handlePreview(50); });
    await act(async () => { resolveFirst({ async: true, task_id: 't-first' }); await first; });
    // Un solo polling: quello della seconda richiesta.
    expect(startPollingMock).toHaveBeenCalledTimes(1);
    expect(startPollingMock.mock.calls[0][0]).toBe('t-second');
  });

  it('anteprima del passo A poi del passo B: i dati di A non compaiono sotto B', async () => {
    let resolveA;
    previewProjectMock.mockImplementation((pid, limit, nodeId) => (nodeId === 'A'
      ? new Promise((r) => { resolveA = r; })
      : Promise.resolve({ success: true, rows: [{ who: 'B' }], columns: [{ name: 'who' }] })));
    const { result } = renderHook(() => useInlinePreview('proj-1'));
    let runA;
    await act(async () => { runA = result.current.run(10, 'A'); await Promise.resolve(); });
    await act(async () => { await result.current.run(10, 'B'); });
    expect(result.current.data?.rows?.[0]?.who).toBe('B');
    await act(async () => { resolveA({ success: true, rows: [{ who: 'A' }], columns: [{ name: 'who' }] }); await runA; });
    expect(result.current.data?.rows?.[0]?.who).toBe('B');
    expect(result.current.status).toBe('success');
  });
});
