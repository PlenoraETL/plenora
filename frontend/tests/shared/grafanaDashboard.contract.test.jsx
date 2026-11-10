/**
 * Dashboard Grafana degli Eseguibili: frontend (11/11/2026). Raccolta in
 * docs/audits/grafana-dashboard-raccolta-2026-11-11.md (sezione L).
 *
 *  1. UNA regola per «Apri dashboard» (dashboardTarget): tabella di tutti
 *     i casi (ruolo, has_dashboard, output a file, stato del risultato).
 *  2. Enumerazione: ogni percorso che mostra la finestra dei risultati di
 *     un Eseguibile (chi usa useEseguibileRun) passa `run.dashboard` al
 *     pulsante unico; il pulsante, l'iframe e le voci esistono solo nei
 *     posti dichiarati (DashboardLauncher, tabella NODE_ACTIONS); le API
 *     della dashboard solo da dashboardActions e useDashboardPass.
 *  3. Voci del menu dalla tabella: per ruolo, funzione attiva, con/senza dashboard.
 *  4. Sessione + lasciapassare + rinnovo (useDashboardPass): ordine,
 *     rinnovo prima della scadenza con il nuovo iframe, fine dei dati,
 *     410, indirizzo non sicuro, chiusura che ferma tutto.
 *  5. Finestra: iframe stesso origine con sandbox, referrer e titolo; si
 *     chiude quando il bersaglio sparisce (risultato non piu' disponibile).
 *  6. «Modifica in Grafana»: scheda aperta al clic, link poi sessione,
 *     mai un indirizzo fuori da /grafana/; scheda bloccata = stessa finestra.
 *  7. Logout, invalidazioni, messaggi degli errori nuovi.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  issueEseguibileDashboardPass: vi.fn(),
  openGrafanaSession: vi.fn(),
  getEseguibileDashboardEditLink: vi.fn(),
  createEseguibileDashboard: vi.fn(),
  deleteEseguibileDashboard: vi.fn(),
}));
vi.mock('../../src/api', async (orig) => ({ ...(await orig()), ...api }));
const auth = vi.hoisted(() => ({ user: { role: 'esecutore', user_id: 'e' } }));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));

import {
  OPENABLE_RESULT_STATES, RENEW_EVERY_MS, RENEW_MARGIN_MS, dashboardFeatureFromList, dashboardTarget,
  dashboardWarningsText, nextRenewal, safeGrafanaPath,
} from '../../src/utils/eseguibileDashboard';
import { NODE_ACTIONS, panelActions } from '../../src/utils/nodeActions';
import { useDashboardPass } from '../../src/hooks/useDashboardPass';
import DashboardLauncher, { DASHBOARD_IFRAME_SANDBOX } from '../../src/components/eseguibili/DashboardLauncher';
import { openDashboardEditor } from '../../src/utils/dashboardActions';
import { closeGrafanaSession } from '../../src/api/grafana';
import { invalidationsForWrite } from '../../src/utils/sharedInvalidations';
import { ERROR_MESSAGES } from '../../src/utils/errorMessages';

const SRC = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8');
function sources(dir = SRC) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === 'mocks' ? [] : sources(full);
    return /\.jsx?$/.test(e.name) ? [path.relative(SRC, full).split(path.sep).join('/')] : [];
  });
}

const esecutore = { role: 'esecutore', user_id: 'e' };
const admin = { role: 'admin', user_id: 'a' };
const superadmin = { role: 'superadmin', user_id: 's' };
const ESE = Object.freeze({ id: 'x1', name: 'Valori OMI', has_dashboard: true, output_is_file_based: false, can_edit: true, dashboard_warnings: [{ column: 'zona', motivo: 'non c\'è più' }] });

describe('1. una regola per «Apri dashboard»', () => {
  const base = { user: esecutore, eseguibile: ESE, taskId: 't1', resultState: 'on_server' };
  it.each([
    ['risultato sul server', {}, true],
    ['risultato scaricato (copia locale)', { resultState: 'local' }, true],
    ['risultato non più disponibile', { resultState: 'gone' }, false],
    ['nessun esito riuscito', { resultState: 'none' }, false],
    ['senza dashboard', { eseguibile: { ...ESE, has_dashboard: false } }, false],
    ['output a file del connettore', { eseguibile: { ...ESE, output_is_file_based: true } }, false],
    ['senza esecuzione', { taskId: null }, false],
    ['senza utente', { user: null }, false],
    ['senza Eseguibile', { eseguibile: null }, false],
  ])('%s', (_, change, expected) => {
    expect(Boolean(dashboardTarget({ ...base, ...change }))).toBe(expected);
  });

  it('gli stati apribili sono esattamente quelli con il risultato ancora qui', () => {
    expect([...OPENABLE_RESULT_STATES].sort()).toEqual(['local', 'on_server']);
  });

  it('avvisi solo a chi può modificare (stessa regola, flag del server)', () => {
    const t = (user, can_edit = true) => dashboardTarget({ user, eseguibile: { ...ESE, can_edit }, taskId: 't1', resultState: 'local' }).warnings;
    expect(t(esecutore)).toEqual([]);
    expect(t(admin)).toHaveLength(1);
    expect(t(admin, false)).toEqual([]);
    expect(t(superadmin)).toHaveLength(1);
    expect(dashboardWarningsText(ESE.dashboard_warnings)).toBe('Dashboard da controllare: «zona»: non c\'è più');
    expect(dashboardWarningsText([])).toBeNull();
  });

  it('funzione attiva dalla lista: sì/no se almeno uno lo dice, null se vuota', () => {
    expect(dashboardFeatureFromList([])).toBeNull();
    expect(dashboardFeatureFromList([{ dashboard_feature_enabled: false }])).toBe(false);
    expect(dashboardFeatureFromList([{ dashboard_feature_enabled: true }])).toBe(true);
  });

  it('solo percorsi relativi sotto /grafana/', () => {
    expect(safeGrafanaPath('/grafana/d/u1?orgId=1&kiosk&var-exec=t1&var-pass=p')).toBeTruthy();
    for (const bad of ['https://evil.example/grafana/d/x', '//evil.example/grafana/', '/api/x', 'javascript:alert(1)', '/grafana//x', '/grafana/\\x', null, 3]) {
      expect(safeGrafanaPath(bad), String(bad)).toBeNull();
    }
  });
});

describe('2. enumerazione dei percorsi', () => {
  const files = sources();
  it('ogni finestra dei risultati di un Eseguibile riceve il bersaglio dall\'hook comune', () => {
    const users = files.filter((f) => f !== 'components/eseguibili/useEseguibileRun.js' && /from '\.\/useEseguibileRun'|useEseguibileRun'/.test(read(f)));
    expect(users.sort()).toEqual(['components/eseguibili/EseguibileExecuteWizard.jsx', 'components/eseguibili/ExecutableDynamicRunForm.jsx']);
    for (const f of users) {
      const src = read(f);
      expect(src, f).toMatch(/useEseguibileRun\(\{ eseguibileId[^}]*\beseguibile\b/);
      expect(src.includes('<DashboardLauncher target={run.dashboard} />') || src.includes('dashboard={run.dashboard}'), f).toBe(true);
    }
    expect(read('components/modals/ExecutionModal.jsx')).toContain('<DashboardLauncher target={dashboard} />');
  });

  it('chi monta la finestra la apre sempre da EseguibileExecuteWizard', () => {
    const mounts = files.filter((f) => read(f).includes('<EseguibileExecuteWizard'));
    expect(mounts.sort()).toEqual(['pages/EsecutoreHome.jsx', 'pages/EseguibiliList.jsx', 'pages/ProjectList.jsx']);
  });

  it('pulsante, iframe e voci solo nei posti dichiarati', () => {
    const where = (re) => files.filter((f) => re.test(read(f))).sort();
    // Etichette scritte come testo (JSX) o stringa, non nei commenti.
    const label = (text) => new RegExp(`(>\\s*|['\`])📊 ${text}`);
    expect(where(label('Apri dashboard'))).toEqual(['components/eseguibili/DashboardLauncher.jsx']);
    expect(where(/<iframe\b/)).toEqual(['components/eseguibili/DashboardLauncher.jsx']);
    expect(where(label('Crea dashboard'))).toEqual(['utils/nodeActions.js']);
    expect(where(label('Modifica in Grafana'))).toEqual(['components/eseguibili/DashboardLauncher.jsx', 'utils/nodeActions.js']);
    expect(where(/<DashboardBadge\b/)).toEqual(['components/projects/EseguibileCard.jsx', 'pages/EsecutoreHome.jsx', 'pages/EseguibiliList.jsx']);
  });

  it('le API della dashboard solo dai gestori comuni e dall\'hook', () => {
    const re = /\b(createEseguibileDashboard|deleteEseguibileDashboard|getEseguibileDashboardEditLink|issueEseguibileDashboardPass|openGrafanaSession|closeGrafanaSession)\b/;
    const users = files.filter((f) => !f.startsWith('api/') && re.test(read(f))).sort();
    expect(users).toEqual(['context/AuthContext.jsx', 'hooks/useDashboardPass.js', 'utils/dashboardActions.js']);
  });

  it('il menu «⋯» della pagina Eseguibili viene dalla tabella', () => {
    const esl = read('pages/EseguibiliList.jsx');
    expect(esl).toContain('panelActions(ESEGUIBILE_NODE');
    expect(esl).not.toMatch(/label: '(🔗 Condividi|✎ Rinomina|🗑 Elimina)'/);
  });
});

describe('3. voci della dashboard nella tabella', () => {
  const node = { kind: 'eseguibile' };
  const all = Object.fromEntries(NODE_ACTIONS.eseguibile.map((a) => [a.id, () => a.id]));
  const dash = (user, item) => panelActions(node, { user, item, handlers: all }).map((a) => a.id).filter((id) => /Dashboard$/.test(id));
  const on = { id: 'x', can_edit: true, dashboard_feature_enabled: true };
  it.each([
    ['admin senza dashboard', admin, on, ['createDashboard']],
    ['admin con dashboard', admin, { ...on, has_dashboard: true }, ['editDashboard', 'deleteDashboard']],
    ['superadmin con dashboard', superadmin, { ...on, has_dashboard: true }, ['editDashboard', 'deleteDashboard']],
    ['admin su Eseguibile bloccato', admin, { ...on, can_edit: false, has_dashboard: true }, []],
    ['esecutore', esecutore, { ...on, has_dashboard: true }, []],
    ['user', { role: 'user', user_id: 'u' }, on, []],
    ['funzione spenta', admin, { ...on, dashboard_feature_enabled: false }, []],
  ])('%s', (_, user, item, expected) => {
    expect(dash(user, item)).toEqual(expected);
  });
});

describe('4. sessione, lasciapassare e rinnovo (useDashboardPass)', () => {
  const NOW = Date.UTC(2026, 10, 11, 9, 0, 0);
  const pass = (n, { ttl = 1800, data = 1800 } = {}) => ({
    task_id: 't1', dashboard_uid: 'u1', pass_token: `p${n}`,
    expires_at: Math.floor(Date.now() / 1000) + ttl,
    data_expires_at: Math.floor(NOW / 1000) + data,
    iframe_path: `/grafana/d/u1?orgId=1&kiosk&var-exec=t1&var-pass=p${n}`,
  });
  const target = { eseguibileId: 'x1', taskId: 't1', item: { id: 'x1', name: 'Valori OMI' }, warnings: [] };
  let calls;
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(NOW);
    calls = [];
    api.openGrafanaSession.mockReset().mockImplementation(async () => { calls.push('session'); });
    api.issueEseguibileDashboardPass.mockReset();
  });
  afterEach(() => { vi.useRealTimers(); });
  const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }); };

  it('nextRenewal: ogni 20 min o un minuto prima della scadenza; alla fine dei dati, fine', () => {
    const s = (sec) => Math.floor(NOW / 1000) + sec;
    expect(nextRenewal({ expires_at: s(1800), data_expires_at: s(3600) }, NOW).at).toBe(NOW + RENEW_EVERY_MS);
    expect(nextRenewal({ expires_at: s(600), data_expires_at: s(3600) }, NOW).at).toBe(NOW + 600_000 - RENEW_MARGIN_MS);
    expect(nextRenewal({ expires_at: s(900), data_expires_at: s(900) }, NOW)).toEqual({ at: NOW + 900_000, expire: true });
  });

  it('prima la sessione, poi il lasciapassare; rinnovo di entrambi con il nuovo iframe; fine dei dati', async () => {
    let n = 0;
    api.issueEseguibileDashboardPass.mockImplementation(async (id, task) => { calls.push(`pass:${id}:${task}`); n += 1; return pass(n, { data: 1800 + 1500 }); });
    const { result, unmount } = renderHook(() => useDashboardPass({ user: esecutore, target, open: true }));
    await flush();
    expect(calls).toEqual(['session', 'pass:x1:t1']);
    expect(result.current.status).toBe('ready');
    expect(result.current.iframePath).toContain('var-pass=p1');
    await act(async () => { vi.advanceTimersByTime(RENEW_EVERY_MS); });
    await flush();
    expect(calls).toEqual(['session', 'pass:x1:t1', 'session', 'pass:x1:t1']);
    expect(result.current.iframePath).toContain('var-pass=p2');
    unmount();
    await act(async () => { vi.advanceTimersByTime(RENEW_EVERY_MS * 3); });
    expect(calls).toHaveLength(4);
  });

  it('lasciapassare fino alla fine dei dati: a quell\'ora «non più disponibili», nessun rinnovo', async () => {
    api.issueEseguibileDashboardPass.mockImplementation(async () => pass(1, { ttl: 600, data: 600 }));
    const { result } = renderHook(() => useDashboardPass({ user: esecutore, target, open: true }));
    await flush();
    expect(result.current.status).toBe('ready');
    await act(async () => { vi.advanceTimersByTime(600_000); });
    expect(result.current.status).toBe('expired');
    expect(result.current.iframePath).toBeNull();
    expect(api.issueEseguibileDashboardPass).toHaveBeenCalledTimes(1);
  });

  it('410 = dati non più disponibili; indirizzo fuori da /grafana/ = errore, mai nell\'iframe', async () => {
    api.issueEseguibileDashboardPass.mockRejectedValueOnce({ response: { status: 410, data: { error: 'dashboard_data_unavailable' } } });
    const first = renderHook(() => useDashboardPass({ user: esecutore, target, open: true }));
    await flush();
    expect(first.result.current.status).toBe('expired');
    first.unmount();
    api.issueEseguibileDashboardPass.mockResolvedValueOnce({ ...pass(1), iframe_path: 'https://evil.example/grafana/d/u1' });
    const second = renderHook(() => useDashboardPass({ user: esecutore, target, open: true }));
    await flush();
    expect(second.result.current.status).toBe('error');
    expect(second.result.current.iframePath).toBeNull();
  });

  it('chiuso o senza permesso: nessuna richiesta', async () => {
    renderHook(() => useDashboardPass({ user: esecutore, target, open: false }));
    renderHook(() => useDashboardPass({ user: null, target, open: true }));
    await flush();
    expect(api.openGrafanaSession).not.toHaveBeenCalled();
    expect(api.issueEseguibileDashboardPass).not.toHaveBeenCalled();
  });
});

describe('5. finestra della dashboard', () => {
  const target = { eseguibileId: 'x1', taskId: 't1', item: { id: 'x1', name: 'Valori OMI', can_edit: true }, warnings: [] };
  beforeEach(() => {
    auth.user = esecutore;
    api.openGrafanaSession.mockReset().mockResolvedValue(undefined);
    api.issueEseguibileDashboardPass.mockReset().mockResolvedValue({
      expires_at: Math.floor(Date.now() / 1000) + 1800, data_expires_at: Math.floor(Date.now() / 1000) + 1800,
      iframe_path: '/grafana/d/u1?orgId=1&kiosk&var-exec=t1&var-pass=p1',
    });
  });

  it('senza bersaglio nessun pulsante', () => {
    const { container } = render(<DashboardLauncher target={null} />);
    expect(container.textContent).toBe('');
  });

  it('iframe stesso origine, sandbox, niente referrer, titolo; si chiude quando il bersaglio sparisce', async () => {
    const { rerender } = render(<DashboardLauncher target={target} />);
    fireEvent.click(screen.getByRole('button', { name: /Apri dashboard/ }));
    const frame = await waitFor(() => {
      const f = document.querySelector('iframe');
      if (!f) throw new Error('iframe assente');
      return f;
    });
    expect(frame.getAttribute('src')).toBe('/grafana/d/u1?orgId=1&kiosk&var-exec=t1&var-pass=p1');
    expect(frame.getAttribute('sandbox')).toBe(DASHBOARD_IFRAME_SANDBOX);
    expect(DASHBOARD_IFRAME_SANDBOX).not.toMatch(/allow-top-navigation/);
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(frame.getAttribute('title')).toMatch(/Valori OMI/);
    expect(frame.hasAttribute('srcdoc')).toBe(false);
    // L'esecutore non modifica la dashboard.
    expect(screen.queryByRole('button', { name: /Modifica in Grafana/ })).toBeNull();
    rerender(<DashboardLauncher target={null} />);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('chi può modificare vede «Modifica in Grafana» e gli avvisi', async () => {
    auth.user = admin;
    render(<DashboardLauncher target={{ ...target, warnings: [{ column: 'zona', motivo: 'colonna tolta' }] }} />);
    expect(screen.getByText(/Dashboard da controllare in Grafana/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Apri dashboard/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Modifica in Grafana/ })).toBeTruthy());
  });
});

describe('6. «Modifica in Grafana»', () => {
  const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
  const item = { id: 'x1', name: 'Valori OMI' };
  let tab;
  let order;
  beforeEach(() => {
    order = [];
    tab = { closed: false, opener: {}, location: { replace: vi.fn() }, close: vi.fn() };
    vi.spyOn(window, 'open').mockImplementation(() => { order.push('open'); return tab; });
    api.getEseguibileDashboardEditLink.mockReset().mockImplementation(async () => { order.push('link'); return { edit_path: '/grafana/d/u1', dashboard_warnings: [] }; });
    api.openGrafanaSession.mockReset().mockImplementation(async () => { order.push('session'); });
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it('scheda aperta al clic, poi link, poi sessione; senza opener', async () => {
    expect(await openDashboardEditor({ toast, item, taskId: 't1' })).toBe(true);
    expect(order).toEqual(['open', 'link', 'session']);
    expect(api.getEseguibileDashboardEditLink).toHaveBeenCalledWith('x1', { taskId: 't1' });
    expect(tab.opener).toBeNull();
    expect(tab.location.replace).toHaveBeenCalledWith('/grafana/d/u1');
  });

  it('indirizzo fuori da /grafana/: niente sessione, scheda chiusa, errore', async () => {
    api.getEseguibileDashboardEditLink.mockResolvedValueOnce({ edit_path: 'https://evil.example/' });
    expect(await openDashboardEditor({ toast, item })).toBe(false);
    expect(api.openGrafanaSession).not.toHaveBeenCalled();
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.replace).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });
});

describe('7. logout, invalidazioni, errori', () => {
  it('logout: DELETE /grafana/session keepalive (AuthContext lo chiama)', () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ status: 204 }));
    vi.stubGlobal('fetch', fetchSpy);
    sessionStorage.setItem('urby_token', 'tok-1');
    try {
      expect(closeGrafanaSession()).toBe(true);
      const [url, opts] = fetchSpy.mock.calls[0];
      expect([url.replace(/^.*\/api/, ''), opts.method, opts.keepalive]).toEqual(['/grafana/session', 'DELETE', true]);
    } finally {
      vi.unstubAllGlobals();
      sessionStorage.removeItem('urby_token');
    }
    expect(read('context/AuthContext.jsx')).toContain('closeGrafanaSession()');
  });

  it('crea/elimina aggiornano lista e home; lasciapassare, link e sessione nessuna lista', () => {
    expect(invalidationsForWrite('post', '/eseguibili/x1/dashboard').sort()).toEqual(['dashboardGraph', 'eseguibili']);
    expect(invalidationsForWrite('delete', '/eseguibili/x1/dashboard').sort()).toEqual(['dashboardGraph', 'eseguibili']);
    for (const [m, u] of [['post', '/eseguibili/x1/dashboard/pass'], ['post', '/eseguibili/x1/dashboard/edit-link'], ['post', '/grafana/session'], ['delete', '/grafana/session']]) {
      expect(invalidationsForWrite(m, u), `${m} ${u}`).toEqual([]);
    }
  });

  it('ogni codice nuovo del backend ha il suo messaggio italiano', () => {
    const codes = fs.readFileSync(path.resolve(SRC, '../../backend/app/shared/error_codes.py'), 'utf-8');
    for (const code of ['grafana_disabled', 'grafana_unavailable', 'dashboard_not_found', 'dashboard_already_exists', 'dashboard_data_unavailable', 'dashboard_pass_invalid']) {
      expect(codes).toContain(`"${code}"`);
      expect(ERROR_MESSAGES[code]?.message, code).toBeTruthy();
    }
  });
});
