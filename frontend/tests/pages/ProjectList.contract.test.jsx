import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ProjectList from '../../src/pages/ProjectList';


const { apiMock, featureMock, filesMock, schedulesMock, datasourcesMock, dashboardMock, httpMock, toastMock, navigateMock } = vi.hoisted(() => ({
  // UI-1 (C4): API, data source e schedule arrivano come conteggi.
  dashboardMock: {
    getDashboardCounts: vi.fn(),
  },
  apiMock: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    listConnections: vi.fn(),
    listApiConnections: vi.fn(),
    listEseguibili: vi.fn(),
    deleteEseguibile: vi.fn(),
  },
  featureMock: {
    listFeatureConnections: vi.fn(),
  },
  filesMock: {
    getFiles: vi.fn(),
  },
  schedulesMock: {
    listSchedules: vi.fn(),
  },
  datasourcesMock: {
    listDataSources: vi.fn(),
  },
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
}));

vi.mock('../../src/api', () => apiMock);
vi.mock('../../src/api/features', () => featureMock);
vi.mock('../../src/api/files', () => filesMock);
vi.mock('../../src/api/schedules', () => schedulesMock);
vi.mock('../../src/api/datasources', () => datasourcesMock);
vi.mock('../../src/api/dashboard', () => dashboardMock);
vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useLocation: () => ({ hash: '' }),
  };
});
vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));
vi.mock('../../src/components/AppSidebar', () => ({
  default: () => null,
}));
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
  const schedulesByProject = {};
  schedules.forEach((s) => { const pid = s.pipeline?.project_id; if (pid) schedulesByProject[pid] = (schedulesByProject[pid] || 0) + 1; });
  dashboardMock.getDashboardCounts.mockResolvedValue({
    api_connections: apiConnections.length,
    datasources: datasources.length,
    schedules_active: schedules.filter((s) => s.schedule?.enabled).length,
    schedules_by_project: schedulesByProject,
  });
}

describe('ProjectList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates an estrai project with the selected connection', async () => {
    mockDashboardData({
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });
    apiMock.createProject.mockResolvedValueOnce({ metadata: { id: 'proj-1' } });

    render(<ProjectList />);

    // /nuovo progetto$/: il quick-start ha aggiunto il bottone
    // "＋ Nuovo progetto Estrai" che matcherebbe /nuovo progetto/i.
    await screen.findByRole('button', { name: /nuovo progetto$/i });
    await userEvent.click(screen.getByRole('button', { name: /nuovo progetto$/i }));

    await userEvent.clear(screen.getByPlaceholderText(/catasto campania q2/i));
    await userEvent.type(screen.getByPlaceholderText(/catasto campania q2/i), 'Orders ETL');
    await userEvent.selectOptions(
      await screen.findByRole('combobox', { name: /connessione/i }),
      'conn-1',
    );
    await userEvent.click(screen.getByRole('button', { name: /crea progetto/i }));

    await waitFor(() => {
      expect(apiMock.createProject).toHaveBeenCalledWith('Orders ETL', '', 'estrai', 'conn-1');
    });
  });

  it('routes project cards to the expected editor pages and project schedule filter', async () => {
    mockDashboardData({
      projects: [
        {
          id: 'estrai-1',
          name: 'Extract Orders',
          description: 'estrai project',
          project_type: 'estrai',
          default_connection_id: 'conn-1',
          last_status: 'success',
        },
        {
          id: 'man-1',
          name: 'Transform Orders',
          description: 'manipola project',
          project_type: 'manipola',
          source_project_ids: ['estrai-1'],
          last_status: 'running',
        },
        {
          id: 'arr-1',
          name: 'Enrich Orders',
          description: 'arricchisci project',
          project_type: 'arricchisci',
          last_status: 'idle',
        },
      ],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
      schedules: [
        { id: 'sch-1', pipeline: { project_id: 'estrai-1' }, schedule: { enabled: true } },
      ],
    });

    render(<ProjectList />);

    await screen.findByText('Extract Orders');
    const openButtons = screen.getAllByRole('button', { name: /apri/i });
    await userEvent.click(openButtons[0]);
    await userEvent.click(openButtons[1]);
    await userEvent.click(openButtons[2]);

    expect(navigateMock).toHaveBeenNthCalledWith(1, '/project/estrai-1');
    expect(navigateMock).toHaveBeenNthCalledWith(2, '/editor/manipola/man-1');
    expect(navigateMock).toHaveBeenNthCalledWith(3, '/arricchisci/arr-1');

    await userEvent.click(screen.getByTitle(/pianificazioni automatiche/i));
    expect(navigateMock).toHaveBeenNthCalledWith(4, '/schedules?project_id=estrai-1');
  });
});
