import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import DataSourceManager from '../../src/pages/DataSourceManager';

/**
 * Page-level contract for DataSourceManager workflows.
 *
 * Covers load/render, empty-state creation entry points, create validation,
 * test-connection status, cascade-delete handling, and viewer role gating.
 */

const {
  navigateMock,
  toastMock,
  listDataSourcesMock,
  createDataSourceMock,
  updateDataSourceMock,
  deleteDataSourceMock,
  testDataSourceMock,
  listFilesMock,
  authState,
} = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  listDataSourcesMock: vi.fn(),
  createDataSourceMock: vi.fn(),
  updateDataSourceMock: vi.fn(),
  deleteDataSourceMock: vi.fn(),
  testDataSourceMock: vi.fn(),
  listFilesMock: vi.fn(),
  authState: {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
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
vi.mock('../../src/api/datasources', () => ({
  listDataSources: listDataSourcesMock,
  createDataSource: createDataSourceMock,
  updateDataSource: updateDataSourceMock,
  deleteDataSource: deleteDataSourceMock,
  testDataSource: testDataSourceMock,
  listFiles: listFilesMock,
}));
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => null,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));

const sampleDs = {
  id: 'ds-1',
  name: 'SharePoint ICSC',
  description: 'Tenant docs',
  type: 'sharepoint',
  site_url: 'https://tenant.sharepoint.com/sites/ICSC',
  username: 'svc',
  can_edit: true,
};

describe('DataSourceManager contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    listDataSourcesMock.mockResolvedValue({ datasources: [] });
  });


  it('loads data sources and renders a card with name, type label and meta', async () => {
    listDataSourcesMock.mockResolvedValueOnce({ datasources: [sampleDs] });

    render(<DataSourceManager />);

    expect(await screen.findByText('SharePoint ICSC')).toBeInTheDocument();
    // Type label dal TYPE_INFO map
    expect(screen.getByText(/SharePoint Online/i)).toBeInTheDocument();
    // Meta: site_url e username
    expect(screen.getByText(/sharepoint\.com\/sites\/ICSC/i)).toBeInTheDocument();
    expect(screen.getByText(/svc/)).toBeInTheDocument();
  });


  it('shows the empty state with the create CTA when no data sources exist', async () => {
    render(<DataSourceManager />);

    expect(
      await screen.findByText('Nessun Data Source configurato'),
    ).toBeInTheDocument();
    // CTA presente per admin (canCreateDs = isAdminOrAbove → true).
    const ctas = screen.getAllByRole('button', {
      name: /Nuovo Data Source/i,
    });
    expect(ctas.length).toBeGreaterThanOrEqual(1);
  });


  it('blocks save with an empty name and surfaces the inline form error', async () => {
    render(<DataSourceManager />);
    await screen.findByText('Nessun Data Source configurato');

    // Apri il modal "Nuovo Data Source" (header CTA).
    const headerCta = screen.getAllByRole('button', {
      name: /Nuovo Data Source/i,
    })[0];
    await userEvent.click(headerCta);

    // Click "Crea Data Source" senza compilare il nome.
    await userEvent.click(
      await screen.findByRole('button', { name: /Crea Data Source/i }),
    );

    // formError inline (no toast, branch handleSave validation).
    expect(
      await screen.findByText(/Il nome è obbligatorio/),
    ).toBeInTheDocument();
    expect(createDataSourceMock).not.toHaveBeenCalled();
  });


  it('defaults new SFTP sources to verified host keys', async () => {
    render(<DataSourceManager />);
    await screen.findByText('Nessun Data Source configurato');
    await userEvent.click(screen.getAllByRole('button', { name: /Nuovo Data Source/i })[0]);
    await userEvent.click(await screen.findByRole('button', { name: /SFTP/i }));

    expect(screen.getByRole('combobox')).toHaveValue('reject');
  });


  // S3: l'esito del test lo salva il backend e torna nella lista (last_test).
  it("mostra l'esito salvato del test: «Mai verificata», poi «Verificata il ... da ...»", async () => {
    const tested = {
      ...sampleDs,
      last_test: { success: true, message: 'Connessione OK', tested_at: '2026-11-05T10:00:00+00:00', tested_by: 'mario', duration_ms: 120, stale: false },
    };
    listDataSourcesMock
      .mockResolvedValueOnce({ datasources: [sampleDs] })
      .mockResolvedValue({ datasources: [tested] });
    testDataSourceMock.mockResolvedValueOnce({
      success: true,
      message: 'Connessione OK',
    });

    render(<DataSourceManager />);
    await screen.findByText('SharePoint ICSC');
    expect(screen.getByText('Mai verificata')).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: /Testa connessione/i }),
    );

    await waitFor(() =>
      expect(testDataSourceMock).toHaveBeenCalledWith('ds-1'),
    );
    const status = await screen.findByText(/^Verificata il .* da mario$/);
    expect(status).toHaveAttribute('title', expect.stringContaining('Connessione OK'));
    expect(listDataSourcesMock).toHaveBeenCalledTimes(2);
  });


  it('shows formatted cascade dependencies on 409 delete (no reload)', async () => {
    listDataSourcesMock.mockResolvedValueOnce({ datasources: [sampleDs] });
    deleteDataSourceMock.mockRejectedValueOnce({
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

    render(<DataSourceManager />);
    await screen.findByText('SharePoint ICSC');

    // Trash icon → apre il custom inline confirm modal.
    await userEvent.click(screen.getByTitle('Elimina'));
    // Il bottone "Elimina" del confirm modal e' l'unico con quel testo
    // visibile dopo l'apertura.
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('SharePoint ICSC');
    expect(msg).toContain('Demo Pipeline');
    expect(msg).toMatch(/progetto/i);
    expect(ttl).toBe(10000);
    // Reload non triggered (handleDelete early return dopo cascade).
    expect(listDataSourcesMock).toHaveBeenCalledTimes(1);
  });


  it('hides "+ Nuovo Data Source" for viewer role', async () => {
    authState.user = { role: 'viewer', username: 'v', group_id: 'grp-1' };
    listDataSourcesMock.mockResolvedValueOnce({ datasources: [sampleDs] });

    render(<DataSourceManager />);
    await screen.findByText('SharePoint ICSC');

    // can.createDataSource = isAdminOrAbove → viewer non vede CTA.
    expect(
      screen.queryByRole('button', { name: /Nuovo Data Source/i }),
    ).not.toBeInTheDocument();
    // Sanity: il viewer puo' comunque vedere la card e il pulsante Sfoglia.
    expect(screen.getByText('SharePoint ICSC')).toBeInTheDocument();
    expect(screen.getByTitle('Sfoglia')).toBeInTheDocument();
  });
});
