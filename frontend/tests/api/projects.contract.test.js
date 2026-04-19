import { vi, describe, it, expect, beforeEach } from 'vitest';

const { httpMock, downloadBlobMock } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  downloadBlobMock: globalThis.__frontendTestHelpers.createDownloadBlobMock(vi),
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

vi.mock('../../src/utils/downloadBlob', () => ({
  downloadBlob: downloadBlobMock,
}));

describe('projects api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('createProject creates estrai projects and bootstraps an empty canvas when a connection is provided', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { metadata: { id: 'proj-1' } },
    });
    httpMock.put.mockResolvedValueOnce({
      data: { ok: true },
    });

    const { createProject } = await import('../../src/api/projects');
    const result = await createProject('Orders', 'nightly import', 'estrai', 'conn-1');

    expect(httpMock.post).toHaveBeenCalledWith('/projects', {
      name: 'Orders',
      description: 'nightly import',
      project_type: 'estrai',
      default_connection_id: 'conn-1',
    });
    expect(httpMock.put).toHaveBeenCalledWith('/projects/proj-1/canvas', { nodes: [], edges: [] });
    expect(result).toEqual({ metadata: { id: 'proj-1' } });
  });

  it('executeProjectQuery preserves the force_recalculate option payload', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { task_id: 'task-1', status: 'queued' },
    });

    const { executeProjectQuery } = await import('../../src/api/projects');
    const result = await executeProjectQuery('proj-9', { force_recalculate: true, ignored: 'x' });

    expect(httpMock.post).toHaveBeenCalledWith('/projects/proj-9/execute', {
      force_recalculate: true,
    });
    expect(result).toEqual({ task_id: 'task-1', status: 'queued' });
  });

  it('downloadProjectOutput requests a blob and forwards the fallback filename', async () => {
    httpMock.get.mockResolvedValueOnce({ data: new Blob(['csv']) });

    const { downloadProjectOutput, getDownloadUrl } = await import('../../src/api/projects');
    const result = await downloadProjectOutput('proj-42', 'export.csv');

    expect(getDownloadUrl('proj-42')).toBe('http://localhost:8000/api/projects/proj-42/download');
    expect(httpMock.get).toHaveBeenCalledWith('/projects/proj-42/download', {
      responseType: 'blob',
    });
    expect(downloadBlobMock).toHaveBeenCalledWith(
      expect.anything(),
      'export.csv',
      'output_proj-42',
    );
    expect(result).toEqual({ success: true });
  });
});
