export function createToastMock(vi) {
  return {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  };
}

export function createAuthContextValue(vi, overrides = {}) {
  return {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
    logout: vi.fn(),
    ...overrides,
  };
}

export function createNavigateMock(vi) {
  return vi.fn();
}

export function createSearchParamsMock(vi, initial = '') {
  return [new URLSearchParams(initial), vi.fn()];
}
