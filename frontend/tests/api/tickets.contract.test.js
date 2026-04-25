import { vi, describe, it, expect, beforeEach } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

/**
 * Contract test — tickets api (B3 Phase 4).
 *
 * Coverage focalizzata su uploadAttachment dopo il fix B3:
 *   - oversize bloccato client-side (UploadTooLargeError, no roundtrip)
 *   - 413 backend normalizzato come UploadTooLargeError(source='server')
 *   - FormData inviato SENZA Content-Type manuale (regression dal bug
 *     pre-B3 che forzava 'multipart/form-data' senza boundary, rompendo
 *     il parsing Starlette)
 */
describe('tickets api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uploadAttachment rejects files over ticket-specific 10MB cap client-side', async () => {
    const { uploadAttachment } = await import('../../src/api/tickets');
    const { TICKET_ATTACHMENT_MAX_SIZE_BYTES, UploadTooLargeError } =
      await import('../../src/api/uploadGuards');

    // Sanity: il cap ticket deve essere 10MB, allineato a
    // backend/app/core/tickets/router.py:TICKET_ATTACHMENT_MAX_BYTES.
    expect(TICKET_ATTACHMENT_MAX_SIZE_BYTES).toBe(10 * 1024 * 1024);

    const oversized = new File(['stub'], 'big.log', { type: 'text/plain' });
    Object.defineProperty(oversized, 'size', {
      value: TICKET_ATTACHMENT_MAX_SIZE_BYTES + 1,
    });

    await expect(uploadAttachment('t-1', oversized)).rejects.toBeInstanceOf(
      UploadTooLargeError,
    );
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('uploadAttachment rejects 20MB files client-side (regression: 100MB default cap is wrong for tickets)', async () => {
    const { uploadAttachment } = await import('../../src/api/tickets');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    // Caso reale del bug: file 20MB -> sotto cap generale (100MB) ma sopra
    // cap ticket (10MB). Pre-fix il client lo accettava e poi prendeva 413
    // dal backend; post-fix viene rifiutato pre-flight.
    const file = new File(['stub'], 'big.log', { type: 'text/plain' });
    Object.defineProperty(file, 'size', { value: 20 * 1024 * 1024 });

    await expect(uploadAttachment('t-1', file)).rejects.toBeInstanceOf(
      UploadTooLargeError,
    );
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('uploadAttachment accepts files under the 10MB cap', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'a-ok' } });

    const { uploadAttachment } = await import('../../src/api/tickets');
    const file = new File(['x'], 'small.txt', { type: 'text/plain' });
    // file.size sara' 1 (default JSDOM): ben sotto i 10MB.

    const result = await uploadAttachment('t-1', file);
    expect(result).toEqual({ id: 'a-ok' });
    expect(httpMock.post).toHaveBeenCalledTimes(1);
  });

  it('uploadAttachment posts FormData WITHOUT manual Content-Type so axios sets boundary', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { id: 'a-1' } });

    const { uploadAttachment } = await import('../../src/api/tickets');
    const file = new File(['hello'], 'note.txt', { type: 'text/plain' });

    const result = await uploadAttachment('t-42', file);

    expect(httpMock.post).toHaveBeenCalledTimes(1);
    const [url, body, config] = httpMock.post.mock.calls[0];
    expect(url).toBe('/tickets/t-42/attachments');
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('file')).toBe(file);

    // Contract: niente Content-Type manuale. Pre-B3 il client passava
    // headers: { 'Content-Type': 'multipart/form-data' } senza boundary,
    // bloccando python-multipart server-side.
    if (config !== undefined) {
      const headers = config.headers || {};
      expect(headers['Content-Type']).toBeUndefined();
      expect(headers['content-type']).toBeUndefined();
    }

    expect(result).toEqual({ id: 'a-1' });
  });

  it('uploadAttachment propagates backend 413 as UploadTooLargeError', async () => {
    const { uploadAttachment } = await import('../../src/api/tickets');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    httpMock.post.mockRejectedValueOnce({
      response: {
        status: 413,
        data: { detail: 'File troppo grande (200MB). Limite massimo: 100MB.' },
      },
    });

    const file = new File(['x'], 'x.txt', { type: 'text/plain' });
    let caught;
    try {
      await uploadAttachment('t-1', file);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(UploadTooLargeError);
    expect(caught.source).toBe('server');
    expect(caught.message).toContain('200MB');
  });

  it('uploadAttachment preserves non-413 backend errors unchanged', async () => {
    const { uploadAttachment } = await import('../../src/api/tickets');
    const { UploadTooLargeError } = await import('../../src/api/uploadGuards');

    const backendErr = {
      response: { status: 403, data: { detail: 'forbidden' } },
    };
    httpMock.post.mockRejectedValueOnce(backendErr);

    const file = new File(['x'], 'x.txt', { type: 'text/plain' });
    let caught;
    try {
      await uploadAttachment('t-1', file);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBe(backendErr);
    expect(caught instanceof UploadTooLargeError).toBe(false);
  });
});
