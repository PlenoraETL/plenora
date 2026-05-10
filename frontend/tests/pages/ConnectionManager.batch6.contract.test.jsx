import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ConnectionManager from '../../src/pages/ConnectionManager';


const {
  navigateMock,
  toastMock,
  listConnectionsMock,
  createConnectionMock,
  updateConnectionMock,
  deleteConnectionMock,
  testConnectionMock,
  listFeatureConnectionsMock,
  createFeatureConnectionMock,
  authState,
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
  updateConnectionMock: vi.fn(),
  deleteConnectionMock: vi.fn(),
  testConnectionMock: vi.fn(),
  listFeatureConnectionsMock: vi.fn(),
  createFeatureConnectionMock: vi.fn(),
  authState: {
    user: { role: 'superadmin', username: 'sa' },
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));

vi.mock('../../src/api', () => ({
  listConnections: listConnectionsMock,
  createConnection: createConnectionMock,
  updateConnection: updateConnectionMock,
  deleteConnection: deleteConnectionMock,
  testConnection: testConnectionMock,
}));

vi.mock('../../src/api/features', () => ({
  listFeatureConnections: listFeatureConnectionsMock,
  createFeatureConnection: createFeatureConnectionMock,
  updateFeatureConnection: vi.fn(),
  deleteFeatureConnection: vi.fn(),
  testFeatureConnection: vi.fn(),
}));

vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => null,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));

// Manteniamo la policy reale: superadmin -> can.createConnection true,
// canEdit dipende da `can_edit` sulla riga. I test si basano sulla
// configurazione superadmin di default.

const sampleConn = {
  id: 'conn-1',
  name: 'Warehouse DB',
  description: 'prod warehouse',
  type: 'postgres',
  host: 'db.local',
  port: 5432,
  database: 'warehouse',
  db_schema: 'public',
  username: 'svc',
  can_edit: true,
};

describe('ConnectionManager contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'superadmin', username: 'sa' };
    listConnectionsMock.mockResolvedValue({ connections: [sampleConn] });
    listFeatureConnectionsMock.mockResolvedValue({ connections: [] });
  });
  it('deletes a database connection after confirm and reloads the list', async () => {
    deleteConnectionMock.mockResolvedValueOnce({ ok: true });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(screen.getByRole('button', { name: /Elimina connessione/i }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() =>
      expect(deleteConnectionMock).toHaveBeenCalledWith('conn-1'),
    );
    expect(listConnectionsMock).toHaveBeenCalledTimes(2); // initial + reload
    expect(toastMock.error).not.toHaveBeenCalled();
  });
  it('shows formatted cascade dependencies on 409 delete and skips reload', async () => {
    deleteConnectionMock.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: 'cascade_in_use',
            message: 'in use',
            dependencies: [
              { kind: 'project', id: 'p1', name: 'Demo Pipeline' },
            ],
          },
        },
      },
    });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(screen.getByRole('button', { name: /Elimina connessione/i }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Warehouse DB');
    expect(msg).toContain('Demo Pipeline');
    expect(msg).toMatch(/progetto/i);
    expect(ttl).toBe(10000);
    // Reload non triggered dopo cascade 409.
    expect(listConnectionsMock).toHaveBeenCalledTimes(1);
  });
  it('shows the OK status badge after a successful test connection', async () => {
    testConnectionMock.mockResolvedValueOnce({ success: true, message: 'ok' });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');
    expect(screen.getByText('Non testata')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /🔌 Test/i }));

    await waitFor(() =>
      expect(testConnectionMock).toHaveBeenCalledWith('conn-1'),
    );
    expect(await screen.findByText('OK')).toBeInTheDocument();
    expect(screen.queryByText('Non testata')).not.toBeInTheDocument();
  });
  it('shows the error badge and inline error banner when test fails', async () => {
    testConnectionMock.mockResolvedValueOnce({
      success: false,
      message: 'Connection refused: 5432',
    });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(screen.getByRole('button', { name: /🔌 Test/i }));

    await waitFor(() =>
      expect(testConnectionMock).toHaveBeenCalledWith('conn-1'),
    );
    expect(await screen.findByText('Errore')).toBeInTheDocument();
    expect(
      await screen.findByText(/Connection refused: 5432/),
    ).toBeInTheDocument();
  });
  it('blocks createConnection when the name is empty and warns the user', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /Crea Connessione/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith(
      'Il nome è obbligatorio',
    );
    expect(createConnectionMock).not.toHaveBeenCalled();
  });
  it('opens the edit modal prefilled and updates the connection on save', async () => {
    updateConnectionMock.mockResolvedValueOnce({ id: 'conn-1' });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /✏️ Modifica/i }),
    );
    expect(
      await screen.findByText('Modifica Connessione'),
    ).toBeInTheDocument();
    const nameInput = screen.getByDisplayValue('Warehouse DB');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Warehouse DB v2');

    await userEvent.click(
      screen.getByRole('button', { name: /Salva Modifiche/i }),
    );

    await waitFor(() =>
      expect(updateConnectionMock).toHaveBeenCalledTimes(1),
    );
    const [calledId, payload] = updateConnectionMock.mock.calls[0];
    expect(calledId).toBe('conn-1');
    expect(payload).toMatchObject({
      name: 'Warehouse DB v2',
      type: 'postgres',
      host: 'db.local',
      database: 'warehouse',
      db_schema: 'public',
    });
    // createConnection NON deve essere chiamata sul branch edit.
    expect(createConnectionMock).not.toHaveBeenCalled();
  });
  it('warns when DB connection submit lacks host or database', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    await userEvent.type(
      await screen.findByPlaceholderText(/Archibus ICSC/i),
      'Halfway DB',
    );

    await userEvent.click(
      screen.getByRole('button', { name: /Crea Connessione/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith(
      'Host e database obbligatori',
    );
    expect(createConnectionMock).not.toHaveBeenCalled();
    expect(createFeatureConnectionMock).not.toHaveBeenCalled();
  });
  it('warns when ArcGIS connection submit lacks server_url', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /ArcGIS Server/i }),
    );
    await userEvent.type(
      screen.getByPlaceholderText(/Archibus ICSC/i),
      'ArcGIS Test',
    );

    await userEvent.click(
      screen.getByRole('button', { name: /Crea Connessione/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith(
      'URL server obbligatorio',
    );
    expect(createFeatureConnectionMock).not.toHaveBeenCalled();
    expect(createConnectionMock).not.toHaveBeenCalled();
  });
  it('routes ArcGIS submit to createFeatureConnection (not createConnection)', async () => {
    createFeatureConnectionMock.mockResolvedValueOnce({ id: 'arc-1' });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /ArcGIS Server/i }),
    );
    await userEvent.type(
      screen.getByPlaceholderText(/Archibus ICSC/i),
      'ArcGIS Prod',
    );
    await userEvent.type(
      screen.getByPlaceholderText(
        /https:\/\/server\/arcgis\/rest\/services/i,
      ),
      'https://arcgis.example.test/rest/services',
    );

    await userEvent.click(
      screen.getByRole('button', { name: /Crea Connessione/i }),
    );

    await waitFor(() => {
      expect(createFeatureConnectionMock).toHaveBeenCalledTimes(1);
    });
    const payload = createFeatureConnectionMock.mock.calls[0][0];
    expect(payload).toMatchObject({
      name: 'ArcGIS Prod',
      server_url: 'https://arcgis.example.test/rest/services',
      auth_type: 'public',
    });
    expect(createConnectionMock).not.toHaveBeenCalled();
  });
});
