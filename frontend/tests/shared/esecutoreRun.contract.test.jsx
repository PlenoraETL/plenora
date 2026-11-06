/**
 * Contract della pagina /run dell'esecutore (UI-4, U14): linguaggio
 * semplice (lo controlla anche terms.contract), file modello delle colonne
 * attese, card senza «0 colonne attese» per gli Eseguibili con il modulo.
 *
 *  1. Il file modello e' un CSV con solo le intestazioni, nell'ordine
 *     dichiarato, con i campi speciali tra virgolette (RFC 4180) e il BOM.
 *  2. La finestra di esecuzione offre il file modello quando ci sono
 *     colonne attese; lo scarica con quelle colonne.
 *  3. La card di un Eseguibile con il modulo dice «Modulo da compilare»;
 *     con colonne attese dice quante, al singolare o al plurale.
 */
import React from 'react';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const api = vi.hoisted(() => ({
  listEseguibili: vi.fn(),
  getEseguibile: vi.fn(),
  analyzeEseguibileUpload: vi.fn(),
  executeEseguibile: vi.fn(),
  cleanupTaskCompletely: vi.fn(),
}));
vi.mock('../../src/api', () => api);
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => ({ token: 't', user: { username: 'e1', role: 'esecutore' } }) }));
vi.mock('../../src/hooks/useSignOut', () => ({ useSignOut: () => () => {} }));
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
const saved = vi.hoisted(() => ({ calls: [] }));
vi.mock('../../src/utils/downloadBlob', async (orig) => ({
  ...(await orig()),
  saveBlob: (blob, name) => { saved.calls.push([blob, name]); },
}));

import { csvTemplateText, csvTemplateName } from '../../src/utils/csvTemplate';
import { usesRunForm } from '../../src/components/eseguibili/runForm';
import { sourceFiles } from '../helpers/sourceScan.js';
import EsecutoreHome from '../../src/pages/EsecutoreHome';
import EseguibileExecuteWizard from '../../src/components/eseguibili/EseguibileExecuteWizard';
import { ToastProvider } from '../../src/context/ToastContext';

afterEach(() => { cleanup(); saved.calls = []; vi.clearAllMocks(); });

describe('file modello delle colonne attese', () => {
  it('solo intestazioni, in ordine, CSV standard con BOM', () => {
    expect(csvTemplateText([{ name: 'codice' }, { name: 'via, civico' }, { name: 'nota "x"' }]))
      .toBe('﻿codice,"via, civico","nota ""x"""\r\n');
    expect(csvTemplateText([])).toBe('﻿\r\n');
    expect(csvTemplateName('Geocodifica / indirizzi')).toBe('modello_Geocodifica_indirizzi.csv');
  });

  it('la finestra lo offre e lo scarica con le colonne dell\'Eseguibile', async () => {
    api.getEseguibile.mockResolvedValue({
      id: 'e1', name: 'Geocodifica', expected_columns: [{ name: 'via', required: true }, { name: 'comune', required: false }],
      default_output_format: 'csv',
    });
    await act(async () => { render(<ToastProvider><EseguibileExecuteWizard eseguibileId="e1" onClose={() => {}} /></ToastProvider>); });
    expect(screen.getByText('Il file deve avere queste colonne:')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Scarica il file modello/ }));
    expect(saved.calls).toHaveLength(1);
    const [blob, name] = saved.calls[0];
    expect(name).toBe('modello_Geocodifica.csv');
    // Il BOM UTF-8 c'e' (text() lo toglie: si guardano i byte).
    const bytes = [...new Uint8Array(await blob.arrayBuffer())];
    expect(bytes.slice(0, 3)).toEqual([0xEF, 0xBB, 0xBF]);
    expect(await blob.text()).toBe('via,comune\r\n');
    // Passi in parole semplici.
    expect(screen.getByText('📁 Carica il file')).toBeInTheDocument();
    expect(screen.getByText('🔗 Abbina le colonne')).toBeInTheDocument();
  });
});

describe('card dell\'Eseguibile su /run', () => {
  // UI conferma U14: modulo o file lo decide il manifest (`has_manifest`
  // nella lista, `manifest` nel dettaglio), come la finestra che si apre;
  // mai il numero di colonne attese.
  it('modulo dinamico: «Modulo da compilare» dal manifest, anche con colonne legacy; senza manifest: il file', async () => {
    api.listEseguibili.mockResolvedValue([
      { id: 'a', name: 'Con modulo', expected_columns_count: 0, inputs_count: 2, has_manifest: true, default_output_format: 'xlsx' },
      { id: 'm', name: 'Modulo e colonne vecchie', expected_columns_count: 2, has_manifest: true, default_output_format: 'xlsx' },
      { id: 'b', name: 'Con file', expected_columns_count: 3, default_output_format: 'csv' },
      { id: 'c', name: 'Una colonna', expected_columns_count: 1, default_output_format: 'csv' },
      { id: 'z', name: 'Senza colonne', expected_columns_count: 0, has_manifest: false, default_output_format: 'csv' },
    ]);
    await act(async () => { render(<ToastProvider><EsecutoreHome /></ToastProvider>); });
    const card = (n) => screen.getByRole('button', { name: new RegExp(n) }).textContent;
    expect(card('Con modulo')).toContain('Modulo da compilare');
    expect(card('Con modulo')).not.toMatch(/0 colonn/);
    expect(card('Modulo e colonne vecchie')).toContain('Modulo da compilare');
    expect(card('Con file')).toContain('File con 3 colonne');
    expect(card('Una colonna')).toContain('File con 1 colonna');
    expect(card('Senza colonne')).toContain('File da caricare');
    expect(card('Senza colonne')).not.toMatch(/0 colonn|Modulo/);
    expect(card('Con file')).toContain('Risultato: csv');
  });

  it('la card e la finestra usano la stessa regola (runForm): nessun\'altra decisione nel codice', () => {
    expect(usesRunForm({ has_manifest: true, expected_columns_count: 4 })).toBe(true);
    expect(usesRunForm({ manifest: { inputs: [] } })).toBe(true);
    expect(usesRunForm({ expected_columns_count: 0 })).toBe(false);
    const offenders = sourceFiles(/\.jsx?$/)
      .filter(f => f.file !== 'components/eseguibili/runForm.js')
      .flatMap(({ file, code }) => [
        ...code.matchAll(/expected_columns_count\s*(?:>|>=|<|<=|===|!==|==|\?)/g),
        ...code.matchAll(/eseguibile\??\.(?:manifest|has_manifest)\s*[)?&|]/g),
      ].map(m => `${file}: ${m[0]}`));
    expect(offenders).toEqual([]);
  });
});
