/**
 * Sessione 8 quick — 5 P0 residui pre-commit.
 *
 * Bug coperti:
 * 1. UserManager.handleDeleteGroup — optional chaining su r.users/.connections
 * 2. api/logs.js — JSDoc allineato a after/before (no piu' start_date/end_date)
 * 3. api/files.js getFiles — `/files` senza trailing slash
 * 4. api/tasks.js createTask — canvas required + fail-fast
 * 5. ManipolaEditor.executeCaricanTask — commento anti-regression deps
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

  // P0 #4a: createTask canvas required validation
  it('createTask solleva subito se manca canvas (no roundtrip 422)', async () => {
    const { createTask } = await import('../../src/api/tasks');
    await expect(createTask({ task_type: 'carica', project_id: 'p-1' }))
      .rejects.toThrow(/canvas.*required/i);
    // Il fail-fast NON deve aver fatto la chiamata HTTP
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  // P0 #4b: createTask passa attraverso se canvas e' presente
  it('createTask procede normalmente con canvas valido', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-1' } });
    const { createTask } = await import('../../src/api/tasks');
    const result = await createTask({
      task_type: 'carica',
      project_id: 'p-1',
      canvas: { nodes: [], edges: [] },
      // Required dal fail-fast P1 quando task_type === 'carica'.
      carica_node_data: { schema_name: 'plenora', table_name: 'foo' },
    });
    expect(result).toEqual({ task_id: 'task-1' });
    expect(httpMock.post).toHaveBeenCalledWith('/tasks', expect.objectContaining({
      canvas: { nodes: [], edges: [] },
    }));
  });

  // P0 #5: ManipolaEditor executeCaricanTask ha commento anti-regression deps
  it('ManipolaEditor executeCaricanTask ha commento di documentazione deps refs', () => {
    const src = readSrc('pages/ManipolaEditor.jsx');
    const idx = src.indexOf('const executeCaricanTask =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(Math.max(0, idx - 600), idx);
    // Il commento sopra deve menzionare i ref Zustand stabili + useExecution cleanup
    expect(window).toContain('nodesRef');
    expect(window).toContain('cleanup');
  });
});
