import { beforeEach, describe, expect, it, vi } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: { get: vi.fn() },
}));

vi.mock('../../src/api/http', () => ({ default: httpMock }));

describe('admin task runtime api contract', () => {
  beforeEach(() => vi.clearAllMocks());

  it('reads the superadmin task runtime endpoint', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { status: 'ok' } });
    const { getTaskRuntime } = await import('../../src/api/adminTaskRuntime');

    const result = await getTaskRuntime();

    expect(httpMock.get).toHaveBeenCalledWith('/admin/task-runtime');
    expect(result.status).toBe('ok');
  });
});
