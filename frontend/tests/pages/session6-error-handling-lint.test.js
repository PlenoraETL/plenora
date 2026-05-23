/**
 * Sessione 6 P1 — Architectural lint sui catch ignored + password validation.
 *
 * Bug coperti:
 * - ConnectionManager.jsx:69-84 → catch con toast invece di silent console.error
 * - ApiConnectionManager.jsx:136-139 → idem
 * - ScheduleList.jsx loadData → idem
 * - ScheduleList.jsx handleDelete → branching su 404/409
 * - UserManager.jsx:174 → password validation < 12 (era < 6)
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


function readSrc(relPath) {
  return readFileSync(resolve(__dirname, '../../src', relPath), 'utf-8');
}


describe('sessione 6 frontend error handling lint', () => {
  it('ConnectionManager loadConnections usa toast.warning su partial failure', () => {
    const src = readSrc('pages/ConnectionManager.jsx');
    const idx = src.indexOf('const loadConnections =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 2500);

    // Anti-regression: niente silent .catch(() => ({connections: []}))
    expect(window).toContain('Promise.allSettled');
    expect(window).toContain('toast.warning');
  });

  it('ApiConnectionManager loadApiConnections usa toast.error nel catch', () => {
    const src = readSrc('pages/ApiConnectionManager.jsx');
    const idx = src.indexOf('const loadApiConnections =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 600);

    expect(window).toContain('toast.error');
    // No piu' solo console.error silenzioso
    const consoleErrorOnly = /catch\s*\(e\)\s*\{\s*console\.error\(e\);\s*\}/;
    expect(window).not.toMatch(consoleErrorOnly);
  });

  it('ScheduleList loadData usa toast.error nel catch', () => {
    const src = readSrc('pages/ScheduleList.jsx');
    const idx = src.indexOf('const loadData =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 2500);
    expect(window).toContain('toast.error');
  });

  it('ScheduleList handleDelete branching su 404/409', () => {
    const src = readSrc('pages/ScheduleList.jsx');
    const idx = src.indexOf('const handleDelete =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 1500);

    expect(window).toContain('status === 404');
    expect(window).toContain('status === 409');
    expect(window).toContain('toast.warning');
  });

  it('UserManager password validation usa < 12 char', () => {
    const src = readSrc('pages/UserManager.jsx');
    // Pattern: deve esserci `newPassword.length < 12`, NON `< 6`
    expect(src).toContain('newPassword.length < 12');
    expect(src).toContain('Minimo 12 caratteri');
    // Anti-regression: no piu' < 6 hardcoded
    expect(src).not.toMatch(/newPassword\.length\s*<\s*6\b/);
  });
});
