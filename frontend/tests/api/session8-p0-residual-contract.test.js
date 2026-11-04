/**
 * Sessione 8 quick — 5 P0 residui pre-commit.
 *
 * Bug coperti:
 * 1. UserManager.handleDeleteGroup — optional chaining su r.users/.connections
 * 2. api/logs.js — JSDoc allineato a after/before (no piu' start_date/end_date)
 * 3. api/files.js getFiles — `/files` senza trailing slash
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


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


function readSrc(relPath) {
  return readFileSync(resolve(__dirname, '../../src', relPath), 'utf-8');
}


describe('Sessione 8 P0 residui', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // P0 #1: UserManager handleDeleteGroup optional chaining
  it('UserManager handleDeleteGroup usa optional chaining su report fields', () => {
    const src = readSrc('pages/UserManager.jsx');
    const idx = src.indexOf('const handleDeleteGroup =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 700);
    // r?.projects / r?.users / r?.connections con fallback ?? '?'
    expect(window).toMatch(/r\?\.projects/);
    expect(window).toMatch(/r\?\.users/);
    expect(window).toMatch(/r\?\.connections/);
  });

  // P0 #2: logs.js JSDoc allineato
  it('logs.js JSDoc documenta after/before non start_date/end_date', () => {
    const src = readSrc('api/logs.js');
    expect(src).toContain('params.after');
    expect(src).toContain('params.before');
    // Anti-regression: il JSDoc non deve menzionare i vecchi nomi
    expect(src).not.toContain('params.start_date');
    expect(src).not.toContain('params.end_date');
  });

  // P0 #3: files.js getFiles senza trailing slash
  it('files.js getFiles chiama /files senza trailing slash', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { files: [] } });
    const { getFiles } = await import('../../src/api/files');
    await getFiles();
    expect(httpMock.get).toHaveBeenCalledWith('/files');
    // Anti-regression: niente trailing slash
    expect(httpMock.get).not.toHaveBeenCalledWith('/files/');
  });

});
