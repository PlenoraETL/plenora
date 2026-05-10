/**
 * Contract tests for useImdfBundles ResultNode integration.
 *
 * Invarianti estratti da ResultNode.jsx che il hook deve preservare:
 *   1. isImdfMode=false: nessuna fetch, ritorna defaults (bundles [], bundleTaskId null).
 *   2. isImdfMode=true: listImdfBundles chiamata con {project_id}, popola bundles.
 *      Se non c'e' live task nello store → auto-select del piu' recente (arr[0]).
 *   3. handleDeleteBundle: chiama deleteImdfBundle server-side, rimuove dal
 *      local state, resetta selectedBundleId se matchava.
 *   4. bundleTaskId derivato: selectedBundleId || liveBundleTaskId || null.
 *      isBundleFallback=true sse non c'e' live ma c'e' override esplicito.
 */

import React from 'react';
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  listImdfBundlesMock,
  getImdfBundleMetadataMock,
  deleteImdfBundleMock,
  useCanvasStoreMock,
  toastErrorMock,
  toastMock,
  useToastMock,
  storeRef,
} = vi.hoisted(() => {
  const toastErrorMock = vi.fn();
  const toastMock = {
    error: toastErrorMock,
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  };
  return {
    listImdfBundlesMock: vi.fn(),
    getImdfBundleMetadataMock: vi.fn(),
    deleteImdfBundleMock: vi.fn(),
    useCanvasStoreMock: vi.fn(),
    toastErrorMock,
    toastMock,
    useToastMock: vi.fn(() => toastMock),
    storeRef: {
      current: {
        projectId: 'p1',
        lastTaskResult: null,
        setLastTaskResult: vi.fn(),
      },
    },
  };
});

vi.mock('../../src/api/imdf', () => ({
  listImdfBundles: listImdfBundlesMock,
  getImdfBundleMetadata: getImdfBundleMetadataMock,
  deleteImdfBundle: deleteImdfBundleMock,
}));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: useToastMock,
}));

// Simula il pattern di Zustand: useCanvasStore(selector) invoca selector(state),
// useCanvasStore.getState() ritorna lo stato piatto.
useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
useCanvasStoreMock.getState = () => storeRef.current;

import { useImdfBundles } from '../../src/components/nodes/result/useImdfBundles';

beforeEach(() => {
  listImdfBundlesMock.mockReset();
  getImdfBundleMetadataMock.mockReset();
  deleteImdfBundleMock.mockReset();
  toastErrorMock.mockReset();
  toastMock.success.mockReset();
  toastMock.warning.mockReset();
  toastMock.info.mockReset();
  useToastMock.mockReturnValue(toastMock);
  storeRef.current = {
    projectId: 'p1',
    lastTaskResult: null,
    setLastTaskResult: vi.fn(),
  };
  useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
  useCanvasStoreMock.getState = () => storeRef.current;
  // Console policy: il hook fa console.warn su errori lista — non vogliamo
  // che facciano fallire i test.
  globalThis.__frontendConsolePolicy.allowError = true;
});


describe('useImdfBundles — contract', () => {
  it('isImdfMode=false: nessuna fetch, ritorna defaults', () => {
    const { result } = renderHook(() => useImdfBundles({ isImdfMode: false }));
    expect(listImdfBundlesMock).not.toHaveBeenCalled();
    expect(result.current.bundles).toEqual([]);
    expect(result.current.bundleTaskId).toBeNull();
    expect(result.current.selectedBundle).toBeNull();
    expect(result.current.isBundleFallback).toBe(false);
    expect(result.current.imdfData).toBeNull();
    expect(result.current.loadingBundles).toBe(false);
  });

  it('isImdfMode=true: fetch con project_id, popola bundles e auto-seleziona il piu recente', async () => {
    const fixture = [
      { task_id: 't2', created_at: '2026-04-21T10:00:00Z' },
      { task_id: 't1', created_at: '2026-04-20T10:00:00Z' },
    ];
    listImdfBundlesMock.mockResolvedValue(fixture);

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));

    await waitFor(() => {
      expect(listImdfBundlesMock).toHaveBeenCalledWith({ project_id: 'p1' });
      expect(result.current.bundles).toEqual(fixture);
    });

    // Senza live task → auto-select del primo (arr[0] = "t2")
    await waitFor(() => {
      expect(result.current.selectedBundleId).toBe('t2');
      expect(result.current.bundleTaskId).toBe('t2');
      expect(result.current.isBundleFallback).toBe(true);
      expect(result.current.loadingBundles).toBe(false);
    });
  });

  it('isImdfMode=true con live task nello store: bundleTaskId deriva dal live, NON fallback', async () => {
    storeRef.current = {
      projectId: 'p1',
      lastTaskResult: { imdf: { bundle_task_id: 'live-99', manifest: { venue: [] } } },
      setLastTaskResult: vi.fn(),
    };
    useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
    useCanvasStoreMock.getState = () => storeRef.current;

    listImdfBundlesMock.mockResolvedValue([
      { task_id: 'live-99' },
      { task_id: 'older' },
    ]);

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));

    await waitFor(() => {
      expect(result.current.bundles.length).toBe(2);
    });

    // Live task presente → bundleTaskId=live-99, fallback=false, niente auto-select
    expect(result.current.bundleTaskId).toBe('live-99');
    expect(result.current.isBundleFallback).toBe(false);
    expect(result.current.selectedBundleId).toBeNull();

    // selectedBundle e' il record matching in bundles
    expect(result.current.selectedBundle).toMatchObject({ task_id: 'live-99' });

    // imdfData esposto dallo store
    expect(result.current.imdfData).toMatchObject({ bundle_task_id: 'live-99' });

    // Store gia' ha manifest per questo id → NO hydrate
    expect(getImdfBundleMetadataMock).not.toHaveBeenCalled();
  });

  it('handleDeleteBundle: chiama API server, rimuove dal local state, resetta selezione se match', async () => {
    listImdfBundlesMock.mockResolvedValue([
      { task_id: 'a' },
      { task_id: 'b' },
    ]);
    deleteImdfBundleMock.mockResolvedValue({ ok: true });

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));

    // Attendi popolamento lista + auto-select (il piu recente = 'a')
    await waitFor(() => {
      expect(result.current.bundles.length).toBe(2);
      expect(result.current.selectedBundleId).toBe('a');
    });

    // Delete 'a' (quello attualmente selezionato)
    await act(async () => {
      await result.current.handleDeleteBundle('a');
    });

    // API chiamata, lista aggiornata, selezione resettata
    expect(deleteImdfBundleMock).toHaveBeenCalledWith('a');
    expect(result.current.bundles).toEqual([{ task_id: 'b' }]);
    expect(result.current.selectedBundleId).toBeNull();
  });

  it('handleDeleteBundle: errore server -> toast.error, lista invariata, selezione invariata', async () => {
    listImdfBundlesMock.mockResolvedValue([
      { task_id: 'a' },
      { task_id: 'b' },
    ]);
    const apiErr = Object.assign(new Error('bundle in uso'), {
      response: { data: { detail: 'bundle in uso' } },
    });
    deleteImdfBundleMock.mockRejectedValue(apiErr);

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));

    await waitFor(() => {
      expect(result.current.bundles.length).toBe(2);
      expect(result.current.selectedBundleId).toBe('a');
    });

    await act(async () => {
      await result.current.handleDeleteBundle('a');
    });

    // API tentata, ma niente mutazione locale e niente alert():
    // l'errore va al toast con il detail server.
    expect(deleteImdfBundleMock).toHaveBeenCalledWith('a');
    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock.mock.calls[0][0]).toMatch(/bundle in uso/);
    expect(result.current.bundles.length).toBe(2);
    expect(result.current.selectedBundleId).toBe('a');
  });

  it('handleDeleteBundle: errore senza response.data.detail -> fallback a e.message', async () => {
    listImdfBundlesMock.mockResolvedValue([{ task_id: 'a' }]);
    deleteImdfBundleMock.mockRejectedValue(new Error('network down'));

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));
    await waitFor(() => { expect(result.current.bundles.length).toBe(1); });

    await act(async () => {
      await result.current.handleDeleteBundle('a');
    });

    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock.mock.calls[0][0]).toMatch(/network down/);
  });

  it('setSelectedBundleId: override manuale fa scattare bundleTaskId senza live', async () => {
    listImdfBundlesMock.mockResolvedValue([{ task_id: 'x' }, { task_id: 'y' }]);

    const { result } = renderHook(() => useImdfBundles({ isImdfMode: true }));

    await waitFor(() => {
      expect(result.current.bundles.length).toBe(2);
    });

    act(() => { result.current.setSelectedBundleId('y'); });

    await waitFor(() => {
      expect(result.current.selectedBundleId).toBe('y');
      expect(result.current.bundleTaskId).toBe('y');
      expect(result.current.isBundleFallback).toBe(true);
    });
  });
});
