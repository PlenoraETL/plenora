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
  it('modulo dinamico: «Modulo da compilare», mai «0 colonne»; colonne: quante', async () => {
    api.listEseguibili.mockResolvedValue([
      { id: 'a', name: 'Con modulo', expected_columns_count: 0, inputs_count: 2, default_output_format: 'xlsx' },
      { id: 'b', name: 'Con file', expected_columns_count: 3, default_output_format: 'csv' },
      { id: 'c', name: 'Una colonna', expected_columns_count: 1, default_output_format: 'csv' },
    ]);
    await act(async () => { render(<ToastProvider><EsecutoreHome /></ToastProvider>); });
    const card = (n) => screen.getByRole('button', { name: new RegExp(n) }).textContent;
    expect(card('Con modulo')).toContain('Modulo da compilare');
    expect(card('Con modulo')).not.toMatch(/0 colonn/);
    expect(card('Con file')).toContain('File con 3 colonne');
    expect(card('Una colonna')).toContain('File con 1 colonna');
    expect(card('Con file')).toContain('Risultato: csv');
  });
});
