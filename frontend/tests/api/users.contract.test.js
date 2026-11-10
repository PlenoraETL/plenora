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

describe('users api contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('changeMyPassword uses the backend field names expected by auth', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { success: true } });

    const { changeMyPassword } = await import('../../src/api/users');
    const result = await changeMyPassword('old-secret', 'new-secret');

    expect(httpMock.post).toHaveBeenCalledWith('/auth/change-password', {
      old_password: 'old-secret',
      new_password: 'new-secret',
    });
    expect(result).toEqual({ success: true });
  });

  it('resetPassword posts the explicit reset payload to the user endpoint', async () => {
    httpMock.post.mockResolvedValueOnce({ data: { ok: true } });

    const { resetPassword } = await import('../../src/api/users');
    const result = await resetPassword('user-7', { new_password: 'Temp1234' });

    expect(httpMock.post).toHaveBeenCalledWith('/auth/users/user-7/reset-password', {
      new_password: 'Temp1234',
    });
    expect(result).toEqual({ ok: true });
  });

  it('deleteUserKeepingHistory posts to the dedicated endpoint (204 vuoto)', async () => {
    httpMock.post.mockResolvedValueOnce({ data: '' });

    const { deleteUserKeepingHistory } = await import('../../src/api/users');
    const result = await deleteUserKeepingHistory('u-e0071');

    expect(httpMock.post).toHaveBeenCalledWith('/auth/users/u-e0071/delete-keeping-history');
    expect(result).toEqual({ success: true, id: 'u-e0071' });
  });

  it('getGroupSummary hits the dedicated summary endpoint', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { users: 3, projects: 2 } });

    const { getGroupSummary } = await import('../../src/api/users');
    const result = await getGroupSummary('grp-1');

    expect(httpMock.get).toHaveBeenCalledWith('/auth/groups/grp-1/summary');
    expect(result).toEqual({ users: 3, projects: 2 });
  });
});
