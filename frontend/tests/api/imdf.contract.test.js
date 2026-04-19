import { vi, describe, it, expect, beforeEach } from 'vitest';

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

describe('imdf api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('listImdfBundles forwards project filtering only when present', async () => {
    httpMock.get.mockResolvedValueOnce({ data: [] });
    httpMock.get.mockResolvedValueOnce({ data: [] });

    const { listImdfBundles } = await import('../../src/api/imdf');

    await listImdfBundles({ project_id: 'proj-7' });
    await listImdfBundles();

    expect(httpMock.get).toHaveBeenNthCalledWith(1, '/imdf/bundles', {
      params: { project_id: 'proj-7' },
    });
    expect(httpMock.get).toHaveBeenNthCalledWith(2, '/imdf/bundles', {
      params: {},
    });
  });

  it('validateImdfPublish preserves the full publish-check payload', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { ok: true } });

    const { validateImdfPublish } = await import('../../src/api/imdf');
    const payload = {
      bundle_task_id: 'bundle-1',
      connection_id: 'conn-1',
      fs_name: 'urby_sites',
      mode: 'upsert',
      folder: 'QA',
      match_columns: { unit: 'unit_id' },
    };

    const result = await validateImdfPublish(payload);

    expect(httpMock.post).toHaveBeenCalledWith('/imdf/validate-publish', payload);
    expect(result).toEqual({ ok: true });
  });

  it('deleteImdfBundle targets the bundle metadata endpoint by task id', async () => {
    httpMock.delete.mockResolvedValueOnce({ data: { deleted: true } });

    const { deleteImdfBundle, getImdfBundleMetadata } = await import('../../src/api/imdf');
    httpMock.get.mockResolvedValueOnce({ data: { bundle_task_id: 'bundle-22' } });

    const metadata = await getImdfBundleMetadata('bundle-22');
    const result = await deleteImdfBundle('bundle-22');

    expect(httpMock.get).toHaveBeenCalledWith('/imdf/bundles/bundle-22');
    expect(httpMock.delete).toHaveBeenCalledWith('/imdf/bundles/bundle-22');
    expect(metadata).toEqual({ bundle_task_id: 'bundle-22' });
    expect(result).toEqual({ deleted: true });
  });
});
