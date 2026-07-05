import { beforeEach, describe, expect, it, vi } from 'vitest';


const { httpMock, downloadBlobMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
  downloadBlobMock: vi.fn(),
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

vi.mock('../../src/utils/downloadBlob', () => ({
  downloadBlob: downloadBlobMock,
}));

describe('api/schedules.js', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('builds schedule download url with and without run parameter', async () => {
    const { getDownloadUrl } = await import('../../src/api/schedules');

    expect(getDownloadUrl('sch-1')).toBe('http://localhost:5004/api/schedules/sch-1/download');
    expect(getDownloadUrl('sch-1', '2026-04-15T10:00:00')).toBe(
      'http://localhost:5004/api/schedules/sch-1/download?run=2026-04-15T10:00:00',
    );
  });

  it('passes page and status filters to getScheduleLogs', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { items: [] } });
    const { getScheduleLogs } = await import('../../src/api/schedules');

    await getScheduleLogs('sch-logs', 2, 50, 'FAILED');

    expect(httpMock.get).toHaveBeenCalledWith('/schedules/sch-logs/logs', {
      params: {
        page: 2,
        page_size: 50,
        status: 'FAILED',
      },
    });
  });

  it('downloads schedule output as blob with authenticated request params', async () => {
    const response = {
      data: new Uint8Array([1, 2, 3]),
      headers: {
        'content-type': 'text/csv',
        'content-disposition': 'attachment; filename="output.csv"',
      },
    };
    httpMock.get.mockResolvedValueOnce(response);
    const { downloadScheduleOutput } = await import('../../src/api/schedules');

    await downloadScheduleOutput('sch-2', 'run-123', 'manual.csv');

    expect(httpMock.get).toHaveBeenCalledWith('/schedules/sch-2/download', {
      params: { run: 'run-123' },
      responseType: 'blob',
    });
    expect(downloadBlobMock).toHaveBeenCalledWith(
      response,
      'manual.csv',
      'schedule_sch-2_output',
    );
  });
});
