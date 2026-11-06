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
  // Fase 5 T2 (R4.8): il caricamento passa da hooks/useResource e un errore
  // si mostra come errore con «Riprova» (ResourceError), mai lista vuota.
  it('ConnectionManager: allSettled, elenco incompleto e errore visibili', () => {
    const src = readSrc('pages/ConnectionManager.jsx');
    expect(src).toContain('useResource(');
    expect(src).toContain('Promise.allSettled');
    expect(src).toContain('title="Elenco incompleto"');
    expect(src).toContain('title="Connessioni non caricate"');
  });

  it('ApiConnectionManager: errore di caricamento con Riprova', () => {
    const src = readSrc('pages/ApiConnectionManager.jsx');
    // UI-1: lista condivisa (useSharedResource, sopra useResource).
    expect(src).toMatch(/useSharedResource\(|useResource\(/);
    expect(src).toContain('title="Connettori non caricati"');
  });

  it('ScheduleList: errore di caricamento con Riprova', () => {
    const src = readSrc('pages/ScheduleList.jsx');
    expect(src).toContain('useResource(');
    expect(src).toContain('title="Pianificazioni non caricate"');
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

  it('UserManager password validation usa la regola unica (12 caratteri)', () => {
    // Fase 5 T1: la soglia vive in utils/passwordRule (uguale al backend,
    // tests/shared/passwordRule.contract.test.js); qui solo che la usi.
    const src = readSrc('pages/UserManager.jsx');
    expect(src).toContain('passwordProblem(newPassword)');
    expect(src).not.toMatch(/newPassword.lengths*<s*d/);
  });
});
