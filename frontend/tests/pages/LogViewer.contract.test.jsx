import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import LogViewer from '../../src/pages/LogViewer';

/**
 * Page-level contract for LogViewer workflows.
 *
 * Covers admin log rendering, filter/search/pagination requests, and viewer
 * role gating to run history only.
 */

const { httpMock, toastMock, navigateMock, authState } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
  authState: {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
  },
}));

vi.mock('../../src/api/http', () => ({ default: httpMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock, useSearchParams: () => [new URLSearchParams(), vi.fn()] };
});
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
// Stub RunsTab; this file exercises LogViewer page behavior.
vi.mock('../../src/components/logviewer/RunsTab', () => ({
  default: () => null,
}));


const STATS_OK = {
  total: 10,
  by_level: { INFO: 5, WARNING: 3, ERROR: 2 },
};

const ENTRIES_2 = [
  {
    ts: '2026-04-15T10:00:00.000Z',
    level: 'INFO',
    module: 'estrai',
    op: 'execute',
    msg: 'Pipeline started',
    project_id: 'proj-1',
  },
  {
    ts: '2026-04-15T10:01:00.000Z',
    level: 'ERROR',
    module: 'carica',
    op: 'carica',
    msg: 'Connection refused',
    project_id: 'proj-2',
    error: 'ConnectionError: refused',
  },
];

function installHttpMock({ entries = ENTRIES_2, total = 2, hasMore = false, stats = STATS_OK } = {}) {
  httpMock.get.mockImplementation((url) => {
    if (url === '/logs/stats') {
      return Promise.resolve({ data: stats });
    }
    if (url === '/logs') {
      return Promise.resolve({ data: { entries, total, has_more: hasMore } });
    }
    return Promise.resolve({ data: {} });
  });
}

function lastLogsParams() {
  // Restituisce i params dell'ULTIMA chiamata a http.get('/logs').
  const calls = httpMock.get.mock.calls.filter((c) => c[0] === '/logs');
  return calls[calls.length - 1]?.[1]?.params || {};
}


describe('LogViewer contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    installHttpMock();
  });

  it('admin loads logs + stats and renders entries on the "Log Tecnici" tab', async () => {
    render(<LogViewer />);

    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );

    // Stats card
    expect(await screen.findByText('Log Totali')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();

    // Table entries
    expect(screen.getByText('Pipeline started')).toBeInTheDocument();
    expect(screen.getByText('Connection refused')).toBeInTheDocument();

    // Mount-effect ha chiamato getLogs(0) almeno una volta + getLogsStats.
    expect(
      httpMock.get.mock.calls.some((c) => c[0] === '/logs/stats'),
    ).toBe(true);
    const params = lastLogsParams();
    expect(params).toMatchObject({ limit: 50, offset: 0 });
    // Nessun filtro impostato sul mount.
    expect(params.level || '').toBe('');
    expect(params.search || '').toBe('');
  });

  it('changing the level filter dropdown triggers a reload with the level param', async () => {
    render(<LogViewer />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );
    // Aspetta il primo paint con la table (stat row indica admin/logs ready)
    await screen.findByText('Log Totali');

    // Trova il select "Livello" via la opzione "Tutti i livelli".
    const allLevelsOption = screen.getByRole('option', {
      name: /Tutti i livelli/i,
    });
    const levelSelect = allLevelsOption.closest('select');
    expect(levelSelect).toBeTruthy();

    await userEvent.selectOptions(levelSelect, 'ERROR');

    await waitFor(() => {
      expect(lastLogsParams().level).toBe('ERROR');
    });
  });

  it('clicking the ERROR stat card toggles the level filter and reloads', async () => {
    render(<LogViewer />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );
    const errStat = (await screen.findByText('Errori')).closest('.lv__stat');
    expect(errStat).toBeTruthy();

    await userEvent.click(errStat);

    await waitFor(() => {
      expect(lastLogsParams().level).toBe('ERROR');
    });
    // Active class applicata al click (filterLevel === 'ERROR').
    expect(errStat.classList.contains('lv__stat--active')).toBe(true);
  });

  // P28: ricerca lato server con la pausa unica (SearchField), niente Invio obbligatorio.
  it('la ricerca dopo la pausa passa il testo come parametro `search`', async () => {
    render(<LogViewer />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );

    const input = await screen.findByRole('searchbox', { name: 'Cerca nel messaggio' });
    await userEvent.type(input, 'connection refused');

    await waitFor(() => {
      expect(lastLogsParams().search).toBe('connection refused');
    });
  });

  it('pagination "Successivi →" calls getLogs with the next offset', async () => {
    installHttpMock({
      entries: ENTRIES_2,
      total: 120, // > PAGE_SIZE (50) → la pagination row e' visibile
      hasMore: true, // → bottone Successivi abilitato
    });

    render(<LogViewer />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );

    // Aspetta che la table sia popolata e la paginazione visibile.
    await screen.findByText('Pipeline started');
    const nextBtn = await screen.findByRole('button', {
      name: /Successivi/i,
    });
    expect(nextBtn).not.toBeDisabled();

    await userEvent.click(nextBtn);

    await waitFor(() => {
      expect(lastLogsParams().offset).toBe(50);
    });
  });

  it('viewer does not see the "Log Tecnici" tab, the Export CSV button, or the technical title', async () => {
    authState.user = { role: 'viewer', username: 'v', group_id: 'grp-1' };
    installHttpMock();

    render(<LogViewer />);

    // Titolo non-admin
    expect(await screen.findByText('Cronologia Esecuzioni')).toBeInTheDocument();
    expect(screen.queryByText('Log di Sistema')).not.toBeInTheDocument();

    // No tab bar (mainTab forzato a 'runs', niente "Log Tecnici")
    expect(
      screen.queryByRole('button', { name: /Log Tecnici/i }),
    ).not.toBeInTheDocument();
    // No Export CSV (gated da canSeeTechnicalLog && mainTab === 'logs')
    expect(
      screen.queryByRole('button', { name: /Export CSV/i }),
    ).not.toBeInTheDocument();
  });

  it('expands a log row with details and shows the inline error trace on click', async () => {
    render(<LogViewer />);

    await userEvent.click(
      await screen.findByRole('button', { name: /Log Tecnici/i }),
    );
    await screen.findByText('Connection refused');

    // Riga 2 (ENTRIES_2[1]) ha entry.error -> hasDetails=true.
    // Click sulla cella messaggio fa toggle dell'expandedRow.
    const row = screen.getByText('Connection refused').closest('tr');
    await userEvent.click(row);

    // Detail row mostra l'error trace dal field `entry.error`.
    expect(
      await screen.findByText(/ConnectionError: refused/),
    ).toBeInTheDocument();
  });
});
