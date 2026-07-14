import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ConnectionManager from '../../src/pages/ConnectionManager';

const {
  navigateMock,
  toastMock,
  listConnectionsMock,
  createConnectionMock,
  testConnectionMock,
  listFeatureConnectionsMock,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  listConnectionsMock: vi.fn(),
  createConnectionMock: vi.fn(),
  testConnectionMock: vi.fn(),
  listFeatureConnectionsMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { role: 'superadmin', username: 'tester' },
    logout: vi.fn(),
  }),
}));

vi.mock('../../src/api', () => ({
  listConnections: listConnectionsMock,
  createConnection: createConnectionMock,
  updateConnection: vi.fn(),
  deleteConnection: vi.fn(),
  testConnection: testConnectionMock,
}));

vi.mock('../../src/api/features', () => ({
  listFeatureConnections: listFeatureConnectionsMock,
  createFeatureConnection: vi.fn(),
  updateFeatureConnection: vi.fn(),
  deleteFeatureConnection: vi.fn(),
  testFeatureConnection: vi.fn(),
}));

vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));

vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => <div data-testid="allowed-groups-selector" />,
}));

vi.mock('../../src/components/AppSidebar', () => ({
  default: () => <div data-testid="app-sidebar" />,
}));

vi.mock('../../src/utils/permissions', () => ({
  can: {
    createConnection: () => true,
  },
  role: {
    isSuperadmin: () => true,
  },
}));

describe('ConnectionManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listConnectionsMock.mockResolvedValue({
      connections: [
        {
          id: 'conn-1',
          name: 'Warehouse DB',
          type: 'postgres',
          host: 'db.local',
          port: 5432,
          database: 'warehouse',
          db_schema: 'public',
          can_edit: true,
        },
      ],
    });
    listFeatureConnectionsMock.mockResolvedValue({ connections: [] });
    createConnectionMock.mockResolvedValue({ id: 'conn-2' });
    testConnectionMock.mockResolvedValue({ success: true, message: 'ok' });
  });

  it('creates a database connection with the backend payload expected by the form', async () => {
    render(<ConnectionManager />);

    expect(await screen.findByText('Warehouse DB')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /nuova connessione/i }));
    await userEvent.clear(screen.getByPlaceholderText(/archibus icsc/i));
    await userEvent.type(screen.getByPlaceholderText(/archibus icsc/i), 'Analytics DB');
    await userEvent.clear(screen.getByPlaceholderText(/localhost/i));
    await userEvent.type(screen.getByPlaceholderText(/localhost/i), 'analytics.local');
    await userEvent.clear(screen.getByPlaceholderText(/nome_database/i));
    await userEvent.type(screen.getByPlaceholderText(/nome_database/i), 'analytics');
    await userEvent.click(screen.getByRole('button', { name: /crea connessione/i }));

    await waitFor(() => {
      expect(createConnectionMock).toHaveBeenCalledWith({
        name: 'Analytics DB',
        description: '',
        type: 'postgres',
        host: 'analytics.local',
        port: 5432,
        database: 'analytics',
        db_schema: 'public',
        username: '',
        password: '',
        mssql_tls_mode: 'verify',
      });
    });
  });
});
