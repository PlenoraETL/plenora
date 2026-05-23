/**
 * Sessione 10 cluster C (JSDoc) + D (race conditions).
 *
 * Bug coperti:
 * - tasks.js createTask: carica_node_data required quando task_type='carica'
 * - carica.js postCaricaAutoMapping: JSDoc allineato a connection_id/schema_name/table_name
 * - ScheduleList.jsx: requestGenRef per scartare response stale su projectFilter change
 * - useProjectListData.js: Promise.allSettled + toast.warning partial-failure
 * - ManipolaEditor.jsx: triggerAutosave single-flight mutex
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


describe('Sessione 10 cluster CD', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('createTask fail-fast se task_type carica e manca carica_node_data', async () => {
    const { createTask } = await import('../../src/api/tasks');
    await expect(createTask({
      task_type: 'carica',
      project_id: 'p-1',
      canvas: { nodes: [], edges: [] },
    })).rejects.toThrow(/carica_node_data/i);
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('createTask procede con task_type carica + carica_node_data presente', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-1' } });
    const { createTask } = await import('../../src/api/tasks');
    const result = await createTask({
      task_type: 'carica',
      project_id: 'p-1',
      canvas: { nodes: [], edges: [] },
      carica_node_data: { connection_id: 'c-1', column_mappings: [] },
    });
    expect(result.task_id).toBe('task-1');
  });

  it('createTask non richiede carica_node_data per altri task_type', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-2' } });
    const { createTask } = await import('../../src/api/tasks');
    const result = await createTask({
      task_type: 'enrichment',
      project_id: 'p-1',
      canvas: { nodes: [], edges: [] },
    });
    expect(result.task_id).toBe('task-2');
  });

  it('carica.js postCaricaAutoMapping JSDoc allineato ai nomi BE', () => {
    const src = readSrc('api/carica.js');
    const idx = src.indexOf('postCaricaAutoMapping');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(Math.max(0, idx - 1500), idx);
    // Pre-fix nomi NON devono essere documentati come field names
    // (possono apparire nei commenti "Pre-fix:" ma non come @param)
    expect(window).toMatch(/payload\.connection_id/);
    expect(window).toMatch(/payload\.schema_name/);
    expect(window).toMatch(/payload\.table_name/);
    // Anti-regression: il vecchio JSDoc non deve apparire come param
    expect(window).not.toMatch(/payload\.target_conn_id/);
    expect(window).not.toMatch(/payload\.target_schema/);
    expect(window).not.toMatch(/payload\.target_table/);
  });

  it('ScheduleList usa requestGenRef per scartare response stale', () => {
    const src = readSrc('pages/ScheduleList.jsx');
    expect(src).toContain('requestGenRef');
    // Sia loadData che refreshData devono fare il check
    expect(src).toMatch(/myGen\s*!==\s*requestGenRef\.current/);
  });

  it('useProjectListData usa Promise.allSettled + toast.warning per partial-failure', () => {
    const src = readSrc('components/projects/useProjectListData.js');
    expect(src).toContain('Promise.allSettled');
    expect(src).toContain('toast.warning');
    expect(src).toContain('failedServices');
  });

  it('ManipolaEditor ha autosave mutex (triggerAutosave + inFlightRef)', () => {
    const src = readSrc('pages/ManipolaEditor.jsx');
    expect(src).toContain('autosaveInFlightRef');
    expect(src).toContain('triggerAutosave');
    // Coalesce: il pending si re-triggera al completamento
    expect(src).toContain('autosavePendingRef');
  });
});
