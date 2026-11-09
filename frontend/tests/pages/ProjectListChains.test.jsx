/**
 * Home per catene (09/11/2026): la pagina con i dati come quelli di
 * produzione (19 progetti del mockup approvato). Catene, evidenza a
 * monte/a valle, pannello con «Usa» / «È usato da» e azioni, «Da guardare»,
 * filtri, colonna richiusa, contatori, interruttore «Catene / Elenco»
 * ricordato in questo browser.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ProjectList from '../../src/pages/ProjectList';
import { PRODUCTION_GRAPH, SC8 } from '../fixtures/homeChainsGraph';
import { takeRunOnOpen } from '../../src/utils/pendingRun';

const { apiMock, featureMock, filesMock, dashboardMock, httpMock, toastMock, navigateMock } = vi.hoisted(() => ({
  dashboardMock: { getDashboardCounts: vi.fn(), getDashboardGraph: vi.fn() },
  apiMock: {
    listProjects: vi.fn(), createProject: vi.fn(), deleteProject: vi.fn(), cloneProject: vi.fn(), updateProject: vi.fn(),
    listConnections: vi.fn(), listApiConnections: vi.fn(), listEseguibili: vi.fn(), deleteEseguibile: vi.fn(),
  },
  featureMock: { listFeatureConnections: vi.fn() },
  filesMock: { getFiles: vi.fn() },
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
}));

vi.mock('../../src/api', () => apiMock);
vi.mock('../../src/api/features', () => featureMock);
vi.mock('../../src/api/files', () => filesMock);
vi.mock('../../src/api/dashboard', () => dashboardMock);
vi.mock('../../src/api/http', () => ({ default: httpMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock, useLocation: () => ({ hash: '' }) };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/PublishEseguibileModal', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/EseguibileExecuteWizard', () => ({
  default: ({ eseguibileId }) => <div data-testid="execute-wizard">{eseguibileId}</div>,
}));

const listProjects = PRODUCTION_GRAPH.projects.map((p) => ({
  id: p.id, name: p.name, project_type: p.project_type, default_connection_id: p.default_connection_id,
  tags: [], created_at: '2026-07-01T08:00:00Z', updated_at: p.updated_at, group_id: 'grp-1', created_by: 'tester',
}));

function mockData(graph = PRODUCTION_GRAPH) {
  apiMock.listProjects.mockResolvedValue({ projects: listProjects });
  apiMock.listConnections.mockResolvedValue({ connections: [{ id: 'conn-1', name: 'ARCHIBUS_EMULE' }] });
  apiMock.listApiConnections.mockResolvedValue({ api_connections: [] });
  apiMock.listEseguibili.mockResolvedValue(PRODUCTION_GRAPH.eseguibili.map((e) => ({ ...e, group_id: 'grp-1', can_edit: true })));
  filesMock.getFiles.mockResolvedValue({ files: [] });
  featureMock.listFeatureConnections.mockResolvedValue({ connections: [] });
  dashboardMock.getDashboardCounts.mockResolvedValue({ api_connections: 0, datasources: 0, schedules_active: 0, schedules_by_project: {} });
  dashboardMock.getDashboardGraph.mockResolvedValue(graph);
}

const chainTitles = () => screen.getAllByRole('heading', { level: 2 })
  .filter((h) => h.classList.contains('ch__chain-title')).map((h) => h.textContent);
const card = (name, type) => screen.getAllByRole('button', { pressed: false })
  .concat(screen.queryAllByRole('button', { pressed: true }))
  .find((b) => b.classList.contains('ch__card') && b.querySelector('.ch__card-name')?.textContent === name
    && (!type || b.querySelector('.ch__badge')?.textContent === type));
const panel = () => screen.getByRole('region', { name: 'Progetto selezionato' });

async function renderHome() {
  render(<ProjectList />);
  await waitFor(() => expect(chainTitles().length).toBeGreaterThan(0));
}

describe('home per catene', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.removeItem('plenora:home-view');
    mockData();
  });

  it('di default: catene di produzione, prima quelle da guardare, «Non usati» in fondo', async () => {
    await renderHome();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Progetti');
    expect(chainTitles()).toEqual([
      'Copia di immobili attivi affittati/sfitti',
      'Copia di SC_scheda_manipola',
      'immobili attivi per istituto',
      'SC_A_patrimonio_unita_AMM',
      'Non usati da altri progetti',
    ]);
    // Colonne del mockup (Arricchisci insieme a Manipola).
    for (const label of ['Connessione', 'Estrai', 'Manipola e Arricchisci', 'Pubblicato']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    // Contatori.
    const counters = screen.getByRole('region', { name: 'Contatori' });
    expect(within(counters).getByText('19').nextSibling).toHaveTextContent('progetti');
    expect(within(counters).getByText('2').nextSibling).toHaveTextContent('pubblicati per gli esecutori');
    expect(within(counters).getByText('0').nextSibling).toHaveTextContent('pianificazioni attive');
    expect(within(counters).getByText('1').nextSibling).toHaveTextContent('connessione usata');
    // Filtri con i numeri.
    expect(screen.getByRole('button', { name: 'Da guardare (5)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Non usati (2)' })).toBeInTheDocument();
    // Nessun canvas chiesto: solo la risorsa del grafo.
    expect(dashboardMock.getDashboardGraph).toHaveBeenCalledTimes(1);
  });

  it('i 9 Estrai della scheda complesso sono richiusi e si aprono', async () => {
    const user = userEvent.setup();
    await renderHome();
    const more = screen.getByRole('button', { name: /9 Estrai/ });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(card('SC_G_atti')).toBeUndefined();
    await user.click(more);
    expect(card('SC_G_atti')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Richiudi' }));
    expect(card('SC_G_atti')).toBeUndefined();
  });

  it('clic su un progetto: a monte e a valle evidenziati, il resto sbiadito; pannello con Usa / È usato da', async () => {
    const user = userEvent.setup();
    await renderHome();
    await user.click(card('immobili attivi per istituto', 'Manipola'));
    const sel = card('immobili attivi per istituto', 'Manipola');
    expect(sel).toHaveAttribute('aria-pressed', 'true');
    expect(sel.className).toMatch(/ch__card--sel/);
    expect(card('immobili attivi', 'Estrai').className).toMatch(/ch__card--up/);
    expect(card('immobili attivi', 'Estrai')).toHaveTextContent('usato');
    expect(card('immobili attivi per istituto', 'Eseguibile').className).toMatch(/ch__card--down/);
    expect(card('immobili attivi per istituto', 'Eseguibile')).toHaveTextContent('lo usa');
    expect(card('SC_H_ticket').className).toMatch(/ch__card--none/);

    const p = panel();
    expect(within(p).getByRole('heading', { level: 2 })).toHaveTextContent('immobili attivi per istituto');
    expect(within(p).getByText('Ultima esecuzione 09/11 · riuscita')).toBeInTheDocument();
    const uses = within(p).getByText('Usa').parentElement;
    expect(within(uses).getByRole('button', { name: /immobili attivi\s*Estrai/ })).toBeInTheDocument();
    const usedBy = within(p).getByText('È usato da').parentElement;
    // «Usa» e «È usato da» sono cliccabili: portano al progetto.
    await user.click(within(usedBy).getByRole('button', { name: /immobili attivi per istituto\s*Eseguibile/ }));
    expect(within(panel()).getByText('Nessun input · versione 1')).toBeInTheDocument();
    expect(within(panel()).getByText('Nessun input: chi lo esegue vede tutti i dati del progetto')).toBeInTheDocument();
    // Esc toglie la selezione.
    await user.keyboard('{Escape}');
    expect(within(panel()).getByText(/Clicca un progetto/)).toBeInTheDocument();
  });

  it('azioni del pannello: Apri, Esegui (apre l\'editor e lo avvia), Cronologia, Esegui dell\'Eseguibile, menu ⋯', async () => {
    const user = userEvent.setup();
    await renderHome();
    await user.click(card('immobili attivi per istituto', 'Manipola'));
    await user.click(within(panel()).getByRole('button', { name: 'Apri' }));
    expect(navigateMock).toHaveBeenLastCalledWith('/editor/manipola/iapi');
    await user.click(within(panel()).getByRole('button', { name: '▶ Esegui' }));
    expect(navigateMock).toHaveBeenLastCalledWith('/editor/manipola/iapi');
    expect(takeRunOnOpen('iapi')).toBe(true);
    await user.click(within(panel()).getByRole('button', { name: /Cronologia/ }));
    expect(navigateMock).toHaveBeenLastCalledWith('/logs?project=iapi');
    await user.click(within(panel()).getByRole('button', { name: /Azioni per/ }));
    for (const item of ['✎ Rinomina', '⧉ Clona', /Pubblica come Eseguibile/, /Elimina/]) {
      expect(screen.getByRole('menuitem', { name: item })).toBeInTheDocument();
    }
    await user.keyboard('{Escape}');
    await user.click(card('SC_A_patrimonio_unita_AMM', 'Eseguibile'));
    await user.click(within(panel()).getByRole('button', { name: '▶ Esegui' }));
    expect(screen.getByTestId('execute-wizard')).toHaveTextContent('ex-amm');
  });

  it('«Da guardare»: un clic seleziona il progetto; i filtri restringono le catene', async () => {
    const user = userEvent.setup();
    await renderHome();
    const watch = screen.getByRole('region', { name: 'Da guardare' });
    await user.click(within(watch).getByRole('button', { name: /SC_scheda_manipola · Manipola/ }));
    expect(within(panel()).getByText(/8 sorgenti sono state rieseguite/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Pubblicati' }));
    expect(chainTitles()).toEqual(['immobili attivi per istituto', 'SC_A_patrimonio_unita_AMM']);
    await user.click(screen.getByRole('button', { name: 'Non usati (2)' }));
    expect(chainTitles()).toEqual(['Non usati da altri progetti']);
    await user.click(screen.getByRole('button', { name: 'Tutte le catene' }));
    expect(chainTitles()).toHaveLength(5);
  });

  it('ricerca per nome, tabella e connessione', async () => {
    const user = userEvent.setup();
    await renderHome();
    const box = screen.getByRole('searchbox', { name: 'Cerca progetto, tabella, connessione' });
    await user.type(box, 'afm_bl{Enter}');
    await waitFor(() => expect(chainTitles()).toEqual(['immobili attivi per istituto']));
    await user.clear(box);
    await user.type(box, 'ticket{Enter}');
    await waitFor(() => expect(chainTitles()).toEqual(['Non usati da altri progetti']));
  });

  it('«Catene / Elenco»: l\'elenco a schede resta e la scelta e\' ricordata', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ProjectList />);
    await waitFor(() => expect(chainTitles().length).toBeGreaterThan(0));
    await user.click(screen.getByRole('button', { name: 'Elenco' }));
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Dashboard');
    expect(screen.queryByRole('region', { name: 'Contatori' })).toBeNull();
    expect(JSON.parse(window.localStorage.getItem('plenora:home-view'))).toBe('list');
    unmount();
    render(<ProjectList />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Elenco' })).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.queryByRole('region', { name: 'Contatori' })).toBeNull();
  });

  it('una parte non disponibile e\' detta e le sue verifiche tacciono', async () => {
    mockData({ ...PRODUCTION_GRAPH, unavailable: ['runs'] });
    await renderHome();
    expect(screen.getByText(/stato delle esecuzioni: le verifiche che li usano non sono state fatte/)).toBeInTheDocument();
    expect(screen.queryByText(/sorgenti sono state rieseguite/)).toBeNull();
    expect(screen.getAllByText('Stato non disponibile').length).toBeGreaterThan(0);
  });

  it('le schede sono pulsanti raggiungibili da tastiera', async () => {
    await renderHome();
    const cards = document.querySelectorAll('.ch__card');
    expect(cards.length).toBeGreaterThan(0);
    for (const c of cards) expect(c.tagName).toBe('BUTTON');
    expect(SC8.length).toBe(8);
  });
});
