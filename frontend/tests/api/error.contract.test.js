import { beforeEach, describe, expect, it, vi } from 'vitest';

const { httpMock } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

describe('api error contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('projects api propagates backend failures from listProjects', async () => {
    const error = new Error('projects down');
    httpMock.get.mockRejectedValueOnce(error);
    const { listProjects } = await import('../../src/api/projects');
    await expect(listProjects()).rejects.toThrow('projects down');
  });

  it('files api propagates upload failures', async () => {
    const error = new Error('upload timeout');
    httpMock.post.mockRejectedValueOnce(error);
    const { uploadFile } = await import('../../src/api/files');
    await expect(uploadFile(new File(['a'], 'a.csv'), 'Orders')).rejects.toThrow('upload timeout');
  });

  it('schedules api propagates list failures', async () => {
    const error = new Error('scheduler offline');
    httpMock.get.mockRejectedValueOnce(error);
    const { listSchedules } = await import('../../src/api/schedules');
    await expect(listSchedules()).rejects.toThrow('scheduler offline');
  });

  it('tasks api propagates task status failures', async () => {
    const error = new Error('task lookup failed');
    httpMock.get.mockRejectedValueOnce(error);
    const { getTaskStatus } = await import('../../src/api/tasks');
    await expect(getTaskStatus('task-1')).rejects.toThrow('task lookup failed');
  });

  it('users api propagates profile failures', async () => {
    const error = new Error('auth me failed');
    httpMock.get.mockRejectedValueOnce(error);
    const { getMe } = await import('../../src/api/users');
    await expect(getMe()).rejects.toThrow('auth me failed');
  });

  it('features api propagates list failures', async () => {
    const error = new Error('feature service unavailable');
    httpMock.get.mockRejectedValueOnce(error);
    const { listFeatureConnections } = await import('../../src/api/features');
    await expect(listFeatureConnections()).rejects.toThrow('feature service unavailable');
  });

  it('enrichment api propagates execute failures', async () => {
    const error = new Error('enrichment failed');
    httpMock.post.mockRejectedValueOnce(error);
    const { executeEnrichmentAsync } = await import('../../src/api/enrichment');
    await expect(executeEnrichmentAsync('proj-1', { format: 'csv' })).rejects.toThrow('enrichment failed');
  });

  it('datasources api propagates list failures', async () => {
    const error = new Error('datasources unavailable');
    httpMock.get.mockRejectedValueOnce(error);
    const { listDataSources } = await import('../../src/api/datasources');
    await expect(listDataSources()).rejects.toThrow('datasources unavailable');
  });

});
