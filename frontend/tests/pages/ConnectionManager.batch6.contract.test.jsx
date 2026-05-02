import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ConnectionManager from '../../src/pages/ConnectionManager';

/**
 * Batch 6 di FE-TEST-001 su ConnectionManager — gap ad alto valore non
 * coperti dal contract test esistente (ConnectionManager.contract.test.jsx,
 * focalizzato sul solo flusso create-DB con payload shape).
 *
 * Aree:
 *   1. delete success (confirm + deleteConnection + reload)
 *   2. delete cascade 409 (handleCascade409 + formatCascadeDependencies
 *      + toast.error TTL 10s) — page-level mai testato
 *   3. test connection success: status badge "OK" sulla riga
 *   4. test connection error: badge "Errore" + banner cm__test-error
 *      con il messaggio del backend
 *   5. create validation: nome vuoto -> toast.warning, no createConnection
 *   6. edit flow: clic Modifica -> updateConnection con id + payload,
 *      titolo modale "Modifica Connessione" + "Salva Modifiche"
 *
 * Nessun refactor della pagina, nessun touch a useTheme/ThemeToggle,
 * nessuna modifica backend/docs/audits.
 */

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

describe('ConnectionManager — batch 6', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'superadmin', username: 'sa' };
    listConnectionsMock.mockResolvedValue({ connections: [sampleConn] });
    listFeatureConnectionsMock.mockResolvedValue({ connections: [] });
  });

  // ── 1. Delete success ────────────────────────────────────────────────

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

  // ── 2. Delete cascade 409 ────────────────────────────────────────────

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

  // ── 3. Test connection success ───────────────────────────────────────

  it('shows the OK status badge after a successful test connection', async () => {
    testConnectionMock.mockResolvedValueOnce({ success: true, message: 'ok' });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    // Pre-test: stato "Non testata".
    expect(screen.getByText('Non testata')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /🔌 Test/i }));

    await waitFor(() =>
      expect(testConnectionMock).toHaveBeenCalledWith('conn-1'),
    );
    expect(await screen.findByText('OK')).toBeInTheDocument();
    expect(screen.queryByText('Non testata')).not.toBeInTheDocument();
  });

  // ── 4. Test connection error ─────────────────────────────────────────

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
    // Banner con messaggio del backend (cm__test-error block).
    expect(
      await screen.findByText(/Connection refused: 5432/),
    ).toBeInTheDocument();
  });

  // ── 5. Create validation: nome vuoto ────────────────────────────────

  it('blocks createConnection when the name is empty and warns the user', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    // Click "Crea Connessione" senza compilare il nome.
    await userEvent.click(
      await screen.findByRole('button', { name: /Crea Connessione/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith(
      'Il nome è obbligatorio',
    );
    expect(createConnectionMock).not.toHaveBeenCalled();
  });

  // ── 6. Edit flow: updateConnection ──────────────────────────────────

  it('opens the edit modal prefilled and updates the connection on save', async () => {
    updateConnectionMock.mockResolvedValueOnce({ id: 'conn-1' });

    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /✏️ Modifica/i }),
    );

    // Modal aperto con titolo "Modifica Connessione" e nome precompilato.
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

  // ── 7. Validation: DB richiede host + database ──────────────────────

  it('warns when DB connection submit lacks host or database', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );
    // Compila SOLO il nome — host + database restano vuoti (default DB
    // postgres). Tipo postgres rimane attivo: branch !isArcgis.
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

  // ── 8. Validation: ArcGIS richiede server_url ───────────────────────

  it('warns when ArcGIS connection submit lacks server_url', async () => {
    render(<ConnectionManager />);
    await screen.findByText('Warehouse DB');

    await userEvent.click(
      screen.getByRole('button', { name: /Nuova Connessione/i }),
    );

    // Switch type a ArcGIS Server cliccando il bottone tipo.
    await userEvent.click(
      await screen.findByRole('button', { name: /ArcGIS Server/i }),
    );
    await userEvent.type(
      screen.getByPlaceholderText(/Archibus ICSC/i),
      'ArcGIS Test',
    );
    // server_url volutamente NON compilato.

    await userEvent.click(
      screen.getByRole('button', { name: /Crea Connessione/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith(
      'URL server obbligatorio',
    );
    expect(createFeatureConnectionMock).not.toHaveBeenCalled();
    expect(createConnectionMock).not.toHaveBeenCalled();
  });

  // ── 9. ArcGIS branch: createFeatureConnection invece di createConnection ─

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
    // Nessuna chiamata al branch DB.
    expect(createConnectionMock).not.toHaveBeenCalled();
  });
});
