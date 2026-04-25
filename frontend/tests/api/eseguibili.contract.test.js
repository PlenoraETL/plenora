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

describe('eseguibili api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('listEseguibili calls GET /eseguibili and returns response data', async () => {
    httpMock.get.mockResolvedValueOnce({ data: [{ id: 'e-1' }] });

    const { listEseguibili } = await import('../../src/api/eseguibili');
    const result = await listEseguibili();

    expect(httpMock.get).toHaveBeenCalledWith('/eseguibili');
    expect(result).toEqual([{ id: 'e-1' }]);
  });

  it('analyzeUpload posts FormData WITHOUT manual Content-Type so axios sets boundary', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { file_id: 'f-1', detected_columns: ['a', 'b'] },
    });

    const { analyzeUpload } = await import('../../src/api/eseguibili');
    const file = new File(['a,b\n1,2'], 'sample.csv', { type: 'text/csv' });

    const result = await analyzeUpload('eseg-77', file);

    expect(httpMock.post).toHaveBeenCalledTimes(1);
    const callArgs = httpMock.post.mock.calls[0];
    const [url, body, config] = callArgs;

    expect(url).toBe('/eseguibili/eseg-77/analyze-upload');
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('file')).toBe(file);

    // contract: il client NON deve forzare il Content-Type. Senza boundary
    // multipart, Starlette/python-multipart rifiuta il body. Lasciare a axios.
    if (config !== undefined) {
      const headers = config.headers || {};
      expect(headers['Content-Type']).toBeUndefined();
      expect(headers['content-type']).toBeUndefined();
    }

    expect(result).toEqual({ file_id: 'f-1', detected_columns: ['a', 'b'] });
  });

  it('executeEseguibile posts JSON payload to /eseguibili/{id}/execute', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { async: true, task_id: 't-1', project_id: 'p-1' },
    });

    const { executeEseguibile } = await import('../../src/api/eseguibili');
    const payload = {
      file_id: 'f-1',
      column_mapping: [{ chain_column: 'addr', file_column: 'a' }],
      output_format: 'xlsx',
    };
    const result = await executeEseguibile('eseg-77', payload);

    expect(httpMock.post).toHaveBeenCalledWith(
      '/eseguibili/eseg-77/execute',
      payload,
    );
    expect(result).toEqual({ async: true, task_id: 't-1', project_id: 'p-1' });
  });

  it('createEseguibile and deleteEseguibile call expected endpoints', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'e-9' } });
    httpMock.delete.mockResolvedValueOnce({ data: { ok: true } });

    const { createEseguibile, deleteEseguibile } = await import(
      '../../src/api/eseguibili'
    );

    await createEseguibile({ source_project_id: 'p-1', name: 'New' });
    expect(httpMock.post).toHaveBeenCalledWith('/eseguibili', {
      source_project_id: 'p-1',
      name: 'New',
    });

    await deleteEseguibile('e-9');
    expect(httpMock.delete).toHaveBeenCalledWith('/eseguibili/e-9');
  });

  // ─── B3 Phase 4: client-side size guard + 413 propagation ────────────────

  it('analyzeUpload rejects oversized files client-side without calling backend', async () => {
    const { analyzeUpload } = await import('../../src/api/eseguibili');
    const { MAX_UPLOAD_SIZE_BYTES, UploadTooLargeError } = await import(
      '../../src/api/uploadGuards'
    );

    const oversized = new File(['stub'], 'big.csv', { type: 'text/csv' });
    Object.defineProperty(oversized, 'size', {
      value: MAX_UPLOAD_SIZE_BYTES + 1,
    });

    await expect(analyzeUpload('eseg-1', oversized)).rejects.toBeInstanceOf(
      UploadTooLargeError,
    );
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('analyzeUpload propagates backend 413 as UploadTooLargeError', async () => {
    const { analyzeUpload } = await import('../../src/api/eseguibili');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    httpMock.post.mockRejectedValueOnce({
      response: {
        status: 413,
        data: { detail: 'File troppo grande (110MB). Limite massimo: 100MB.' },
      },
    });

    const file = new File(['ok'], 'ok.csv', { type: 'text/csv' });
    let caught;
    try {
      await analyzeUpload('eseg-1', file);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(UploadTooLargeError);
    expect(caught.source).toBe('server');
    expect(caught.message).toContain('110MB');
  });
});
