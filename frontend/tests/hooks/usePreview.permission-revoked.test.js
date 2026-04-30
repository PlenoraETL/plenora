/**
 * Contract test — usePreview + permission_revoked (PR-cleanup UX Tier 1).
 *
 * Backend (`pipeline_task.py:392-398`) propaga
 * `result.status='permission_revoked'` quando il ruolo utente viene
 * declassato/disabilitato mid-task. Pre-fix: l'hook trattava il caso
 * come `result.success === false` -> errore generico, perdendo la
 * coerenza UX con gli altri 3 modal (Carica/Execution/Eseguibile).
 *
 * Post-fix: l'hook espone flag `permissionRevoked: true`, consumer
 * (PreviewModal) renderizza <PermissionRevokedBanner> inline.
 *
 * 3 cases:
 *   1. task.status=success + result.status=permission_revoked -> flag true
 *   2. task.status=failure + result.status=permission_revoked -> flag true
 *   3. task.status=success + result.success=true (classico) -> flag false
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { startPollingMock, previewProjectMock } = vi.hoisted(() => ({
  startPollingMock: vi.fn(),
  previewProjectMock: vi.fn(),
}));

vi.mock('../../src/hooks/useTaskPolling', () => ({
  useTaskPolling: () => ({
    mountedRef: { current: true },
    stopPolling: vi.fn(),
    startPolling: startPollingMock,
  }),
}));

vi.mock('../../src/api', () => ({
  previewProject: previewProjectMock,
}));

import { usePreview } from '../../src/hooks/usePreview';


function setupHook() {
  return renderHook(() => usePreview('proj-1'));
}


describe('usePreview — permission_revoked detection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    previewProjectMock.mockResolvedValue({
      async: true,
      task_id: 'task-1',
      message: 'in coda',
    });
  });

  it('task.status=success + result.status=permission_revoked -> permissionRevoked=true', async () => {
    const { result } = setupHook();
    await act(async () => {
      await result.current.handlePreview();
    });

    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'success',
        result: {
          status: 'permission_revoked',
          message: 'Ruolo declassato a viewer mid-preview',
        },
      });
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.permissionRevoked).toBe(true);
    expect(result.current.error).toMatch(/viewer/i);
    expect(result.current.data).toBeNull();
  });

  it('task.status=failure + result.status=permission_revoked -> permissionRevoked=true', async () => {
    const { result } = setupHook();
    await act(async () => {
      await result.current.handlePreview();
    });

    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'failure',
        result: {
          status: 'permission_revoked',
          message: 'Utenza disabilitata',
        },
      });
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.permissionRevoked).toBe(true);
    expect(result.current.error).toMatch(/disabilitata/i);
  });

  it('task.status=success classico -> permissionRevoked=false (no-op default)', async () => {
    const { result } = setupHook();
    await act(async () => {
      await result.current.handlePreview();
    });

    const pollCallback = startPollingMock.mock.calls[0][1];
    act(() => {
      pollCallback({
        status: 'success',
        result: {
          success: true,
          rows: [{ id: 1 }],
          columns: [{ name: 'id', type: 'int' }],
        },
      });
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.permissionRevoked).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.data).toEqual({
      success: true,
      rows: [{ id: 1 }],
      columns: [{ name: 'id', type: 'int' }],
    });
  });
});
