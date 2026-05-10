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

describe('files api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uploadFile serializes multipart fields including selected columns and srid', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'file-1' } });

    const { uploadFile } = await import('../../src/api/files');
    const file = new File(['id,name\n1,Rome'], 'cities.csv', { type: 'text/csv' });

    const result = await uploadFile(
      file,
      'Cities',
      'import csv',
      'layer-a',
      'sheet-1',
      ['id', 'name'],
      4326,
    );

    expect(httpMock.post).toHaveBeenCalledTimes(1);
    const [url, formData] = httpMock.post.mock.calls[0];
    expect(url).toBe('/files/upload');
    expect(formData).toBeInstanceOf(FormData);
    expect(formData.get('file')).toBe(file);
    expect(formData.get('name')).toBe('Cities');
    expect(formData.get('description')).toBe('import csv');
    expect(formData.get('layer')).toBe('layer-a');
    expect(formData.get('sheet')).toBe('sheet-1');
    expect(formData.get('selected_columns')).toBe(JSON.stringify(['id', 'name']));
    expect(formData.get('srid')).toBe('4326');
    expect(result).toEqual({ id: 'file-1' });
  });

  it('previewFileTemp omits optional layer and sheet when not provided', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { columns: ['id'] } });

    const { previewFileTemp } = await import('../../src/api/files');
    const file = new File(['id\n1'], 'preview.csv', { type: 'text/csv' });

    const result = await previewFileTemp(file);

    expect(httpMock.post).toHaveBeenCalledTimes(1);
    const [url, formData] = httpMock.post.mock.calls[0];
    expect(url).toBe('/files/preview-temp');
    expect(formData.get('file')).toBe(file);
    expect(formData.get('layer')).toBeNull();
    expect(formData.get('sheet')).toBeNull();
    expect(result).toEqual({ columns: ['id'] });
  });

  it('getFilePreview forwards the explicit preview limit as query params', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { rows: [] } });

    const { getFilePreview } = await import('../../src/api/files');
    const result = await getFilePreview('file-77', 250);

    expect(httpMock.get).toHaveBeenCalledWith('/files/file-77/preview', {
      params: { limit: 250 },
    });
    expect(result).toEqual({ rows: [] });
  });


  it('uploadFile rejects oversized files client-side without calling backend', async () => {
    const { uploadFile } = await import('../../src/api/files');
    const { MAX_UPLOAD_SIZE_BYTES, UploadTooLargeError } = await import(
      '../../src/api/uploadGuards'
    );

    // File "finto" con size oltre il cap: il File reale verrebbe creato a
    // partire da un Buffer di 100MB+, non vogliamo allocarlo.
    const oversized = new File(['stub'], 'big.csv', { type: 'text/csv' });
    Object.defineProperty(oversized, 'size', {
      value: MAX_UPLOAD_SIZE_BYTES + 1,
    });

    await expect(uploadFile(oversized, 'Big')).rejects.toBeInstanceOf(
      UploadTooLargeError,
    );
    // Il guard pre-flight deve aver impedito il roundtrip.
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('uploadFile propagates backend 413 as UploadTooLargeError with server detail', async () => {
    const { uploadFile } = await import('../../src/api/files');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    httpMock.post.mockRejectedValueOnce({
      response: {
        status: 413,
        data: { detail: 'File troppo grande (120MB). Limite massimo: 100MB.' },
      },
    });

    const file = new File(['ok'], 'ok.csv', { type: 'text/csv' });
    let caught;
    try {
      await uploadFile(file, 'Ok');
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(UploadTooLargeError);
    expect(caught.source).toBe('server');
    expect(caught.message).toContain('120MB');
    expect(httpMock.post).toHaveBeenCalledTimes(1);
  });

  it('uploadFile/uploadMultiLayer/previewFileTemp do NOT force manual Content-Type on FormData', async () => {
    httpMock.post.mockResolvedValue({ data: { ok: true } });

    const { uploadFile, uploadMultiLayer, previewFileTemp } = await import(
      '../../src/api/files'
    );
    const file = new File(['x'], 'x.csv', { type: 'text/csv' });

    await uploadFile(file, 'X');
    await uploadMultiLayer(file, 'X');
    await previewFileTemp(file);

    // Per ognuna delle 3 chiamate verifichiamo che il config (3o argomento
    // di axios.post) o sia undefined o non porti header Content-Type
    // forzato — axios deve aggiungere il boundary multipart da solo.
    const calls = httpMock.post.mock.calls;
    expect(calls.length).toBe(3);
    for (const [, body, config] of calls) {
      expect(body).toBeInstanceOf(FormData);
      if (config !== undefined) {
        const headers = config.headers || {};
        expect(headers['Content-Type']).toBeUndefined();
        expect(headers['content-type']).toBeUndefined();
      }
    }
  });

  it('uploadFile preserves non-413 backend errors unchanged', async () => {
    const { uploadFile } = await import('../../src/api/files');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    const backendErr = {
      response: { status: 400, data: { detail: 'bad request' } },
    };
    httpMock.post.mockRejectedValueOnce(backendErr);

    const file = new File(['x'], 'x.csv');
    let caught;
    try {
      await uploadFile(file, 'X');
    } catch (e) {
      caught = e;
    }
    expect(caught).toBe(backendErr);
    expect(caught instanceof UploadTooLargeError).toBe(false);
  });
});
