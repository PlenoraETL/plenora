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

    expect(httpMock.get).toHaveBeenCalledWith('/eseguibili', {
      params: { limit: 200, offset: 0 },
    });
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

  it('analyzeUpload scopes a file to its manifest input', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { file_id: 'f-2' } });
    const { analyzeUpload } = await import('../../src/api/eseguibili');
    const file = new File(['id\n1'], 'customers.csv', { type: 'text/csv' });

    await analyzeUpload('eseg-77', file, 'customers_file');

    expect(httpMock.post).toHaveBeenCalledWith(
      '/eseguibili/eseg-77/analyze-upload',
      expect.any(FormData),
      { params: { input_key: 'customers_file' } },
    );
  });

  it('runEseguibile posts the versioned multi-input contract', async () => {
    httpMock.post.mockResolvedValueOnce({
      data: { async: true, task_id: 't-2', run_id: 'run-1' },
    });
    const { runEseguibile } = await import('../../src/api/eseguibili');
    const payload = {
      version_id: 'v-2',
      inputs: {
        customers_file: { file_id: 'f-2', column_mapping: [] },
        country_code: 'IT',
      },
      output_format: 'xlsx',
    };

    const result = await runEseguibile('eseg-77', payload);

    expect(httpMock.post).toHaveBeenCalledWith(
      '/eseguibili/eseg-77/runs',
      payload,
    );
    expect(result.run_id).toBe('run-1');
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

  it('updates executable metadata through PUT without publishing a version', async () => {
    httpMock.put.mockResolvedValueOnce({ data: { id: 'e-9', name: 'Nuovo nome' } });
    const { updateEseguibile } = await import('../../src/api/eseguibili');

    await expect(
      updateEseguibile('e-9', { name: 'Nuovo nome' }),
    ).resolves.toEqual({ id: 'e-9', name: 'Nuovo nome' });
    expect(httpMock.put).toHaveBeenCalledWith('/eseguibili/e-9', {
      name: 'Nuovo nome',
    });
  });

  it('exposes list, republish and activate version endpoints', async () => {
    httpMock.get.mockResolvedValueOnce({ data: [{ id: 'v-2' }] });
    httpMock.post
      .mockResolvedValueOnce({ data: { active_version: 3 } })
      .mockResolvedValueOnce({ data: { active_version: 2 } });
    const {
      listEseguibileVersions,
      republishEseguibile,
      activateEseguibileVersion,
    } = await import('../../src/api/eseguibili');

    await expect(listEseguibileVersions('exec-1')).resolves.toEqual([{ id: 'v-2' }]);
    await republishEseguibile('exec-1');
    await activateEseguibileVersion('exec-1', 'v-2');

    expect(httpMock.get).toHaveBeenCalledWith('/eseguibili/exec-1/versions');
    expect(httpMock.post).toHaveBeenNthCalledWith(
      1,
      '/eseguibili/exec-1/versions',
    );
    expect(httpMock.post).toHaveBeenNthCalledWith(
      2,
      '/eseguibili/exec-1/versions/v-2/activate',
    );
  });


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
