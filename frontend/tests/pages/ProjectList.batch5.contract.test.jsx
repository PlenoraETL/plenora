import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ProjectList from '../../src/pages/ProjectList';

/**
 * Batch 5 di FE-TEST-001 su ProjectList — gap ad alto valore non
 * coperti dal contract test esistente (ProjectList.contract.test.jsx,
 * focalizzato su create-estrai e routing card → editor).
 *
 * Aree:
 *   1. delete success path (confirm + deleteProject + reload)
 *   2. delete cascade 409 (handleCascade409 + formatCascadeDependencies
 *      + toast.error TTL 10s) — integrazione page-level, non testata
 *      altrove
 *   3. delete 500 project_cleanup_partial (branch dedicato — toast con
 *      "Ritenta o contatta admin", invariante project_preserved:true)
 *   4. create validation blocks empty name
 *   5. search filter restringe la lista per nome
 *   6. role gating: viewer NON vede "+ Nuovo Progetto" ne' "+ Nuovo"
 *      di sezione (can.createProject = isUserOrAbove)
 *
 * Nessun refactor della pagina, nessun touch a useTheme/ThemeToggle,
 * nessuna modifica backend/docs/audits.
 */

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

describe('ProjectList — batch 5', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
  });

  // ── 1. Delete success ────────────────────────────────────────────────

  it('deletes a project after confirm and reloads the dashboard data', async () => {
    mockDashboardData({
      projects: [sampleProject],
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });
    apiMock.deleteProject.mockResolvedValueOnce({ ok: true });

    render(<ProjectList />);
    await screen.findByText('Orders ETL');

    // Trash icon su ProjectCard.
    await userEvent.click(screen.getByRole('button', { name: '🗑' }));
    // ConfirmDialog button "Elimina" (confirmText nel componente).
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() =>
      expect(apiMock.deleteProject).toHaveBeenCalledWith('proj-1'),
    );
    // Reload: listProjects chiamata 2 volte (initial + dopo delete).
    expect(apiMock.listProjects).toHaveBeenCalledTimes(2);
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  // ── 2. Delete cascade 409 ────────────────────────────────────────────

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

    await userEvent.click(screen.getByRole('button', { name: '🗑' }));
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
    // No reload triggered (handleDeleteProject early return).
    expect(apiMock.listProjects).toHaveBeenCalledTimes(1);
  });

  // ── 3. Delete 500 project_cleanup_partial ────────────────────────────

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

    await userEvent.click(screen.getByRole('button', { name: '🗑' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Orders ETL');
    expect(msg).toContain('cleanup step canvas_db failed');
    expect(msg).toMatch(/Ritenta o contatta admin/i);
    expect(ttl).toBe(10000);
    // Reload non triggered: il branch fa early return dopo toast.
    expect(apiMock.listProjects).toHaveBeenCalledTimes(1);
  });

  // ── 4. Create validation: empty name ─────────────────────────────────

  it('blocks createProject with an empty name and warns the user', async () => {
    mockDashboardData({
      connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
    });

    render(<ProjectList />);

    await userEvent.click(
      await screen.findByRole('button', { name: /nuovo progetto/i }),
    );
    // Modal aperto: clicca "Crea Progetto" senza compilare il nome.
    await userEvent.click(
      await screen.findByRole('button', { name: /crea progetto/i }),
    );

    expect(toastMock.warning).toHaveBeenCalledWith('Inserisci un nome');
    expect(apiMock.createProject).not.toHaveBeenCalled();
  });

  // ── 5. Search filter ─────────────────────────────────────────────────

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

  // ── 6. Role gating: viewer ───────────────────────────────────────────

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
    // Sanity: il viewer puo' comunque vedere card e bottoni Apri.
    expect(screen.getByText('Orders ETL')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /apri/i }),
    ).toBeInTheDocument();
  });
});
