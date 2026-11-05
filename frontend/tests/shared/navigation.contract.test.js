/**
 * Contract di navigazione (Fase 5, T4; R7.1, R7.4, R7.5).
 *  - un solo blocker (NavigationGuard) in un data router; nessun BrowserRouter;
 *  - gli editor non chiedono a mano prima di navigare (lo fa il guard: niente
 *    doppia domanda); il logout chiede (SaveRules);
 *  - una sola mappa tipo -> editor (utils/projectRoutes), uguale alle Route;
 *    nessun percorso di editor costruito altrove;
 *  - "Indietro" solo da utils/navigation.goBack (un link diretto non esce
 *    dall'app); rimando a /login con `state.from`; pagina 404.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';

import { PROJECT_EDITOR_ROUTES, OPEN_PROJECT_ROUTE, projectEditorPath, openProjectPath } from '../../src/utils/projectRoutes';
import { goBack, returnPathAfterLogin } from '../../src/utils/navigation';

const SRC = path.resolve(import.meta.dirname, '../../src');
function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return files(full);
    return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
  });
}
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const ALL = files(SRC).map(f => ({ file: rel(f), code: fs.readFileSync(f, 'utf-8') }));
const app = fs.readFileSync(path.join(SRC, 'App.jsx'), 'utf-8');
const routes = [...app.matchAll(/<Route\s+path="([^"]+)"/g)].map(m => m[1]);
const usersOf = (re) => ALL.filter(({ code }) => re.test(code)).map(({ file }) => file).sort();

describe('guard di navigazione unico', () => {
  it('data router, un solo useBlocker, nessun BrowserRouter', () => {
    expect(app).toMatch(/createBrowserRouter\(/);
    expect(app).toMatch(/<NavigationGuard \/>/);
    expect(usersOf(/\bBrowserRouter\b/)).toEqual([]);
    expect(usersOf(/\buseBlocker\(/)).toEqual(['components/common/NavigationGuard.jsx']);
  });

  it('gli editor navigano senza chiedere a mano (chiede il guard)', () => {
    expect(usersOf(/if\s*\(\s*(await\s+)?confirmNavigation\(\)\s*\)\s*navigate\(/)).toEqual([]);
  });
});

describe('mappa unica tipo -> editor (R7.4)', () => {
  it('ogni rotta della mappa e la rotta di apertura sono Route di App.jsx', () => {
    for (const r of [...Object.values(PROJECT_EDITOR_ROUTES), OPEN_PROJECT_ROUTE]) expect(routes).toContain(r);
  });

  it('nessun percorso di editor costruito fuori da utils/projectRoutes', () => {
    const offenders = ALL.filter(({ file, code }) => file !== 'utils/projectRoutes.js' && file !== 'App.jsx'
      && /[`'"]\/(project|editor\/manipola|arricchisci)\/(\$\{|[a-zA-Z0-9])/.test(code)).map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('tipo -> rotta; i log aprono dalla rotta che legge il tipo', () => {
    expect(projectEditorPath({ id: 'a', project_type: 'manipola' })).toBe('/editor/manipola/a');
    expect(projectEditorPath({ id: 'a', project_type: 'arricchisci' })).toBe('/arricchisci/a');
    expect(projectEditorPath({ id: 'a', project_type: 'estrai' })).toBe('/project/a');
    expect(projectEditorPath({ id: 'a' })).toBe('/project/a');
    expect(openProjectPath('a')).toBe('/open/a');
    const logViewer = ALL.find(f => f.file === 'pages/LogViewer.jsx').code;
    expect(logViewer).toMatch(/openProjectPath\(entry\.project_id\)/);
  });
});

describe('Indietro, login e 404 (R7.5)', () => {
  it('navigate(-1) solo in utils/navigation.goBack', () => {
    expect(usersOf(/navigate\(\s*-1\s*\)/)).toEqual(['utils/navigation.js']);
  });

  it('goBack: con una pagina precedente torna indietro, da link diretto va al ripiego', () => {
    const navigate = vi.fn();
    window.history.replaceState({ idx: 0 }, '');
    goBack(navigate, '/files');
    expect(navigate).toHaveBeenLastCalledWith('/files', { replace: true });
    window.history.replaceState({ idx: 2 }, '');
    goBack(navigate, '/files');
    expect(navigate).toHaveBeenLastCalledWith(-1);
    window.history.replaceState(null, '');
  });

  it('dopo il login si torna alla pagina richiesta (se si puo\' vedere)', () => {
    expect(returnPathAfterLogin({ pathname: '/logs', search: '?x=1' }, '/')).toBe('/logs?x=1');
    expect(returnPathAfterLogin(null, '/')).toBe('/');
    expect(returnPathAfterLogin({ pathname: '/login' }, '/')).toBe('/');
    expect(returnPathAfterLogin('//evil.example', '/')).toBe('/');
    expect(returnPathAfterLogin({ pathname: '/logs' }, '/run', { esecutore: true })).toBe('/run');
    expect(returnPathAfterLogin({ pathname: '/run' }, '/run', { esecutore: true })).toBe('/run');
  });

  it('i rimandi a /login ricordano la pagina; il catch-all mostra una 404', () => {
    expect(app).not.toMatch(/<Navigate to="\/login" replace \/>/);
    expect(app).toMatch(/state=\{loginRedirectState\(location\)\}/);
    expect(app).toMatch(/<Route path="\*" element=\{<ProtectedRoute><NotFoundPage \/><\/ProtectedRoute>\} \/>/);
  });

  it('R4.6: ogni rotta con :projectId rimonta la pagina per progetto (PerProject)', () => {
    const withParam = [...app.matchAll(/<Route\s+path="([^"]*:projectId[^"]*)"\s+element=\{(.*)\}\s*\/>/g)];
    expect(withParam.length).toBeGreaterThanOrEqual(4);
    for (const [, route, element] of withParam) expect(element, route).toMatch(/<PerProject>/);
  });
});
