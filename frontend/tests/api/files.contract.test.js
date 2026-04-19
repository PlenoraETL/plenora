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
});
