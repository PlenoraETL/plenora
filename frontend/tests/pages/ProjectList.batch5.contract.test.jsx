import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ProjectList from '../../src/pages/ProjectList';


const { apiMock, featureMock, filesMock, schedulesMock, datasourcesMock,
        httpMock, toastMock, navigateMock, authState } = vi.hoisted(() => ({
  apiMock: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    listConnections: vi.fn(),
    listApiConnections: vi.fn(),
    listEseguibili: vi.fn(),
    deleteEseguibile: vi.fn(),
  },
  featureMock: { listFeatureConnections: vi.fn() },
  filesMock: { getFiles: vi.fn() },
  schedulesMock: { listSchedules: vi.fn() },
  datasourcesMock: { listDataSources: vi.fn() },
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
  authState: {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
  },
}));

vi.mock('../../src/api', () => apiMock);
vi.mock('../../src/api/features', () => featureMock);
vi.mock('../../src/api/files', () => filesMock);
vi.mock('../../src/api/schedules', () => schedulesMock);
vi.mock('../../src/api/datasources', () => datasourcesMock);
vi.mock('../../src/api/http', () => ({ default: httpMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useLocation: () => ({ hash: '' }),
  };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/PublishEseguibileModal', () => ({
  default: () => null,
}));
vi.mock('../../src/components/eseguibili/EseguibileExecuteWizard', () => ({
  default: () => null,
}));

function mockDashboardData({
  projects = [],
  connections = [],
  apiConnections = [],
  files = [],
  schedules = [],
  arcgisConnections = [],
  eseguibili = [],
  datasources = [],
} = {}) {
  apiMock.listProjects.mockResolvedValue({ projects });
  apiMock.listConnections.mockResolvedValue({ connections });
  apiMock.listApiConnections.mockResolvedValue({ api_connections: apiConnections });
  filesMock.getFiles.mockResolvedValue({ files });
  schedulesMock.listSchedules.mockResolvedValue({ schedules, total: schedules.length });
  featureMock.listFeatureConnections.mockResolvedValue({ connections: arcgisConnections });
  apiMock.listEseguibili.mockResolvedValue(eseguibili);
  datasourcesMock.listDataSources.mockResolvedValue({ datasources });
}

const sampleProject = {
  id: 'proj-1',
  name: 'Orders ETL',
  description: 'Daily orders pipeline',
  project_type: 'estrai',
  default_connection_id: 'conn-1',
  last_status: 'success',
};

describe('ProjectList contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
  });
  it('deletes a project after confirm with optimistic removal (no reload)', async () => {
    mockDashboardData({
      projects: [sampleProject],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });
    apiMock.deleteProject.mockResolvedValueOnce({ ok: true });

    render(<ProjectList />);
    await screen.findByText('Orders ETL');
    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Orders ETL' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '🗑 Elimina' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() =>
      expect(apiMock.deleteProject).toHaveBeenCalledWith('proj-1'),
    );
    // Epic C (delete ottimistico): la card sparisce subito via
    // removeProjectLocal, NESSUN reload su successo — solo il load
    // iniziale del mount.
    await waitFor(() =>
      expect(screen.queryByText('Orders ETL')).not.toBeInTheDocument(),
    );
    expect(apiMock.listProjects).toHaveBeenCalledTimes(1);
    expect(toastMock.error).not.toHaveBeenCalled();
  });
  it('shows formatted cascade dependencies on 409 delete (eseguibili + downstream projects)', async () => {
    mockDashboardData({
      projects: [sampleProject],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });
    apiMock.deleteProject.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: 'cascade_in_use',
            message: 'in use',
            dependencies: [
              { kind: 'eseguibile', id: 'es-1', name: 'Esec One' },
              { kind: 'project', id: 'p2', name: 'Downstream Project' },
            ],
          },
        },
      },
    });

    render(<ProjectList />);
    await screen.findByText('Orders ETL');

    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Orders ETL' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '🗑 Elimina' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Orders ETL');
    expect(msg).toContain('Esec One');
    expect(msg).toContain('Downstream Project');
    expect(msg).toMatch(/eseguibile/i);
    expect(msg).toMatch(/progetto/i);
    expect(ttl).toBe(10000);
    // Epic C: su errore la rimozione ottimistica viene RIPRISTINATA con
    // loadData() → load iniziale + reload di restore = 2.
    expect(apiMock.listProjects).toHaveBeenCalledTimes(2);
  });
  it('shows the cleanup_partial recovery message on 500 project_cleanup_partial', async () => {
    mockDashboardData({
      projects: [sampleProject],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });
    apiMock.deleteProject.mockRejectedValueOnce({
      response: {
        status: 500,
        data: {
          detail: {
            error: 'project_cleanup_partial',
            message: 'cleanup step canvas_db failed',
            project_preserved: true,
          },
        },
      },
    });

    render(<ProjectList />);
    await screen.findByText('Orders ETL');

    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Orders ETL' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '🗑 Elimina' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Orders ETL');
    expect(msg).toContain('cleanup step canvas_db failed');
    expect(msg).toMatch(/Ritenta o contatta admin/i);
    expect(ttl).toBe(10000);
    // Epic C: su errore la rimozione ottimistica viene RIPRISTINATA con
    // loadData() → load iniziale + reload di restore = 2.
    expect(apiMock.listProjects).toHaveBeenCalledTimes(2);
  });
  it('blocks createProject with an empty name and warns the user', async () => {
    mockDashboardData({
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });

    render(<ProjectList />);

    await userEvent.click(
      // /nuovo progetto$/: il quick-start ha aggiunto il bottone
      // "＋ Nuovo progetto Estrai" che matcherebbe /nuovo progetto/i.
      await screen.findByRole('button', { name: /nuovo progetto$/i }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /crea progetto/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith('Inserisci un nome');
    expect(apiMock.createProject).not.toHaveBeenCalled();
  });
  it('filters the project list by the search input', async () => {
    mockDashboardData({
      projects: [
        { ...sampleProject, id: 'p1', name: 'Apple Pipeline' },
        { ...sampleProject, id: 'p2', name: 'Banana Pipeline' },
      ],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });

    render(<ProjectList />);
    await screen.findByText('Apple Pipeline');
    expect(screen.getByText('Banana Pipeline')).toBeInTheDocument();

    const search = screen.getByPlaceholderText(/cerca progetti/i);
    await userEvent.type(search, 'apple');

    expect(screen.getByText('Apple Pipeline')).toBeInTheDocument();
    expect(screen.queryByText('Banana Pipeline')).not.toBeInTheDocument();
  });
  it('hides "+ Nuovo Progetto" and section "+ Nuovo" buttons for viewer role', async () => {
    authState.user = { role: 'viewer', username: 'v', group_id: 'grp-1' };
    mockDashboardData({
      projects: [sampleProject],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });

    render(<ProjectList />);
    await screen.findByText('Orders ETL');

    // can.createProject = isUserOrAbove → viewer NON puo' creare.
    expect(
      screen.queryByRole('button', { name: /Nuovo Progetto/i }),
    ).not.toBeInTheDocument();
    // Section "+ Nuovo" pulsanti (gated da canCreate prop su ProjectSection):
    expect(
      screen.queryByRole('button', { name: /^＋\s*Nuovo$/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Orders ETL')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /apri/i }),
    ).toBeInTheDocument();
  });
});
