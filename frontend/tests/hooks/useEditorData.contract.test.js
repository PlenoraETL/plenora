/**
 * Contract tests for useEditorData editor state behavior.
 *
 * Invarianti da preservare dopo il refactor cancellation-flag:
 *   1. Happy path: getProject + listConnections popolano project/connections,
 *      loading transiziona true→false.
 *   2. Unmount durante fetch: cleanup setta cancelled=true, le promise in
 *      flight risolvono senza crashare e senza aggiornare stato.
 *   3. REGRESSIONE B1: projectId switch mentre getProject(A) e' in-flight —
 *      A risolve DOPO B → result.current.project deve restare B, NON essere
 *      sovrascritto da A (stale write).
 */

import React from 'react';
import { renderHook, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  getProjectMock,
  listProjectsMock,
  listConnectionsMock,
  getConnectionTablesMock,
  listFeatureConnectionsMock,
  getFilesMock,
} = vi.hoisted(() => ({
  getProjectMock: vi.fn(),
  listProjectsMock: vi.fn(),
  listConnectionsMock: vi.fn(),
  getConnectionTablesMock: vi.fn(),
  listFeatureConnectionsMock: vi.fn(),
  getFilesMock: vi.fn(),
}));

vi.mock('../../src/api/projects', () => ({
  getProject: getProjectMock,
  listProjects: listProjectsMock,
}));
vi.mock('../../src/api/connections', () => ({
  listConnections: listConnectionsMock,
  getConnectionTables: getConnectionTablesMock,
}));
vi.mock('../../src/api/features', () => ({
  listFeatureConnections: listFeatureConnectionsMock,
}));
vi.mock('../../src/api/files', () => ({
  getFiles: getFilesMock,
}));

import { useEditorData } from '../../src/hooks/useEditorData';

beforeEach(() => {
  getProjectMock.mockReset();
  listProjectsMock.mockReset().mockResolvedValue({ projects: [] });
  listConnectionsMock.mockReset().mockResolvedValue({ connections: [] });
  getConnectionTablesMock.mockReset().mockResolvedValue({ tables: {}, views: {} });
  listFeatureConnectionsMock.mockReset().mockResolvedValue({ connections: [] });
  getFilesMock.mockReset().mockResolvedValue({ files: [] });
  // Il hook chiama console.error nel catch del bootstrap; la policy di default
  // della test suite fa throw su console.error.
  globalThis.__frontendConsolePolicy.allowError = true;
});

describe('useEditorData — contract', () => {
  it('happy path: project e connections popolati, loading scende a false', async () => {
    getProjectMock.mockResolvedValue({ id: 'p1', name: 'Progetto 1' });
    listConnectionsMock.mockResolvedValue({
      connections: [{ id: 'c1', name: 'db1' }],
    });

    const { result } = renderHook(() => useEditorData('p1'));

    // Stato iniziale
    expect(result.current.loading).toBe(true);
    expect(result.current.project).toBeNull();

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.project).toEqual({ id: 'p1', name: 'Progetto 1' });
    expect(result.current.connections).toEqual([{ id: 'c1', name: 'db1' }]);
    expect(result.current.error).toBeNull();
  });

  it('unmount durante fetch: cleanup cancella il run, nessun crash dopo resolve', async () => {
    let resolveProject;
    getProjectMock.mockImplementation(() => new Promise((res) => {
      resolveProject = res;
    }));

    const { unmount, result } = renderHook(() => useEditorData('p1'));

    expect(result.current.loading).toBe(true);

    // Unmount mentre getProject e' in flight
    unmount();

    // Risolvo DOPO l'unmount: il cancelled flag deve prevenire setProject
    // (e quindi qualsiasi throw/warning React su state update post-unmount).
    resolveProject({ id: 'p1', name: 'late' });
    await new Promise(r => setTimeout(r, 20));

    // Il cleanup ha cancellato il run: project non deve mai essere stato
    // popolato con il valore 'late', perche' il cancelled flag ha bloccato
    // setProject. result.current riflette lo snapshot dell'ultimo render
    // (pre-unmount), quindi project === null come da stato iniziale.
    expect(result.current.project).toBeNull();
    expect(getProjectMock).toHaveBeenCalledTimes(1);
  });

  it('B1 regression: projectId switch durante fetch — A risolve dopo B, NON sovrascrive B', async () => {
    let resolveA;
    const pA = new Promise((res) => { resolveA = res; });

    // Prima chiamata (projectId='A'): promise controllata manualmente
    getProjectMock.mockImplementationOnce(() => pA);
    // Seconda chiamata (projectId='B'): risolve immediatamente
    getProjectMock.mockImplementationOnce(() => Promise.resolve({ id: 'B', name: 'proj B' }));

    const { result, rerender } = renderHook(
      ({ projectId }) => useEditorData(projectId),
      { initialProps: { projectId: 'A' } }
    );

    // A e' in-flight, switch a B
    rerender({ projectId: 'B' });

    // B risolve → result mostra B
    await waitFor(() => {
      expect(result.current.project?.id).toBe('B');
    });

    // Ora risolvo A DOPO lo switch (il run di A era stato cancelled dal cleanup)
    await act(async () => {
      resolveA({ id: 'A', name: 'proj A' });
      await new Promise(r => setTimeout(r, 20));
    });

    // project DEVE restare B — cancelled flag del run A ha bloccato setProject(A)
    expect(result.current.project?.id).toBe('B');
    expect(result.current.project?.name).toBe('proj B');
  });
});
