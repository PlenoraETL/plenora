import { beforeEach, describe, expect, it, vi } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({ default: httpMock }));

describe('admin object storage api contract', () => {
  beforeEach(() => vi.clearAllMocks());

  it('deletes one exact key through the superadmin endpoint', async () => {
    httpMock.delete.mockResolvedValueOnce({
      data: { deleted: true, key: 'tenants/g1/results/p1/r1.bin' },
    });
    const { deleteStorageObject } = await import(
      '../../src/api/adminObjectStorage'
    );

    const result = await deleteStorageObject(
      'tenants/g1/results/p1/r1.bin',
    );

    expect(httpMock.delete).toHaveBeenCalledWith('/admin/object-storage', {
      params: { key: 'tenants/g1/results/p1/r1.bin' },
    });
    expect(result.deleted).toBe(true);
  });
});
