/**
 * Sessione 10 cluster C (JSDoc) + D (race conditions).
 *
 * Bug coperti:
 * - carica.js postCaricaAutoMapping: JSDoc allineato a connection_id/schema_name/table_name
 * - ScheduleList.jsx: requestGenRef per scartare response stale su projectFilter change
 * - useProjectListData.js: Promise.allSettled + avviso dei servizi non caricati
 * - ManipolaEditor.jsx: nessun salvataggio automatico (era il mutex triggerAutosave)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


const { httpMock } = vi.hoisted(() => ({
  httpMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));


function readSrc(relPath) {
  return readFileSync(resolve(__dirname, '../../src', relPath), 'utf-8');
}


describe('Sessione 10 cluster CD', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('carica.js postCaricaAutoMapping JSDoc allineato ai nomi BE', () => {
    const src = readSrc('api/carica.js');
    const idx = src.indexOf('postCaricaAutoMapping');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(Math.max(0, idx - 1500), idx);
    // Pre-fix nomi NON devono essere documentati come field names
    // (possono apparire nei commenti "Pre-fix:" ma non come @param)
    expect(window).toMatch(/payload\.connection_id/);
    expect(window).toMatch(/payload\.schema_name/);
    expect(window).toMatch(/payload\.table_name/);
    // Anti-regression: il vecchio JSDoc non deve apparire come param
    expect(window).not.toMatch(/payload\.target_conn_id/);
    expect(window).not.toMatch(/payload\.target_schema/);
    expect(window).not.toMatch(/payload\.target_table/);
  });

  // Fase 5 T2: l'id di richiesta e' quello unico di hooks/useResource, con il
  // filtro di progetto nella chiave (una risposta superata non scrive).
  it('ScheduleList carica dall\'hook comune con il filtro nella chiave', () => {
    const src = readSrc('pages/ScheduleList.jsx');
    expect(src).toContain('useResource(');
    expect(src).toMatch(/key:\s*\['schedules',\s*projectFilter/);
    expect(src).not.toContain('requestGenRef');
  });

  it('useProjectListData usa Promise.allSettled e mostra i servizi non caricati (Fase 5 T2: avviso con Riprova)', () => {
    const src = readSrc('components/projects/useProjectListData.js');
    expect(src).toContain('Promise.allSettled');
    expect(src).toContain('failures:');
    expect(src).toContain('useResource');
    expect(readSrc('pages/ProjectList.jsx')).toContain('title="Alcuni dati non caricati"');
  });

  it('ManipolaEditor non salva da solo (Fase 4, M7): niente PUT concorrenti da sincronizzare', () => {
    // Il mutex dell'autosave non serve piu': l'editor non salva. Gli schemi li
    // aggiorna la sottoscrizione unica della fonte delle colonne (una
    // richiesta per piu' modifiche ravvicinate, Blocco A regola 4), non
    // l'editor.
    const src = readSrc('pages/ManipolaEditor.jsx');
    expect(src).not.toContain('handleSaveQuiet');
    expect(src).not.toContain('triggerAutosave');
    expect(src).not.toContain('refreshSchemas');
    expect(readSrc('store/useColumnStore.js')).toContain('COLUMN_SYNC_DELAY_MS');
  });
});
