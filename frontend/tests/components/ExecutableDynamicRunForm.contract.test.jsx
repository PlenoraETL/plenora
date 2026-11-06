/**
 * ExecutableDynamicRunForm — Fase 5 T2 (BA2, R9.2, R9.3, R4.2, N12).
 *
 * Gli Eseguibili versionati usano la stessa finestra «Prima di avviare»
 * dell'editor (input, file, stima di Arricchisci con lo stesso payload
 * dell'avvio) e lo stesso esito (ExecutionModal sopra useTask): mai
 * "completata" con i permessi revocati, pulizia dell'esecutore a ogni
 * chiusura dopo la fine, l'analisi del file A non finisce sul file B.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const {
  analyzeMock, preflightMock, runMock, downloadMock, getTaskStatusMock, cancelTaskMock,
} = vi.hoisted(() => ({
  analyzeMock: vi.fn(),
  preflightMock: vi.fn(),
  runMock: vi.fn(),
  downloadMock: vi.fn(),
  getTaskStatusMock: vi.fn(),
  cancelTaskMock: vi.fn(),
}));

vi.mock('../../src/api', () => ({
  analyzeEseguibileUpload: analyzeMock,
  preflightEseguibile: preflightMock,
  runEseguibile: runMock,
  downloadEseguibileOutput: downloadMock,
  getTaskStatus: getTaskStatusMock,
  cancelTask: cancelTaskMock,
}));

import { ConfirmProvider } from '../../src/context/ConfirmContext';
import ExecutableDynamicRunForm, { filePayloads, filesComplete, scalarDefinitions } from '../../src/components/eseguibili/ExecutableDynamicRunForm';

const fileInput = {
  key: 'dati', label: 'File dati', kind: 'file', required: true, order: 1,
  expected_columns: [{ name: 'city', required: true }],
};
const scalarInput = {
  key: 'soglia', label: 'Soglia', kind: 'integer', required: false, order: 2,
  validation: { minimum: 1, maximum: 10 }, default_value: '',
};
const connInput = {
  key: 'conn', label: 'Connessione', kind: 'connection', required: false, order: 3,
  connection_options: [{ key: 'sel-a', label: 'Produzione' }],
};

const eseguibileOf = (projectType = 'estrai') => ({
  id: 'exe-1', name: 'Pulizia indirizzi', project_type: projectType,
  active_version: 2, active_version_id: 'v2', default_output_format: 'csv',
  manifest: { inputs: [fileInput, scalarInput, connInput] },
});

const analysis = (fileId) => ({
  file_id: fileId, row_count: 3, detected_columns: [{ name: 'citta' }], auto_mapping: { city: 'citta' },
});

async function chooseAndAnalyze(name, fileId) {
  const input = document.querySelector('input[type="file"]');
  const file = new File(['citta\nRoma'], name, { type: 'text/csv' });
  analyzeMock.mockResolvedValueOnce(analysis(fileId));
  // T5 (R8.10): l'analisi parte da sola alla scelta del file.
  fireEvent.change(input, { target: { files: [file] } });
  await screen.findByText(/3 righe rilevate/);
}

beforeEach(() => {
  vi.clearAllMocks();
  runMock.mockResolvedValue({ async: true, task_id: 'task-9' });
  getTaskStatusMock.mockResolvedValue({ status: 'success', progress: 100, result: { rows_processed: 3, output_file: 'out.csv' } });
  downloadMock.mockResolvedValue(undefined);
});

describe('ExecutableDynamicRunForm — la finestra unica «Prima di avviare»', () => {
  it('definizioni del manifest nel formato della finestra; payload e completezza dei file', () => {
    expect(scalarDefinitions({ inputs: [fileInput, scalarInput] })).toEqual([{ ...scalarInput, input_key: 'soglia', input_kind: 'integer' }]);
    const files = { dati: [{ id: 'f1', analysis: { file_id: 'up-1' }, mapping: { city: 'citta' } }] };
    expect(filePayloads([fileInput], files)).toEqual({ dati: { file_id: 'up-1', column_mapping: [{ chain_column: 'city', file_column: 'citta' }] } });
    expect(filesComplete([fileInput], files)).toBe(true);
    expect(filesComplete([fileInput], { dati: [{ id: 'f1', analysis: null }] })).toBe(false);
    expect(filesComplete([fileInput], {})).toBe(false);
  });

  it('mostra input, vincoli e connessioni nella finestra unica; Avvia solo con i file analizzati', async () => {
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={vi.fn()} />);
    expect(await screen.findByText('Pulizia indirizzi')).toBeInTheDocument();
    const soglia = screen.getByLabelText('Soglia');
    expect(screen.getByRole('option', { name: 'Produzione' })).toHaveValue('sel-a');
    const avvia = screen.getByRole('button', { name: /avvia l'esecuzione/i });
    expect(avvia).toBeDisabled();
    await chooseAndAnalyze('a.csv', 'up-a');
    expect(avvia).not.toBeDisabled();
    // T5 (R8.10): i vincoli sono la regola del backend, non attributi HTML:
    // fuori dai limiti l'errore si vede sul campo e Avvia resta spento.
    fireEvent.change(soglia, { target: { value: '50' } });
    expect(screen.getByText('Inserisci un valore tra 1 e 10')).toBeInTheDocument();
    expect(avvia).toBeDisabled();
    fireEvent.change(soglia, { target: { value: '7' } });
    expect(screen.queryByText('Inserisci un valore tra 1 e 10')).toBeNull();
    expect(avvia).not.toBeDisabled();
    fireEvent.click(avvia);
    // Il valore inviato e' quello convertito (7), non il testo ("7").
    await waitFor(() => expect(runMock).toHaveBeenCalledWith('exe-1', expect.objectContaining({
      inputs: expect.objectContaining({ soglia: 7 }),
    })));
  });

  it('l\'analisi del file A arrivata dopo la scelta di B non finisce su B', async () => {
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={vi.fn()} />);
    const input = await waitFor(() => document.querySelector('input[type="file"]'));
    let resolveA;
    analyzeMock.mockImplementationOnce(() => new Promise((r) => { resolveA = r; }));
    fireEvent.change(input, { target: { files: [new File(['x'], 'a.csv')] } });
    await waitFor(() => expect(analyzeMock).toHaveBeenCalledTimes(1));
    // Scelto B prima della risposta per A.
    fireEvent.change(input, { target: { files: [new File(['y'], 'b.csv')] } });
    await act(async () => { resolveA(analysis('up-a')); });
    expect(screen.queryByText(/3 righe rilevate/)).toBeNull();
    expect(screen.getByRole('button', { name: /avvia l'esecuzione/i })).toBeDisabled();
  });

  it('avvio e polling dal ciclo unico: progresso, esito e download; chiusura = pulizia dell\'esecutore', async () => {
    const onClose = vi.fn();
    const onAfterDownload = vi.fn().mockResolvedValue(undefined);
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={onClose} onAfterDownload={onAfterDownload} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    await waitFor(() => expect(runMock).toHaveBeenCalledWith('exe-1', {
      version_id: 'v2',
      inputs: { dati: { file_id: 'up-a', column_mapping: [{ chain_column: 'city', file_column: 'citta' }] } },
      output_format: 'csv',
      output_name: undefined,
    }));
    expect(await screen.findByText('Esecuzione completata!')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /scarica il file/i }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('exe-1', 'task-9', 'Pulizia indirizzi.csv', expect.objectContaining({ onProgress: expect.any(Function) })));
    // UI-3 (U13): scaricare non chiude; la pulizia parte alla chiusura.
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button', { name: /^chiudi$/i }).at(-1));
    await waitFor(() => expect(onAfterDownload).toHaveBeenCalledWith('task-9'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('U13 (UI-3): chiudere con un risultato NON scaricato chiede; «Resta qui» non elimina, «Chiudi ed elimina» pulisce', async () => {
    const onClose = vi.fn();
    const onAfterDownload = vi.fn().mockResolvedValue(undefined);
    render(<ConfirmProvider><ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={onClose} onAfterDownload={onAfterDownload} /></ConfirmProvider>);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    expect(await screen.findByText('Esecuzione completata!')).toBeInTheDocument();
    expect(screen.getByText(/se la chiudi senza scaricarlo ti viene chiesto/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /^chiudi$/i }).at(-1));
    fireEvent.click(await screen.findByRole('button', { name: 'Resta qui' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Resta qui' })).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
    expect(onAfterDownload).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button', { name: /^chiudi$/i }).at(-1));
    fireEvent.click(await screen.findByRole('button', { name: 'Chiudi ed elimina' }));
    await waitFor(() => expect(onAfterDownload).toHaveBeenCalledWith('task-9'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(downloadMock).not.toHaveBeenCalled();
  });

  it('permessi revocati durante l\'esecuzione: banner, nessun "completata" ne\' download (R9.2)', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'success', result: { status: 'permission_revoked', success: false, message: 'Ruolo declassato' } });
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={vi.fn()} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    expect(await screen.findAllByText(/Ruolo declassato/)).not.toHaveLength(0);
    expect(screen.queryByText('Esecuzione completata!')).toBeNull();
    expect(screen.queryByRole('button', { name: /scarica file/i })).toBeNull();
  });

  it('esito fallito e Chiudi: la pulizia dell\'esecutore parte comunque (R9.3)', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'failure', error: 'processore fermo' });
    const onClose = vi.fn();
    const onAfterDownload = vi.fn().mockResolvedValue(undefined);
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={onClose} onAfterDownload={onAfterDownload} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    expect(await screen.findByText(/processore fermo/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Chiudi', { selector: 'button' }));
    await waitFor(() => expect(onAfterDownload).toHaveBeenCalledWith('task-9'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('task che non si legge piu\' (404 ripetuti): esito di errore, la finestra non resta in esecuzione (N12)', async () => {
    getTaskStatusMock.mockRejectedValue({ response: { status: 403 } });
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={vi.fn()} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    expect(await screen.findByText(/Errore nell'esecuzione/)).toBeInTheDocument();
    expect(screen.getByText('Chiudi', { selector: 'button' })).not.toBeDisabled();
  });

  it('durante l\'esecuzione: Annulla, e la finestra non si chiude senza esito', async () => {
    getTaskStatusMock.mockResolvedValue({ status: 'progress', progress: 20, message: 'in corso' });
    cancelTaskMock.mockResolvedValue({ status: 'cancellation_requested' });
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf()} onClose={vi.fn()} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    fireEvent.click(screen.getByRole('button', { name: /avvia l'esecuzione/i }));
    const annulla = await screen.findByRole('button', { name: /annulla/i });
    // × (stack unico, busy) e Chiudi del piede: entrambi spenti.
    const chiudi = screen.getAllByRole('button', { name: 'Chiudi' });
    expect(chiudi.length).toBeGreaterThanOrEqual(1);
    chiudi.forEach(b => expect(b).toBeDisabled());
    fireEvent.click(annulla);
    await waitFor(() => expect(cancelTaskMock).toHaveBeenCalledWith('task-9'));
  });

  it('Arricchisci: stima del server con gli stessi input dell\'avvio e impronta all\'avvio; 409 = stima rifatta', async () => {
    preflightMock.mockResolvedValue({ valid: true, nodes: [{ node_id: 'api1', api_calls: 3, input_rows: 3 }], preparation_fingerprint: 'fp-1', checks: {} });
    const conflict = Object.assign(new Error('cambiata'), {
      response: { status: 409, data: { detail: { error: 'validation_error', reason: 'preparation_changed', message: 'Preparazione cambiata' } } },
    });
    runMock.mockRejectedValueOnce(conflict).mockResolvedValueOnce({ async: true, task_id: 'task-9' });
    render(<ExecutableDynamicRunForm eseguibile={eseguibileOf('arricchisci')} onClose={vi.fn()} />);
    await chooseAndAnalyze('a.csv', 'up-a');
    await waitFor(() => expect(preflightMock).toHaveBeenCalled());
    const [, payload] = preflightMock.mock.calls.at(-1);
    expect(payload).toEqual({ version_id: 'v2', inputs: { dati: { file_id: 'up-a', column_mapping: [{ chain_column: 'city', file_column: 'citta' }] } } });
    expect(await screen.findByText('3 chiamate stimate')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /conferma e avvia/i }));
    await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
    expect(runMock.mock.calls[0][1].preparation_fingerprint).toBe('fp-1');
    // 409: la finestra torna con il motivo e la stima si rifa'.
    expect(await screen.findByText(/Preparazione cambiata/)).toBeInTheDocument();
    await waitFor(() => expect(preflightMock.mock.calls.length).toBeGreaterThan(1));
  });
});
