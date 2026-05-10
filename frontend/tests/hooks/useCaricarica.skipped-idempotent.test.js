/**
 * Contract tests for useCaricarica skipped_idempotent handling.
 *
 * Backend `app/core/carica/models.py:81` espone `WriteStatus.SKIPPED_IDEMPOTENT`,
 * propagato come `result.status='skipped_idempotent'` quando il run_id e'
 * gia' stato completato in precedenza (idempotency guard, N15).
 *
 * Regression contract: `data.result?.status === 'skipped_idempotent'` is success-equivalent
 * perche' il match era sul literal "success", trattando l'idempotent come errore.
 *
 * Current contract: the hook accepts both 'success' and 'skipped_idempotent' as
 * success-no-op, ed espone `result.skippedIdempotent=true` per consentire
 * al modal di mostrare un messaggio dedicato (no rosso, no "errore").
 *
 * Coperture:
 *   1. result.status='skipped_idempotent' -> hook setResult({success:true, skippedIdempotent:true})
 *   2. result.status='success' classico -> hook setResult({success:true, skippedIdempotent:false})
 *   3. result.status='permission_revoked' -> hook setResult({success:false, permissionRevoked:true})
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { startPollingMock, executeProjectQueryMock, createTaskMock } = vi.hoisted(() => ({
  startPollingMock: vi.fn(),
  executeProjectQueryMock: vi.fn(),
  createTaskMock: vi.fn(),
}));

vi.mock('../../src/hooks/useTaskPolling', () => ({
  useTaskPolling: () => ({
    mountedRef: { current: true },
    stopPolling: vi.fn(),
    startPolling: startPollingMock,
  }),
}));

vi.mock('../../src/api', () => ({
  executeProjectQuery: executeProjectQueryMock,
  cancelTask: vi.fn(),
  createTask: createTaskMock,
}));

import { useCaricarica } from '../../src/hooks/useCaricarica';


function setupHook() {
  return renderHook(() =>
    useCaricarica('proj-1', { getCanvas: () => ({ nodes: [], edges: [] }) })
  );
}


describe('useCaricarica — skipped_idempotent + permission_revoked detection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    executeProjectQueryMock.mockResolvedValue({ success: true, result: { rows: [] } });
    createTaskMock.mockResolvedValue({ task_id: 'task-1' });
  });

  it('result.status=skipped_idempotent -> success-no-op (success:true, skippedIdempotent:true)', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleExecute('carica-node', {
        schema_name: 'plenora', table_name: 'foo', write_mode: 'append',
      });
    });

    // Il polling callback simula il task che torna result.status='skipped_idempotent'
    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'success',
        result: {
          status: 'skipped_idempotent',
          table_name: 'plenora.foo',
          rows_written: 0,
        },
      });
    });

    await waitFor(() => {
      expect(result.current.result).not.toBeNull();
    });
    expect(result.current.result.success).toBe(true);
    expect(result.current.result.skippedIdempotent).toBe(true);
    expect(result.current.result.permissionRevoked).toBe(false);
  });

  it('result.status=success -> success classico (skippedIdempotent:false)', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleExecute('carica-node', {
        schema_name: 'plenora', table_name: 'bar', write_mode: 'create',
      });
    });

    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'success',
        result: {
          status: 'success',
          table_name: 'plenora.bar',
          rows_written: 100,
        },
      });
    });

    await waitFor(() => {
      expect(result.current.result).not.toBeNull();
    });
    expect(result.current.result.success).toBe(true);
    expect(result.current.result.skippedIdempotent).toBe(false);
    expect(result.current.result.permissionRevoked).toBe(false);
  });

  it('result.status=permission_revoked dentro task=failure -> permissionRevoked:true', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleExecute('carica-node', {
        schema_name: 'plenora', table_name: 'baz', write_mode: 'append',
      });
    });

    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'failure',
        result: {
          status: 'permission_revoked',
          message: 'Ruolo declassato a viewer mid-task',
        },
      });
    });

    await waitFor(() => {
      expect(result.current.result).not.toBeNull();
    });
    expect(result.current.result.success).toBe(false);
    expect(result.current.result.permissionRevoked).toBe(true);
    expect(result.current.result.message).toMatch(/viewer/i);
  });
});
