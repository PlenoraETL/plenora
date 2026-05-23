/**
 * Sessione 6 P1 — deleteUser/deleteGroup 204 No Content shape.
 *
 * Pre-fix: `.then(r => r.data)` ritornava stringa vuota su DELETE 204.
 * I consumer che facevano `result.id` ricevevano undefined silenziosamente.
 *
 * Post-fix: ritorna {success: true, id} canonico cosi' caller possono
 * branchare su success in modo esplicito.
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


describe('deleteUser / deleteGroup shape 204 (sessione 6 P1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('deleteUser ritorna {success: true, id} su 204', async () => {
    httpMock.delete.mockResolvedValueOnce({ status: 204, data: '' });

    const { deleteUser } = await import('../../src/api/users');
    const result = await deleteUser('user-1');

    expect(result).toEqual({ success: true, id: 'user-1' });
  });

  it('deleteGroup ritorna {success: true, id} su 204', async () => {
    httpMock.delete.mockResolvedValueOnce({ status: 204, data: '' });

    const { deleteGroup } = await import('../../src/api/users');
    const result = await deleteGroup('group-1');

    expect(result).toEqual({ success: true, id: 'group-1' });
  });
});
