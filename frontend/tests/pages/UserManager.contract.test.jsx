import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import UserManager from '../../src/pages/UserManager';

const {
  navigateMock,
  toastMock,
  listUsersMock,
  createUserMock,
  listGroupsMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  listUsersMock: vi.fn(),
  createUserMock: vi.fn(),
  listGroupsMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'admin-1', role: 'superadmin', group_id: 'grp-1', username: 'root' },
  }),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/api/users', () => ({
  listUsers: listUsersMock,
  createUser: createUserMock,
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  resetPassword: vi.fn(),
  listGroups: listGroupsMock,
  createGroup: vi.fn(),
  deleteGroup: vi.fn(),
  getGroupSummary: vi.fn(),
}));

vi.mock('../../src/api/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/components/ChangePasswordModal', () => ({
  default: () => null,
}));

vi.mock('../../src/components/AppSidebar', () => ({
  default: () => <div data-testid="app-sidebar" />,
}));

vi.mock('../../src/utils/permissions', () => ({
  can: {
    manageUsers: () => true,
  },
  role: {
    isSuperadmin: (user) => user?.role === 'superadmin',
    isAdminOrAbove: (user) => ['admin', 'superadmin'].includes(user?.role),
  },
}));

describe('UserManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listUsersMock.mockResolvedValue([
      { id: 'user-1', username: 'mario', role: 'user', group_id: 'grp-1', is_active: true },
    ]);
    listGroupsMock.mockResolvedValue([
      { group_id: 'grp-1', name: 'Group One' },
      { group_id: 'grp-2', name: 'Group Two' },
    ]);
    createUserMock.mockResolvedValue({ id: 'user-2' });
  });

  it('creates a new user with the modal payload expected by the backend', async () => {
    render(<UserManager />);

    expect(await screen.findByText('mario')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /\+ nuovo utente/i }));
    await userEvent.type(screen.getByPlaceholderText(/mario\.rossi/i), 'laura');
    await userEvent.type(screen.getByPlaceholderText(/password sicura/i), 'StrongPwd1');
    await userEvent.selectOptions(screen.getAllByRole('combobox')[0], 'admin');
    await userEvent.selectOptions(screen.getAllByRole('combobox')[1], 'grp-2');
    await userEvent.click(screen.getByRole('button', { name: /crea utente/i }));

    await waitFor(() => {
      expect(createUserMock).toHaveBeenCalledWith({
        username: 'laura',
        password: 'StrongPwd1',
        role: 'admin',
        group_id: 'grp-2',
        is_active: true,
      });
    });
  });
});
