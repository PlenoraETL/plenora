import { describe, it, expect } from 'vitest';

import {
  MAX_UPLOAD_SIZE_BYTES,
  UploadTooLargeError,
  assertFileSize,
  extract413Message,
  throwIfUploadTooLarge,
} from '../../src/api/uploadGuards';

/**
 * Contract tests for uploadGuards.
 *
 * Coverage:
 *   1. file troppo grande viene rifiutato client-side (assertFileSize)
 *   2. 413 backend resta leggibile/propagato (extract413Message,
 *      throwIfUploadTooLarge)
 *   3. niente forzatura di Content-Type su FormData
 *      (vedi tests/api/{files,eseguibili,tickets}.contract.test.js)
 */
describe('uploadGuards contract', () => {
  describe('assertFileSize', () => {
    it('non rilancia per file sotto soglia', () => {
      const file = { size: 1024 };
      expect(() => assertFileSize(file)).not.toThrow();
    });

    it('rilancia UploadTooLargeError per file > MAX_UPLOAD_SIZE_BYTES', () => {
      const file = { size: MAX_UPLOAD_SIZE_BYTES + 1 };
      let caught;
      try {
        assertFileSize(file);
      } catch (e) {
        caught = e;
      }
      expect(caught).toBeInstanceOf(UploadTooLargeError);
      expect(caught.source).toBe('client');
      expect(caught.fileSize).toBe(MAX_UPLOAD_SIZE_BYTES + 1);
      expect(caught.maxSize).toBe(MAX_UPLOAD_SIZE_BYTES);
      expect(caught.message).toMatch(/troppo grande/i);
      expect(caught.message).toMatch(/limite massimo/i);
    });

    it('rispetta override maxBytes', () => {
      const file = { size: 2048 };
      expect(() => assertFileSize(file, { maxBytes: 4096 })).not.toThrow();
      expect(() => assertFileSize(file, { maxBytes: 1024 })).toThrow(
        UploadTooLargeError,
      );
    });

    it('non rilancia su input non-File (size undefined): backend resta fonte di verita', () => {
      // Mock con size mancante non deve bloccare il flow.
      expect(() => assertFileSize({})).not.toThrow();
      expect(() => assertFileSize(null)).not.toThrow();
      expect(() => assertFileSize(undefined)).not.toThrow();
    });
  });

  describe('extract413Message', () => {
    it('ritorna detail testuale dal body FastAPI 413', () => {
      const error = {
        response: {
          status: 413,
          data: { detail: 'File troppo grande (200MB). Limite massimo: 100MB.' },
        },
      };
      expect(extract413Message(error)).toBe(
        'File troppo grande (200MB). Limite massimo: 100MB.',
      );
    });

    it('accetta body string-only (rare proxy) come fallback', () => {
      const error = { response: { status: 413, data: 'Payload Too Large' } };
      expect(extract413Message(error)).toBe('Payload Too Large');
    });

    it('ritorna null per status diversi da 413', () => {
      expect(extract413Message({ response: { status: 400, data: { detail: 'x' } } }))
        .toBeNull();
      expect(extract413Message({ response: { status: 500 } })).toBeNull();
      expect(extract413Message(null)).toBeNull();
      expect(extract413Message(new Error('Network Error'))).toBeNull();
    });

    it('ritorna fallback testuale se 413 senza detail/message strutturato', () => {
      const error = { response: { status: 413, data: {} } };
      expect(extract413Message(error)).toMatch(/troppo grande/i);
    });
  });

  describe('throwIfUploadTooLarge', () => {
    it('lancia UploadTooLargeError con source="server" su 413', () => {
      const error = {
        response: {
          status: 413,
          data: { detail: 'File troppo grande (150MB). Limite massimo: 100MB.' },
        },
      };
      let caught;
      try {
        throwIfUploadTooLarge(error);
      } catch (e) {
        caught = e;
      }
      expect(caught).toBeInstanceOf(UploadTooLargeError);
      expect(caught.source).toBe('server');
      expect(caught.message).toContain('150MB');
      expect(caught.maxSize).toBe(MAX_UPLOAD_SIZE_BYTES);
    });

    it('NON lancia (no-op) per errori non-413: il caller fara throw err originale', () => {
      const error = { response: { status: 500, data: { detail: 'boom' } } };
      expect(() => throwIfUploadTooLarge(error)).not.toThrow();
      expect(() => throwIfUploadTooLarge(new Error('Network'))).not.toThrow();
      expect(() => throwIfUploadTooLarge(null)).not.toThrow();
    });
  });
});
