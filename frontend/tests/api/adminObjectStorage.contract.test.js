import { beforeEach, describe, expect, it, vi } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    delete: vi.fn(),
    post: vi.fn(),
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

  it('loads grouped project history with an optional tenant filter', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { projects: [], results_count: 0 } });
    const { listObjectStorageProjects } = await import(
      '../../src/api/adminObjectStorage'
    );

    await listObjectStorageProjects({ group_id: 'g1' });

    expect(httpMock.get).toHaveBeenCalledWith('/admin/object-storage/projects', {
      params: { group_id: 'g1' },
    });
  });

  it('purges previews in the requested namespace only', async () => {
    httpMock.delete.mockResolvedValueOnce({
      data: { deleted: true, deleted_count: 2, scope: 'canonical' },
    });
    const { deleteAllStoragePreviews } = await import(
      '../../src/api/adminObjectStorage'
    );

    const result = await deleteAllStoragePreviews({
      scope: 'canonical',
      group_id: 'g1',
    });

    expect(httpMock.delete).toHaveBeenCalledWith('/admin/object-storage/previews', {
      params: { scope: 'canonical', group_id: 'g1' },
    });
    expect(result.deleted_count).toBe(2);
  });

  it('runs the server-side orphan cleanup in apply mode', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { orphan_results: { rows_deleted: 2 }, errors: [] },
    });
    const { cleanupOrphanStorage } = await import(
      '../../src/api/adminObjectStorage'
    );

    await cleanupOrphanStorage(['p-1']);

    expect(httpMock.post).toHaveBeenCalledWith(
      '/projects/admin/cleanup-orphans',
      { project_ids: ['p-1'] },
      { params: { dry_run: false } },
    );
  });
});
