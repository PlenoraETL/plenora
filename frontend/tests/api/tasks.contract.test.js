import { vi, describe, it, expect, beforeEach } from 'vitest';

const { httpMock, downloadBlobMock } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  downloadBlobMock: globalThis.__frontendTestHelpers.createDownloadBlobMock(vi),
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

vi.mock('../../src/utils/downloadBlob', () => ({
  downloadBlob: downloadBlobMock,
}));

describe('tasks api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('createTask forwards the payload unchanged to the backend task endpoint', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { task_id: 'task-7' } });

    const { createTask } = await import('../../src/api/tasks');
    // Payload completo dei campi obbligatori del fail-fast client-side:
    // canvas (422 BE) e carica_node_data (400 BE quando task_type=carica).
    const payload = {
      task_type: 'carica',
      project_id: 'proj-1',
      canvas: { nodes: [], edges: [] },
      carica_node_data: { schema_name: 'plenora', table_name: 'foo' },
      options: {
        mode: 'upsert',
        target_connection_id: 'conn-9',
      },
    };

    const result = await createTask(payload);

    expect(httpMock.post).toHaveBeenCalledWith('/tasks', payload);
    expect(result).toEqual({ task_id: 'task-7' });
  });

  it('createTask fails fast when task_type carica lacks carica_node_data', async () => {
    const { createTask } = await import('../../src/api/tasks');
    await expect(createTask({
      task_type: 'carica',
      project_id: 'proj-1',
      canvas: { nodes: [], edges: [] },
    })).rejects.toThrow(/carica_node_data/);
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('downloadTaskOutput requests a blob and preserves the fallback filename', async () => {
    httpMock.get.mockResolvedValueOnce({ data: new Blob(['task-output']) });

    const { downloadTaskOutput } = await import('../../src/api/tasks');
    await downloadTaskOutput('task-22', 'result.csv');

    expect(httpMock.get).toHaveBeenCalledWith('/tasks/task-22/download', {
      responseType: 'blob',
    });
    expect(downloadBlobMock).toHaveBeenCalledWith(
      expect.anything(),
      'result.csv',
      'task_task-22_output',
    );
  });

  it('cleanupTaskCompletely stays best-effort when the backend cleanup fails', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    httpMock.delete.mockRejectedValueOnce(new Error('network down'));

    const { cleanupTaskCompletely } = await import('../../src/api/tasks');
    await cleanupTaskCompletely('task-99');

    expect(httpMock.delete).toHaveBeenCalledWith('/tasks/task-99/full-cleanup');
    expect(warnSpy).toHaveBeenCalledWith(
      '[cleanupTaskCompletely] errore cleanup:',
      'network down',
    );

    warnSpy.mockRestore();
  });
});
