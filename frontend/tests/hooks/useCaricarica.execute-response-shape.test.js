/**
 * Regression test per useCaricarica vs shape reale di executeProjectQuery.
 *
 * Bug originale (plenora-bug-contracts-flows P0):
 *   `executeProjectQuery` backend ritorna `{task_id, status, async}` (Celery
 *   async task), NON `{success: bool}`. Il vecchio codice faceva
 *   `if (!executeResult.success) throw new Error('Errore esecuzione query')`
 *   → `!undefined` → throw → modal mostrava "Errore esecuzione query"
 *   per OGNI tentativo di CARICA, mascherando il vero esito.
 *
 * Il test esistente `useCaricarica.skipped-idempotent.test.js` mockava
 * `{success: true, result: {rows: []}}` — shape inventata non realistica —
 * quindi NON catturava la regressione. Questo test usa la shape REALE del
 * BE (estratta da `backend/app/core/projects/router.py` execute endpoint).
 *
 * Contract: con response async legittima, l'hook deve:
 *   - non sollevare nel try/catch (no setResult success:false con error)
 *   - chiamare createTask subito dopo
 *   - registrare il taskId di createTask e avviare il polling
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

describe('useCaricarica — executeProjectQuery response shape contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Shape REALE che il BE ritorna dal POST /projects/{id}/execute:
    // Celery task accepted, ritorna metadata async (no `success`).
    executeProjectQueryMock.mockResolvedValue({
      task_id: 'pipeline-task-abc',
      status: 'pending',
      async: true,
    });
    createTaskMock.mockResolvedValue({ task_id: 'carica-task-xyz' });
  });

  it('non setta error result quando executeProjectQuery ritorna {task_id, status:pending, async:true}', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleExecute('carica-node', {
        schema_name: 'plenora', table_name: 'foo', write_mode: 'append',
      });
    });

    // Pre-fix: result sarebbe stato { success: false, message: 'Errore esecuzione query' }
    // perche' il check `!executeResult.success` sollevava su undefined.
    // Post-fix: nessun error set, l'hook procede a createTask.
    expect(result.current.result).toBeNull();
    expect(createTaskMock).toHaveBeenCalledTimes(1);
    expect(createTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({ task_type: 'carica', project_id: 'proj-1' })
    );
    expect(startPollingMock).toHaveBeenCalledTimes(1);
    expect(startPollingMock.mock.calls[0][0]).toBe('carica-task-xyz');
  });

  it('propaga errore HTTP di executeProjectQuery al catch (modal mostra "Errore durante il caricamento")', async () => {
    executeProjectQueryMock.mockRejectedValueOnce(new Error('Network timeout'));
    // L'hook logga `console.error('[useCaricarica] Errore:', ...)` nel catch:
    // path atteso del test, non un bug. Disabilito la policy console-error
    // strict per questa singola asserzione.
    globalThis.__frontendConsolePolicy.allowError = true;

    const { result } = setupHook();

    await act(async () => {
      await result.current.handleExecute('carica-node', {
        schema_name: 'plenora', table_name: 'foo', write_mode: 'append',
      });
    });

    await waitFor(() => {
      expect(result.current.result).not.toBeNull();
    });
    expect(result.current.result.success).toBe(false);
    expect(result.current.result.message).toBe('Network timeout');
    expect(createTaskMock).not.toHaveBeenCalled();
    expect(startPollingMock).not.toHaveBeenCalled();
  });
});
