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
  it('UserManager handleDeleteGroup tollera un report incompleto', () => {
    // Fase 5 T1 (R10.3): il report arriva davvero (api/users.deleteGroup) e lo
    // riassume groupReportText, che salta le chiavi assenti e i report non oggetto.
    const src = readSrc('pages/UserManager.jsx');
    const idx = src.indexOf('const handleDeleteGroup =');
    expect(idx).toBeGreaterThan(-1);
    expect(src.slice(idx, idx + 400)).toContain('groupReportText(res?.report)');
    expect(src).toContain("if (!report || typeof report !== 'object') return ''");
    expect(src).toContain('.filter(({ key }) => key in report)');
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
    // UI-1 (C5), conferma A-K10: a pagine (limit + cursore), sempre su `/files`.
    expect(httpMock.get).toHaveBeenCalledWith('/files', expect.objectContaining({ params: expect.objectContaining({ limit: 200 }) }));
    // Anti-regression: niente trailing slash
    expect(httpMock.get).not.toHaveBeenCalledWith('/files/');
  });

});
