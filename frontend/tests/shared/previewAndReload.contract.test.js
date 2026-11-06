import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { formatCellNumber, formatPreviewValue, PREVIEW_ROW_LIMIT } from '../../src/utils/previewFormat';
import React from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { lazyPage, reloadForNewBuild } from '../../src/utils/chunkReload';
import { markLeaveConfirmed, registerLeaveProbe, isLeaveConfirmed, confirmLeave } from '../../src/utils/leaveGuard';
import { sourceFiles } from '../helpers/sourceScan.js';

/** Contract: formato delle anteprime e ripresa dopo un deploy. */
describe('preview format', () => {
  it('rounds binary decimals, leaves integers and text alone', () => {
    expect(formatCellNumber(58.333333333336)).toBe('58,3333');
    expect(formatCellNumber(2026)).toBe('2026');
    expect(formatCellNumber(1234567)).toBe('1234567');
    expect(formatPreviewValue('092_01283')).toBe('092_01283');
    expect(formatPreviewValue(null)).toBe('—');
    expect(formatPreviewValue({ a: 1 })).toBe('{"a":1}');
  });

  it('step and modal previews ask for the same rows', () => {
    const src = (p) => fs.readFileSync(path.resolve(import.meta.dirname, '../../src', p), 'utf-8');
    expect(src('components/canvas/StepPreview.jsx')).toMatch(/run\(PREVIEW_ROW_LIMIT/);
    expect(src('hooks/usePreview.js')).toMatch(/limit = PREVIEW_ROW_LIMIT/);
    expect(PREVIEW_ROW_LIMIT).toBe(100);
  });
});

function fakeWindow() {
  const store = {};
  return {
    sessionStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    location: { reload: vi.fn() },
  };
}

// Il caricatore di una pagina fallisce come dopo un deploy; si monta la pagina.
class Boundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() { return this.state.error ? React.createElement('p', null, 'errore') : this.props.children; }
}
async function openPage(win) {
  const Page = lazyPage(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module')), win);
  let view;
  await act(async () => {
    view = render(React.createElement(Boundary, null,
      React.createElement(React.Suspense, { fallback: React.createElement('p', null, 'caricamento') }, React.createElement(Page))));
  });
  await act(async () => { await new Promise(r => setTimeout(r, 0)); });
  return view.container.textContent;
}

/*
 * UI conferma C-K1: dopo un deploy ricarica SOLO la navigazione verso una
 * pagina (lazyPage), una volta ogni 30 s, e mai con modifiche non salvate
 * senza che l'uscita sia stata confermata. Il prefetch fallito si ignora.
 */
describe('chunk reload after a deploy: only a real navigation', () => {
  let unregister = null;
  // La pagina che non si carica arriva all'ErrorBoundary: React lo scrive in console.
  beforeEach(() => { globalThis.__frontendConsolePolicy.allowError = true; });
  afterEach(() => { cleanup(); unregister?.(); unregister = null; });

  it('a failed page import reloads once (loading stays), then lets the error through', async () => {
    const win = fakeWindow();
    expect(await openPage(win)).toBe('caricamento');
    expect(win.location.reload).toHaveBeenCalledTimes(1);
    expect(await openPage(win)).toBe('errore');
    expect(win.location.reload).toHaveBeenCalledTimes(1);
  });

  it('never reloads when the "already reloaded" mark cannot be stored (no loop)', async () => {
    const win = fakeWindow();
    win.sessionStorage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
    expect(await openPage(win)).toBe('errore');
    expect(win.location.reload).not.toHaveBeenCalled();
  });

  it('with unsaved changes it reloads only after the leave question was answered', async () => {
    unregister = registerLeaveProbe(() => true);
    const win = fakeWindow();
    expect(reloadForNewBuild(win)).toBe(false);
    expect(await openPage(win)).toBe('errore');
    expect(win.location.reload).not.toHaveBeenCalled();
    // «Esci senza salvare» (NavigationGuard/useSignOut): la ricarica parte e
    // il prompt nativo non chiede di nuovo (isLeaveConfirmed).
    expect(await confirmLeave(async () => true)).toBe(true);
    expect(isLeaveConfirmed()).toBe(true);
    expect(await openPage(fakeWindow())).toBe('caricamento');
  });

  it('a confirmation covers only the editors mounted when it was given', () => {
    unregister = registerLeaveProbe(() => true);
    markLeaveConfirmed();
    expect(isLeaveConfirmed()).toBe(true);
    const other = registerLeaveProbe(() => true);
    expect(isLeaveConfirmed()).toBe(false);
    markLeaveConfirmed();
    other();
    expect(isLeaveConfirmed()).toBe(false);
  });

  it('no global preloadError handler: a failed prefetch is ignored and never reloads', async () => {
    const ev = new Event('vite:preloadError', { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    const code = Object.fromEntries(sourceFiles(/\.jsx?$/).map(f => [f.file, f.code]));
    expect(Object.entries(code).filter(([, c]) => /vite:preloadError/.test(c.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''))).map(([f]) => f)).toEqual([]);
    // Il prefetch non conosce la ricarica: la sua promessa fallita si ignora.
    const noComments = (c) => c.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
    expect(noComments(code['utils/editorChunks.js'])).not.toMatch(/chunkReload|location\.reload/);
    expect(code['utils/editorChunks.js']).toMatch(/\.catch\(\(\) => \{ prefetched\.delete\(type\); \}\)/);
    // Ogni pagina delle rotte passa da lazyPage; nessun React.lazy diretto in App.
    expect(code['App.jsx']).not.toMatch(/React\.lazy\(/);
    expect((code['App.jsx'].match(/lazyPage\(/g) || []).length).toBeGreaterThan(15);
    expect(code['index.jsx']).not.toMatch(/installChunkReload/);
  });
});
