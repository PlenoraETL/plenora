/**
 * Sessione 6 P1 — downloadTaskOutput 410 Gone.
 *
 * Pre-fix: 410 Gone (output scaduto per retention policy) cadeva
 * nell'interceptor http come errore generico "Errore di comunicazione
 * con il server". L'utente non capiva il motivo.
 *
 * Post-fix: gestione 410 esplicita con messaggio dedicato + code
 * TASK_OUTPUT_GONE per branching downstream.
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


describe('downloadTaskOutput 410 Gone gestito (sessione 6 P1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('su 410 solleva con code TASK_OUTPUT_GONE e messaggio user-friendly', async () => {
    const err = new Error('Request failed with status code 410');
    err.response = { status: 410, data: {} };
    httpMock.get.mockRejectedValueOnce(err);

    const { downloadTaskOutput } = await import('../../src/api/tasks');

    await expect(downloadTaskOutput('task-1')).rejects.toMatchObject({
      code: 'TASK_OUTPUT_GONE',
      status: 410,
      message: expect.stringContaining('conservazione'),
    });
  });

  it('non intercetta status diversi da 410', async () => {
    const err = new Error('Request failed with status code 500');
    err.response = { status: 500, data: { message: 'Server error' } };
    httpMock.get.mockRejectedValueOnce(err);

    const { downloadTaskOutput } = await import('../../src/api/tasks');

    await expect(downloadTaskOutput('task-2')).rejects.toMatchObject({
      response: { status: 500 },
    });
  });
});
