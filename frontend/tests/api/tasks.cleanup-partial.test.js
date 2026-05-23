/**
 * Sessione 2 P0 — cleanupTaskCompletely 207 Multi-Status propagato.
 *
 * Bug originale: il check 207 era solo `console.warn` silente. Il
 * claim GDPR "nessuna traccia dell'esecuzione" del ruolo esecutore
 * veniva VIOLATO silenziosamente — l'utente vedeva "download
 * completato + cleanup OK" mentre alcuni step lasciavano dati
 * residui (es. delete_log_runs fallito).
 *
 * Post-fix: return shape strutturato con flag `partialCleanup`. Il
 * caller (EsecutoreHome) puo' mostrare un toast di trasparenza.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));


describe('cleanupTaskCompletely 207 partial cleanup (sessione 2 P0)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('200 OK → partialCleanup=false', async () => {
    httpMock.delete.mockResolvedValueOnce({
      status: 200,
      data: { status: 'success', cleanup_steps: ['delete_result', 'celery_forget'] },
    });

    const { cleanupTaskCompletely } = await import('../../src/api/tasks');
    const result = await cleanupTaskCompletely('task-1');

    expect(result.partialCleanup).toBe(false);
    expect(result.status).toBe('success');
  });

  it('207 Multi-Status → partialCleanup=true con failed_steps', async () => {
    httpMock.delete.mockResolvedValueOnce({
      status: 207,
      data: {
        status: 'partial',
        failed_steps: ['delete_log_runs', 'unlink_output_file'],
      },
    });

    const { cleanupTaskCompletely } = await import('../../src/api/tasks');
    const result = await cleanupTaskCompletely('task-2');

    expect(result.partialCleanup).toBe(true);
    expect(result.failedSteps).toEqual(['delete_log_runs', 'unlink_output_file']);
    expect(result.networkError).toBeUndefined();
  });

  it('network error → partialCleanup=true con networkError flag', async () => {
    httpMock.delete.mockRejectedValueOnce(new Error('Network unreachable'));

    const { cleanupTaskCompletely } = await import('../../src/api/tasks');
    const result = await cleanupTaskCompletely('task-3');

    expect(result.partialCleanup).toBe(true);
    expect(result.networkError).toBe(true);
    expect(result.errorMessage).toContain('Network unreachable');
  });
});
