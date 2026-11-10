/**
 * Home: azioni e «Visibile a» per ogni tipo di nodo (10/11/2026), sulla
 * pagina con i dati di produzione. Per ruolo: il superadmin vede
 * «Condividi», admin e user no; le azioni aprono le finestre delle pagine
 * di origine (condivisione, versioni) e chiamano le stesse API.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ProjectList from '../../src/pages/ProjectList';
import { productionGraph } from '../fixtures/homeChainsGraph';
import { takeOnOpen } from '../../src/utils/pendingRun';

const { apiMock, featureMock, filesMock, dashboardMock, httpMock, toastMock, navigateMock, authState } = vi.hoisted(() => ({
  dashboardMock: { getDashboardCounts: vi.fn(), getDashboardGraph: vi.fn() },
  apiMock: {
    listProjects: vi.fn(), createProject: vi.fn(), deleteProject: vi.fn(), cloneProject: vi.fn(), updateProject: vi.fn(),
    listConnections: vi.fn(), listApiConnections: vi.fn(), listEseguibili: vi.fn(), deleteEseguibile: vi.fn(),
    updateEseguibile: vi.fn(), testConnection: vi.fn(),
  },
  featureMock: { listFeatureConnections: vi.fn(), testFeatureConnection: vi.fn() },
  filesMock: { getFiles: vi.fn() },
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
  authState: { user: null },
}));

vi.mock('../../src/api', () => apiMock);
vi.mock('../../src/api/features', () => featureMock);
vi.mock('../../src/api/files', () => filesMock);
vi.mock('../../src/api/dashboard', () => dashboardMock);
vi.mock('../../src/api/http', () => ({ default: httpMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: authState.user, logout: vi.fn() }) }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock, useLocation: () => ({ hash: '' }) };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/PublishEseguibileModal', () => ({
  default: ({ project }) => <div data-testid="publish-modal">{project.id}</div>,
}));
vi.mock('../../src/components/eseguibili/EseguibileExecuteWizard', () => ({
  default: ({ eseguibileId }) => <div data-testid="execute-wizard">{eseguibileId}</div>,
}));
vi.mock('../../src/components/eseguibili/EseguibileVersionsModal', () => ({
  default: ({ eseguibile }) => <div data-testid="versions-modal">{eseguibile.id}</div>,
}));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: ({ resourceType, resourceId, disabled }) => (
    <div data-testid="share-selector">{`${resourceType}:${resourceId}:${disabled ? 'ro' : 'rw'}`}</div>
  ),
}));

const SUPER = { role: 'superadmin', username: 'root', group_id: 'grp-1' };
const ADMIN = { role: 'admin', username: 'tester', group_id: 'grp-1' };
const USER = { role: 'user', username: 'mario', group_id: 'grp-1' };

function graphWith({ shares = true } = {}) {
  const g = productionGraph();
  g.projects = g.projects.map((p) => ({ ...p, group_id: 'grp-1' }));
  g.access = [
    { resource_type: 'eseguibili', id: 'ex-iapi', group_id: 'grp-1', shared_with: shares ? ['grp-3'] : null },
    { resource_type: 'eseguibili', id: 'ex-amm', group_id: 'grp-1', shared_with: shares ? ['*'] : null },
    { resource_type: 'connections', id: 'conn-1', group_id: 'grp-2', shared_with: shares ? [] : null },
  ];
  g.groups = shares ? { 'grp-1': 'Uffici', 'grp-2': 'Patrimonio', 'grp-3': 'Ospiti' } : { 'grp-1': 'Uffici', 'grp-2': 'Patrimonio' };
  return g;
}

function mockData(graph, { sourceChanged = false } = {}) {
  const listProjects = graph.projects.map((p) => ({
    id: p.id, name: p.name, project_type: p.project_type, default_connection_id: p.default_connection_id,
    tags: [], created_at: '2026-07-01T08:00:00Z', updated_at: p.updated_at, group_id: 'grp-1', created_by: 'tester',
  }));
  apiMock.listProjects.mockResolvedValue({ projects: listProjects });
  apiMock.listConnections.mockResolvedValue({ connections: [{ id: 'conn-1', name: 'ARCHIBUS_EMULE', group_id: 'grp-2', can_edit: true }] });
  apiMock.listApiConnections.mockResolvedValue({ api_connections: [] });
  apiMock.listEseguibili.mockResolvedValue(graph.eseguibili.map((e) => ({ ...e, group_id: 'grp-1', can_edit: true, source_changed: sourceChanged })));
  apiMock.updateEseguibile.mockResolvedValue({});
  apiMock.testConnection.mockResolvedValue({ success: true, message: 'Connessione riuscita' });
  filesMock.getFiles.mockResolvedValue({ files: [] });
  featureMock.listFeatureConnections.mockResolvedValue({ connections: [] });
  dashboardMock.getDashboardCounts.mockResolvedValue({ api_connections: 0, datasources: 0, schedules_active: 0, schedules_by_project: {} });
  dashboardMock.getDashboardGraph.mockResolvedValue(graph);
}

const card = (name, type) => [...document.querySelectorAll('.ch__card')]
  .find((b) => b.querySelector('.ch__card-name')?.textContent === name && (!type || b.querySelector('.ch__badge')?.textContent === type));
const panel = () => screen.getByRole('region', { name: 'Progetto selezionato' });
const visibleTo = () => [...within(panel()).getByRole('group', { name: 'Visibile a' }).querySelectorAll('li')].map((li) => li.textContent);

async function renderHome(who, graph = graphWith({ shares: who === SUPER }), opts) {
  authState.user = who;
  mockData(graph, opts);
  render(<ProjectList />);
  await waitFor(() => expect(document.querySelectorAll('.ch__card').length).toBeGreaterThan(0));
  // Le liste della pagina (permessi degli elementi) sono caricate.
  await waitFor(() => expect(apiMock.listEseguibili).toHaveBeenCalled());
}

async function menuItems(ux) {
  const button = within(panel()).queryByRole('button', { name: /Azioni per/ });
  if (!button) return [];
  await ux.click(button);
  const names = screen.queryAllByRole('menuitem').map((m) => m.textContent);
  await ux.keyboard('{Escape}');
  return names;
}

describe('home: azioni del pannello per tipo di nodo e ruolo', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.removeItem('plenora:home-view');
  });

  it.each([
    ['superadmin', SUPER, ['🔗 Condividi', '✎ Rinomina', 'Versioni', '🗑 Elimina']],
    ['admin', ADMIN, ['✎ Rinomina', 'Versioni', '🗑 Elimina']],
    ['user', USER, []],
  ])('Eseguibile, %s: le azioni della pagina Eseguibili', async (_, who, expected) => {
    const ux = userEvent.setup();
    await renderHome(who);
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    const buttons = within(panel()).getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toContain('Apri il progetto');
    expect(buttons).toContain('▶ Esegui');
    expect(buttons).not.toContain('Pubblica nuova versione');
    await waitFor(async () => expect(await menuItems(ux)).toEqual(expected));
  });

  it('superadmin: «Condividi» apre la finestra comune sulla risorsa «eseguibili»; «Visibile a» con i gruppi', async () => {
    const ux = userEvent.setup();
    await renderHome(SUPER);
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    expect(visibleTo()).toEqual(['Gruppo Uffici (proprietario)', 'Gruppo Ospiti']);
    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: '🔗 Condividi' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Condividi Eseguibile')).toBeInTheDocument();
    expect(within(dialog).getByTestId('share-selector')).toHaveTextContent('eseguibili:ex-iapi:rw');
    // «*» = tutti i gruppi.
    await ux.keyboard('{Escape}');
    await ux.click(card('SC_A_patrimonio_unita_AMM', 'Eseguibile'));
    expect(visibleTo()).toEqual(['Gruppo Uffici (proprietario)', 'Tutti i gruppi']);
  });

  it('admin: «Visibile a» senza l\'elenco delle condivisioni (le vede il superadmin)', async () => {
    const ux = userEvent.setup();
    await renderHome(ADMIN);
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    expect(visibleTo()).toEqual(['Gruppo Uffici (proprietario)']);
    expect(within(panel()).getByText(/le vede e le gestisce il superadmin/)).toBeInTheDocument();
  });

  it('Versioni, Rinomina ed Elimina: la finestra delle versioni e le API della pagina Eseguibili', async () => {
    const ux = userEvent.setup();
    apiMock.deleteEseguibile.mockResolvedValue({});
    await renderHome(ADMIN);
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: 'Versioni' }));
    expect(screen.getByTestId('versions-modal')).toHaveTextContent('ex-iapi');

    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: '✎ Rinomina' }));
    const field = within(panel()).getByRole('textbox', { name: 'Nuovo nome' });
    await ux.clear(field);
    await ux.type(field, 'Patrimonio per istituto{Enter}');
    await waitFor(() => expect(apiMock.updateEseguibile).toHaveBeenCalledWith('ex-iapi', { name: 'Patrimonio per istituto' }));
    expect(toastMock.success).toHaveBeenCalledWith('Eseguibile rinominato');

    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: '🗑 Elimina' }));
    await ux.click(await screen.findByRole('button', { name: 'Elimina' }));
    await waitFor(() => expect(apiMock.deleteEseguibile).toHaveBeenCalledWith('ex-iapi'));
  });

  it('«Pubblica nuova versione» se il progetto e\' cambiato: la finestra delle versioni', async () => {
    const ux = userEvent.setup();
    await renderHome(ADMIN, undefined, { sourceChanged: true });
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    await ux.click(await within(panel()).findByRole('button', { name: 'Pubblica nuova versione' }));
    expect(screen.getByTestId('versions-modal')).toHaveTextContent('ex-iapi');
  });

  it.each([
    ['superadmin', SUPER, ['✏️ Modifica', 'Apri Connessioni', '🔌 Test'], ['🔗 Condividi']],
    ['admin', ADMIN, ['✏️ Modifica', 'Apri Connessioni', '🔌 Test'], []],
    ['user', USER, ['Apri Connessioni', '🔌 Test'], []],
  ])('connessione, %s: Modifica, Apri, Test, Condividi', async (_, who, buttons, menu) => {
    const ux = userEvent.setup();
    await renderHome(who);
    await ux.click(card('ARCHIBUS_EMULE', 'Connessione'));
    await waitFor(() => expect(within(panel()).getAllByRole('button').map((b) => b.textContent)).toEqual(expect.arrayContaining(buttons)));
    expect(await menuItems(ux)).toEqual(menu);
  });

  it('connessione: Test, Modifica (la finestra della pagina Connessioni), Condividi, «Visibile a»', async () => {
    const ux = userEvent.setup();
    await renderHome(SUPER);
    await ux.click(card('ARCHIBUS_EMULE', 'Connessione'));
    expect(visibleTo()).toEqual(['Gruppo Patrimonio (proprietario)']);
    expect(within(panel()).getByText('Non condiviso con altri gruppi.')).toBeInTheDocument();
    await ux.click(await within(panel()).findByRole('button', { name: '🔌 Test' }));
    await waitFor(() => expect(apiMock.testConnection).toHaveBeenCalledWith('conn-1'));
    expect(toastMock.success).toHaveBeenCalledWith('Test di «ARCHIBUS_EMULE»: Connessione riuscita');

    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: '🔗 Condividi' }));
    expect(within(await screen.findByRole('dialog')).getByTestId('share-selector')).toHaveTextContent('connections:conn-1:rw');
    await ux.keyboard('{Escape}');

    await ux.click(within(panel()).getByRole('button', { name: '✏️ Modifica' }));
    expect(navigateMock).toHaveBeenLastCalledWith('/connections');
    expect(takeOnOpen('editConnection', 'db:conn-1')).toBe(true);
  });

  it('progetto: le azioni della scheda e «Visibile a: tutto il gruppo»', async () => {
    const ux = userEvent.setup();
    await renderHome(ADMIN);
    await ux.click(card('immobili attivi per istituto', 'Manipola'));
    expect(visibleTo()).toEqual(['Tutto il gruppo Uffici']);
    const buttons = within(panel()).getAllByRole('button').map((b) => b.textContent);
    for (const b of ['Apri', '▶ Esegui', '🕐 Cronologia']) expect(buttons).toContain(b);
    expect(await menuItems(ux)).toEqual(['✎ Rinomina', '⧉ Clona', '🚀 Pubblica come Eseguibile', '🗑 Elimina']);
    await ux.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    await ux.click(screen.getByRole('menuitem', { name: '🚀 Pubblica come Eseguibile' }));
    expect(screen.getByTestId('publish-modal')).toHaveTextContent('iapi');
  });

  it('progetto, user: rinomina e clona (come la scheda), non pubblica né elimina un progetto altrui', async () => {
    const ux = userEvent.setup();
    await renderHome(USER);
    await ux.click(card('immobili attivi per istituto', 'Manipola'));
    expect(await menuItems(ux)).toEqual(['✎ Rinomina', '⧉ Clona']);
  });

  it('parte «access» non caricata: «Visibile a» lo dice', async () => {
    const ux = userEvent.setup();
    const g = graphWith();
    g.access = [];
    g.unavailable = ['access'];
    await renderHome(SUPER, g);
    expect(screen.getByText(/proprietari e condivisioni: le verifiche/)).toBeInTheDocument();
    await ux.click(card('immobili attivi per istituto', 'Eseguibile'));
    expect(visibleTo()).toEqual(['Non caricato']);
  });
});
