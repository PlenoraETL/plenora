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

  // ─── C3.4 Phase 4: per-file download (boundary helper for ExecutionModal) ──

  it('downloadProjectFile GETs /projects/:id/download?file=... with responseType blob', async () => {
    const blob = new Blob(['error data']);
    httpMock.get.mockResolvedValueOnce({ data: blob, status: 200 });

    const { downloadProjectFile } = await import('../../src/api/projects');
    const r = await downloadProjectFile('proj-77', 'error_report_xyz.csv');

    expect(httpMock.get).toHaveBeenCalledWith(
      '/projects/proj-77/download?file=error_report_xyz.csv',
      { responseType: 'blob' },
    );
    expect(r.data).toBe(blob);
  });

  it('downloadProjectFile preserves the raw query string (no axios params encoding)', async () => {
    httpMock.get.mockResolvedValueOnce({ data: new Blob(), status: 200 });

    const { downloadProjectFile } = await import('../../src/api/projects');
    await downloadProjectFile('proj-1', 'my file.csv');

    // Contratto deliberato: la query string viene concatenata letterale
    // (preserva URL semantics pre-C3.4 dove ExecutionModal costruiva
    // ${API_ENDPOINT}/projects/.../download?file=${filename}). Niente
    // `params: { file }` che invece encoderebbe lo spazio.
    const [url, config] = httpMock.get.mock.calls[0];
    expect(url).toBe('/projects/proj-1/download?file=my file.csv');
    expect(config.params).toBeUndefined();
  });

  it('downloadProjectFile returns the full AxiosResponse (not just data)', async () => {
    const blob = new Blob(['x']);
    const axiosResp = {
      data: blob,
      status: 200,
      headers: { 'content-type': 'text/csv' },
    };
    httpMock.get.mockResolvedValueOnce(axiosResp);

    const { downloadProjectFile } = await import('../../src/api/projects');
    const r = await downloadProjectFile('p-1', 'r.csv');

    // Il caller (ExecutionModal) puo' accedere a headers per leggere
    // Content-Disposition se in futuro vuole rispettare il filename
    // server-side. Oggi forza 'error_report.csv', ma il contratto resta.
    expect(r).toBe(axiosResp);
    expect(r.headers['content-type']).toBe('text/csv');
  });
});
