/**
 * Contract (Fase 5, voce S3): l'esito del «Test connessione» salvato dal
 * backend (`last_test` negli elementi delle liste) si mostra con UNA regola
 * (utils/connectionTestStatus) in tutte le liste delle connessioni.
 *
 * Enumerazione: i quattro stati (mai verificata, da riverificare, verificata,
 * fallita) e le quattro liste che la usano (connessioni database e ArcGIS,
 * Data Source, connettori API; i membri dei Connection Set mostrano lo stato
 * della connessione scelta).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { describeConnectionTest, lastTestFromResult, withLastTest } from '../../src/utils/connectionTestStatus';
import ConnectionTestStatus from '../../src/components/connections/ConnectionTestStatus';
import { formatDateTime } from '../../src/utils/format';

const AT = '2026-11-05T10:00:00+00:00';
const base = { success: true, message: 'Connessione PostgreSQL riuscita', tested_at: AT, tested_by: 'mario', duration_ms: 120, stale: false };

describe('describeConnectionTest: i quattro stati', () => {
  it.each([
    ['mai verificata', null, 'none', 'Mai verificata'],
    ['senza data', { ...base, tested_at: null }, 'none', 'Mai verificata'],
    ['verificata', base, 'ok', `Verificata il ${formatDateTime(AT)} da mario`],
    ['verificata senza utente', { ...base, tested_by: null }, 'ok', `Verificata il ${formatDateTime(AT)}`],
    ['fallita', { ...base, success: false, message: 'Timeout connessione MS SQL' }, 'err', 'Ultimo test fallito: Timeout connessione MS SQL'],
    ['scaduta (riuscita)', { ...base, stale: true }, 'stale', 'Da riverificare'],
    ['scaduta (fallita)', { ...base, success: false, stale: true }, 'stale', 'Da riverificare'],
  ])('%s', (_, lastTest, tone, label) => {
    const out = describeConnectionTest(lastTest);
    expect(out.tone).toBe(tone);
    expect(out.label).toBe(label);
    expect(out.detail).toBeTruthy();
  });

  it('il dettaglio dell\'esito scaduto dice quando, chi e com\'era andato', () => {
    expect(describeConnectionTest({ ...base, success: false, message: 'Host irraggiungibile', stale: true }).detail)
      .toBe(`Ultimo test il ${formatDateTime(AT)} da mario, fallito: Host irraggiungibile`);
  });

  it.each([
    ['riuscita', { ...base, tested_by: null }],
    ['fallita', { ...base, tested_by: null, success: false, message: 'Timeout' }],
    ['scaduta', { ...base, tested_by: null, stale: true }],
  ])('senza tested_by (utente di un altro gruppo) nessun «da …»: %s', (_, lastTest) => {
    const out = describeConnectionTest(lastTest);
    expect(`${out.label} ${out.detail}`).not.toMatch(/\bda\b|null|undefined/);
    expect(`${out.label} ${out.detail}`).toContain(formatDateTime(AT));
  });

  it('il dettaglio dell\'esito riuscito porta messaggio e durata', () => {
    expect(describeConnectionTest(base).detail).toBe('Connessione PostgreSQL riuscita (120ms)');
  });
});

describe('esito appena ricevuto', () => {
  it('ha la stessa forma di last_test (messaggio o errore, utente, ora)', () => {
    const now = new Date('2026-11-05T12:00:00Z');
    expect(lastTestFromResult({ success: false, error: 'HTTP 401' }, 'anna', now)).toEqual({
      success: false, message: 'HTTP 401', tested_at: now.toISOString(), tested_by: 'anna', duration_ms: null, stale: false,
    });
    expect(lastTestFromResult({ success: true }, null, now).message).toBe('Connessione riuscita');
    expect(withLastTest([{ id: 'a' }, { id: 'b' }], 'b', base)).toEqual([{ id: 'a' }, { id: 'b', last_test: base }]);
  });

  it('il componente mostra etichetta e dettaglio', () => {
    render(<ConnectionTestStatus lastTest={{ ...base, success: false, message: 'Credenziali non valide' }} />);
    const el = screen.getByText('Ultimo test fallito: Credenziali non valide');
    expect(el).toHaveAttribute('title', `Test il ${formatDateTime(AT)} da mario (120ms)`);
    expect(el.className).toContain('conn-test-status--err');
  });
});

describe('enumerazione delle liste', () => {
  const SRC = path.resolve(import.meta.dirname, '../../src');
  const read = (f) => fs.readFileSync(path.join(SRC, f), 'utf-8');
  it.each([
    ['pages/ConnectionManager.jsx', /<ConnectionTestStatus lastTest=\{conn\.last_test\}/],
    ['pages/DataSourceManager.jsx', /<ConnectionTestStatus lastTest=\{ds\.last_test\}/],
    ['components/api-conn/ApiConnectionCard.jsx', /<ConnectionTestStatus lastTest=\{api\.last_test\}/],
    ['components/connections/ConnectionSetsPanel.jsx', /describeConnectionTest\(connection\.last_test\)/],
  ])('%s mostra l\'esito salvato', (file, re) => {
    expect(read(file)).toMatch(re);
  });

  it('nessuna lista tiene un esito solo locale (testResults) ne\' l\'etichetta «Da verificare»', () => {
    for (const file of ['pages/ConnectionManager.jsx', 'pages/DataSourceManager.jsx', 'pages/ApiConnectionManager.jsx']) {
      const code = read(file);
      expect(code, file).not.toMatch(/testResults|Da verificare/);
    }
  });
});
