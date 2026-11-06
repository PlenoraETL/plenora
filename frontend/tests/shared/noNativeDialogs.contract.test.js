/**
 * Contract (Fase 5, T4; R6.10): nessun dialog nativo del browser.
 * window.confirm / alert / prompt sono vietati in src: si usa il dialog del
 * progetto (ConfirmProvider in App.jsx, useConfirm*, useConfirmModal).
 *
 * Unica eccezione (non e' una chiamata): il prompt di `beforeunload` in
 * utils/leaveGuard.js (unico per tutta l'app, conferma finale G8), perche' alla chiusura/ricarica della
 * scheda i browser non consentono un dialog proprio.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const SRC = path.resolve(import.meta.dirname, '../../src');
function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return files(full);
    return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
  });
}
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
// Senza commenti: la documentazione puo' nominarli.
const code = (f) => fs.readFileSync(f, 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const ALL = files(SRC).map(f => ({ file: rel(f), code: code(f) }));

describe('nessun dialog nativo', () => {
  it('niente window/globalThis/self .confirm/.alert/.prompt', () => {
    const offenders = ALL.filter(({ code: c }) => /\b(window|globalThis|self)\s*\.\s*(confirm|alert|prompt)\b/.test(c))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('niente alert()/prompt() globali', () => {
    const offenders = ALL.filter(({ code: c }) => /(^|[^\w.$])(alert|prompt)\s*\(/.test(c)).map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('confirm(...) solo se e\' una funzione del file (il dialog del progetto)', () => {
    const offenders = ALL.filter(({ code: c }) => /(^|[^\w.$])confirm\s*\(/.test(c)
      && !/(const|let|var)\s+confirm\b|[{,(]\s*confirm\s*[,})=:]|function\s+confirm\b|\bconfirm\s*=\s*(use|async|\()/.test(c))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it('il ConfirmProvider avvolge tutta l\'app', () => {
    const app = fs.readFileSync(path.join(SRC, 'App.jsx'), 'utf-8');
    expect(app).toMatch(/<ConfirmProvider>[\s\S]*<AppRoutes \/>[\s\S]*<\/ConfirmProvider>/);
  });
});
