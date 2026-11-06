/**
 * Contract della navigazione unica (UI-4, U23), per enumerazione sulla
 * tabella utils/navItems e sui ruoli.
 *
 *  1. La barra laterale e la palette Ctrl+K mostrano, per ogni ruolo, le
 *     stesse pagine con lo stesso nome e la stessa icona: quelle della
 *     tabella che il ruolo vede. Nessuna delle due ha una lista propria.
 *  2. Ogni destinazione della tabella e' una rotta di App.jsx.
 *  3. Ctrl+K cerca anche i progetti gia' scaricati (cache condivisa,
 *     risorsa `projects`), senza richieste, e apre l'editor giusto; una
 *     risposta arrivata dopo il logout non li mostra al login seguente e una
 *     rinomina o eliminazione li toglie (conferma C-K5).
 *  4. La barra si chiude a sole icone e lo ricorda (anche senza
 *     localStorage funzionante).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, act, cleanup, within } from '@testing-library/react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

const auth = vi.hoisted(() => ({ user: null }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ token: 't', user: auth.user, logout: () => {} }),
}));
vi.mock('../../src/api/tickets', () => ({ getTicketsCounts: vi.fn(() => Promise.resolve({ active_total: 0 })) }));
vi.mock('../../src/hooks/useSignOut', () => ({ useSignOut: () => () => {} }));
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../src/api/http', () => ({ default: http }));

import AppSidebar from '../../src/components/AppSidebar';
import CommandPalette from '../../src/components/common/CommandPalette';
import { NAV_ITEMS, visibleNavItems } from '../../src/utils/navItems';
import { SHARED_RESOURCES } from '../../src/utils/sharedResources';
import { defineSharedResources, readShared, invalidateAfterWrite } from '../../src/utils/sharedCache';
import { resetSession } from '../../src/utils/session';
import { LAYOUT_KEYS } from '../../src/hooks/useStoredState';
import { SRC, stripComments } from '../helpers/sourceScan.js';

const ROLES = ['viewer', 'editor', 'admin', 'superadmin'];

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.hash}</output>;
}

function mount(ui, initial = '/files') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes><Route path="*" element={<>{ui}<Where /></>} /></Routes>
    </MemoryRouter>,
  );
}

const openPalette = async () => {
  await act(async () => { fireEvent.keyDown(window, { key: 'k', ctrlKey: true }); });
  return screen.getByRole('dialog', { name: 'Palette comandi' });
};

beforeEach(() => { localStorage.clear(); });
afterEach(() => {
  cleanup(); resetSession(); vi.restoreAllMocks();
  defineSharedResources({ projects: SHARED_RESOURCES.projects });
});

// La lista dei progetti come la scarica la dashboard: nella cache condivisa.
let projectsLoad = null;
const useProjectsLoad = () => defineSharedResources({ projects: { load: () => projectsLoad(), freshMs: 30_000 } });
async function rememberProjects(projects) {
  projectsLoad = () => Promise.resolve({ projects });
  useProjectsLoad();
  await readShared('projects');
}

describe('barra laterale e palette: la stessa tabella', () => {
  it.each(ROLES)('%s: la barra mostra esattamente le voci della tabella che il ruolo vede', async (r) => {
    auth.user = { user_id: 1, username: 'u', role: r };
    mount(<AppSidebar />);
    const nav = screen.getByRole('navigation', { name: 'Navigazione' });
    const labels = within(nav).getAllByRole('button')
      .map(b => b.querySelector('.app-sidebar__item-label, .app-sidebar__user-name')?.textContent)
      .filter(Boolean)
      .filter(l => l !== 'u');
    expect(labels).toEqual(visibleNavItems(auth.user).map(i => i.label));
    await act(async () => {});
  });

  it.each(ROLES)('%s: la palette mostra le stesse pagine, con nome e icona della tabella', async (r) => {
    auth.user = { user_id: 1, username: 'u', role: r };
    mount(<CommandPalette />);
    const dialog = await openPalette();
    const items = within(dialog).getAllByRole('button').map(b => [
      b.querySelector('.cmdk__item-icon')?.textContent, b.querySelector('.cmdk__item-label')?.textContent,
    ]);
    const pages = visibleNavItems(auth.user).map(i => [i.icon, i.label]);
    expect(items.slice(0, pages.length)).toEqual(pages);
    expect(items.slice(pages.length).map(([, l]) => l)).toEqual(['Scorciatoie da tastiera']);
  });

  it('nessuna lista di pagine propria nella barra o nella palette', () => {
    for (const file of ['components/AppSidebar.jsx', 'components/common/CommandPalette.jsx']) {
      const code = stripComments(fs.readFileSync(path.join(SRC, file), 'utf-8'));
      expect({ file, paths: code.match(/path:\s*'\//g) || [] }).toEqual({ file, paths: [] });
      expect({ file, nav: /navigate\('\/[a-z]/.test(code) }).toEqual({ file, nav: false });
    }
  });

  it('chiavi e nomi unici; ogni destinazione e\' una rotta di App.jsx', () => {
    expect(new Set(NAV_ITEMS.map(i => i.key)).size).toBe(NAV_ITEMS.length);
    expect(new Set(NAV_ITEMS.map(i => i.label)).size).toBe(NAV_ITEMS.length);
    const app = fs.readFileSync(path.join(SRC, 'App.jsx'), 'utf-8');
    const routes = new Set([...app.matchAll(/<Route path="([^"]+)"/g)].map(m => m[1]));
    for (const item of NAV_ITEMS) {
      const p = item.path.split('#')[0] || '/';
      expect({ key: item.key, route: routes.has(p) }).toEqual({ key: item.key, route: true });
    }
  });
});

describe('palette: ricerca dei progetti', () => {
  it('trova i progetti gia\' scaricati senza richieste e apre l\'editor del tipo', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'editor' };
    await rememberProjects([
      { id: 'p1', name: 'Catasto Campania', project_type: 'manipola', canvas: { big: true } },
      { id: 'p2', name: 'Ticket aperti', project_type: 'estrai' },
    ]);
    mount(<CommandPalette />);
    const dialog = await openPalette();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'catasto' } });
    const item = within(dialog).getByRole('button', { name: /Catasto Campania/ });
    expect(item.textContent).toContain('Progetto Manipola');
    await act(async () => { fireEvent.click(item); });
    expect(screen.getByTestId('where').textContent).toBe('/editor/manipola/p1');
    expect(http.get).not.toHaveBeenCalled();
  });

  it('senza ricerca nessun progetto; a fine sessione la lista si svuota', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'editor' };
    await rememberProjects([{ id: 'p1', name: 'Catasto', project_type: 'estrai' }]);
    mount(<CommandPalette />);
    let dialog = await openPalette();
    expect(within(dialog).queryByText('Catasto')).toBeNull();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'catasto' } });
    expect(within(dialog).getByText('Catasto')).toBeInTheDocument();
    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
    resetSession();
    dialog = await openPalette();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'catasto' } });
    expect(within(dialog).queryByText('Catasto')).toBeNull();
  });
});

describe('palette: progetti di un\'altra sessione o superati (conferma C-K5)', () => {
  it('una risposta arrivata dopo il logout non mostra i progetti di A a B', async () => {
    auth.user = { user_id: 2, username: 'b', role: 'editor' };
    let release;
    projectsLoad = () => new Promise((res) => { release = () => res({ projects: [{ id: 'pa', name: 'Segreto di A', project_type: 'estrai' }] }); });
    useProjectsLoad();
    const pending = readShared('projects');
    resetSession();
    release();
    await pending;
    mount(<CommandPalette />);
    const dialog = await openPalette();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'segreto' } });
    expect(within(dialog).queryByText('Segreto di A')).toBeNull();
  });

  it('rinomina ed eliminazione: il nome di prima non resta nella palette', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'editor' };
    await rememberProjects([{ id: 'p1', name: 'Vecchio nome', project_type: 'estrai' }]);
    invalidateAfterWrite('put', '/projects/p1');
    mount(<CommandPalette />);
    let dialog = await openPalette();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'vecchio' } });
    expect(within(dialog).queryByText('Vecchio nome')).toBeNull();
    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
    await rememberProjects([{ id: 'p2', name: 'Da eliminare', project_type: 'estrai' }]);
    invalidateAfterWrite('delete', '/projects/p2');
    dialog = await openPalette();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'eliminare' } });
    expect(within(dialog).queryByText('Da eliminare')).toBeNull();
  });

  it('nessun modulo tiene una copia propria dei progetti per la palette', () => {
    expect(fs.existsSync(path.join(SRC, 'utils/knownProjects.js'))).toBe(false);
    const palette = stripComments(fs.readFileSync(path.join(SRC, 'components/common/CommandPalette.jsx'), 'utf-8'));
    expect(palette).toMatch(/peekShared\('projects'\)/);
  });
});

describe('barra laterale richiudibile (U26)', () => {
  it('si chiude a sole icone e resta chiusa al montaggio successivo', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'editor' };
    const { unmount } = mount(<AppSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /Chiudi la barra laterale/ }));
    expect(screen.getByRole('navigation').className).toContain('app-sidebar--collapsed');
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEYS.appSidebarCollapsed))).toBe(true);
    unmount();
    mount(<AppSidebar />);
    expect(screen.getByRole('navigation').className).toContain('app-sidebar--collapsed');
    await act(async () => {});
  });

  // UI conferma C-K7: a barra chiusa si nascondono solo i testi, mai un
  // comando («Cambia password» non si raggiungeva). Le classi nascoste si
  // leggono dal CSS; ogni pulsante o link della barra chiusa e i suoi
  // contenitori non ne hanno nessuna, e hanno un nome accessibile.
  it('a barra chiusa ogni comando resta raggiungibile (nessun comando nascosto dal CSS)', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'admin' };
    const css = stripComments(fs.readFileSync(path.join(SRC, 'components/AppSidebar.css'), 'utf-8'));
    const hidden = new Set();
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/display\s*:\s*none/.test(m[2])) continue;
      for (const sel of m[1].split(',')) {
        const s = sel.trim().match(/^\.app-sidebar--collapsed\s+\.([\w-]+)$/);
        if (s) hidden.add(s[1]);
      }
    }
    expect(hidden.size).toBeGreaterThan(0);
    mount(<AppSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /Chiudi la barra laterale/ }));
    const nav = screen.getByRole('navigation');
    const commands = [...nav.querySelectorAll('button, a[href]')];
    expect(commands.length).toBeGreaterThan(5);
    const unreachable = commands.filter((el) => {
      for (let n = el; n && n !== nav; n = n.parentElement) {
        if ([...n.classList].some(c => hidden.has(c))) return true;
      }
      return false;
    }).map(el => el.getAttribute('aria-label') || el.title || el.textContent);
    expect(unreachable).toEqual([]);
    expect(commands.filter(el => !(el.getAttribute('aria-label') || el.title || el.textContent.trim()))).toEqual([]);
    expect(screen.getByRole('button', { name: 'Cambia password' })).toBeInTheDocument();
    await act(async () => {});
  });

  it('senza localStorage funziona lo stesso (stato della sola pagina)', async () => {
    auth.user = { user_id: 1, username: 'u', role: 'editor' };
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('bloccato'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('bloccato'); });
    mount(<AppSidebar />);
    fireEvent.click(screen.getByRole('button', { name: /Chiudi la barra laterale/ }));
    expect(screen.getByRole('navigation').className).toContain('app-sidebar--collapsed');
    await act(async () => {});
  });
});
