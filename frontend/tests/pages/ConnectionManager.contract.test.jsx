import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import ConnectionManager from '../../src/pages/ConnectionManager';
import { requestOnOpen } from '../../src/utils/pendingRun';

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

// Tabella reale dei permessi, con le sole capability del test forzate.
vi.mock('../../src/utils/permissions', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    can: {
      ...actual.can,
      createConnection: () => true,
    },
    role: {
      ...actual.role,
      isSuperadmin: () => true,
    },
  };
});

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

  // Fase 5 T2 (R9.10): test per connessione, la fine del test di A non
  // riabilita B mentre il suo test gira.
  it('test paralleli: la fine di A non riabilita B', async () => {
    listConnectionsMock.mockResolvedValue({
      connections: [
        { id: 'a', name: 'DB A', type: 'postgres', host: 'h', port: 5432, database: 'a', can_edit: true },
        { id: 'b', name: 'DB B', type: 'postgres', host: 'h', port: 5432, database: 'b', can_edit: true },
      ],
    });
    let finishA;
    testConnectionMock.mockImplementation((id) => (id === 'a'
      ? new Promise((r) => { finishA = r; })
      : new Promise(() => {})));
    render(<ConnectionManager />);
    expect(await screen.findByText('DB A')).toBeInTheDocument();
    const [testA, testB] = screen.getAllByRole('button', { name: /test/i });
    await userEvent.click(testA);
    await userEvent.click(testB);
    expect(testConnectionMock).toHaveBeenCalledTimes(2);
    finishA({ success: true, message: 'ok' });
    await waitFor(() => expect(screen.getAllByRole('button', { name: /test/i })[0]).not.toBeDisabled());
    expect(screen.getAllByRole('button', { name: '⏳' })).toHaveLength(1);
  });

  // Fase 5 T2 (R4.8): un errore di caricamento e' un errore, non "nessuna connessione".
  it('errore di caricamento: messaggio con Riprova, non lo stato vuoto', async () => {
    listConnectionsMock.mockRejectedValueOnce({ response: { status: 500, data: {} } });
    listFeatureConnectionsMock.mockRejectedValueOnce({ response: { status: 500, data: {} } });
    render(<ConnectionManager />);
    expect(await screen.findByText('Connessioni non caricate')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }));
    expect(await screen.findByText('Warehouse DB')).toBeInTheDocument();
  });

  // «Modifica» dal pannello della home (10/11/2026): si apre la finestra di
  // modifica di QUESTA pagina per quella connessione, una volta sola.
  it('richiesta «Modifica» della home: apre la modifica della connessione, una volta', async () => {
    requestOnOpen('editConnection', 'db:conn-1');
    const { unmount } = render(<ConnectionManager />);
    expect(await screen.findByText('Modifica Connessione')).toBeInTheDocument();
    expect(screen.getByDisplayValue('db.local')).toBeInTheDocument();
    unmount();
    render(<ConnectionManager />);
    expect(await screen.findByText('Warehouse DB')).toBeInTheDocument();
    expect(screen.queryByText('Modifica Connessione')).toBeNull();
  });

  it('richiesta per una connessione ArcGIS con lo stesso id: non apre quella database', async () => {
    requestOnOpen('editConnection', 'arcgis:conn-1');
    render(<ConnectionManager />);
    expect(await screen.findByText('Warehouse DB')).toBeInTheDocument();
    expect(screen.queryByText('Modifica Connessione')).toBeNull();
    requestOnOpen('editConnection', null);
  });
});
