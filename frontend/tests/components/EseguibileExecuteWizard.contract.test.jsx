import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

import EseguibileExecuteWizard from '../../src/components/eseguibili/EseguibileExecuteWizard';

const {
  getEseguibileMock,
  analyzeEseguibileUploadMock,
  executeEseguibileMock,
  getTaskStatusMock,
  downloadEseguibileOutputMock,
  toastMock,
} = vi.hoisted(() => ({
  getEseguibileMock: vi.fn(),
  analyzeEseguibileUploadMock: vi.fn(),
  executeEseguibileMock: vi.fn(),
  getTaskStatusMock: vi.fn(),
  downloadEseguibileOutputMock: vi.fn(),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/api', () => ({
  getEseguibile: getEseguibileMock,
  analyzeEseguibileUpload: analyzeEseguibileUploadMock,
  executeEseguibile: executeEseguibileMock,
  getTaskStatus: getTaskStatusMock,
  downloadEseguibileOutput: downloadEseguibileOutputMock,
}));

describe('EseguibileExecuteWizard contract', () => {
  let setIntervalSpy;
  let clearIntervalSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    setIntervalSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation((callback) => {
      Promise.resolve().then(() => callback());
      return 1;
    });
    clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => {});

    getEseguibileMock.mockResolvedValue({
      id: 'exe-1',
      name: 'Enrich Addresses',
      description: 'Executes a reusable enrichment chain',
      default_output_format: 'xlsx',
      default_output_name: 'enriched_addresses',
      expected_columns: [
        { name: 'city', type: 'str', required: true, description: 'City name' },
        { name: 'cap', type: 'str', required: false, description: 'Postal code' },
      ],
    });

    analyzeEseguibileUploadMock.mockResolvedValue({
      file_id: 'file-1',
      detected_columns: [
        { name: 'city_name', type: 'str' },
        { name: 'zip_code', type: 'str' },
      ],
      row_count: 2,
      auto_mapping: { city: 'city_name', cap: 'zip_code' },
      preview_rows: [
        { city_name: 'Rome', zip_code: '00100' },
        { city_name: 'Milan', zip_code: '20100' },
      ],
    });

    executeEseguibileMock.mockResolvedValue({
      async: true,
      task_id: 'task-1',
      message: 'queued',
    });

    getTaskStatusMock.mockResolvedValue({
      status: 'success',
      progress: 100,
      message: 'done',
      result: {
        rows_processed: 2,
        output_file: 'enriched_addresses.xlsx',
      },
    });

    downloadEseguibileOutputMock.mockResolvedValue({ success: true });
  });

  afterEach(() => {
    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it('executes the full upload->mapping->run->download flow with the expected payloads', async () => {
    const onClose = vi.fn();
    const onAfterDownload = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={onClose}
        onAfterDownload={onAfterDownload}
      />,
    );

    expect(await screen.findByText('▶ Enrich Addresses')).toBeInTheDocument();
    expect(screen.getByText(/executes a reusable enrichment chain/i)).toBeInTheDocument();

    const file = new File(['city_name,zip_code\nRome,00100'], 'addresses.csv', { type: 'text/csv' });
    const input = document.getElementById('esw-file-input');
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: /avanti/i }));

    await waitFor(() => {
      expect(analyzeEseguibileUploadMock).toHaveBeenCalledWith('exe-1', file);
    });

    expect(await screen.findByText(/abbina le colonne del tuo file/i)).toBeInTheDocument();
    expect(screen.getByText(/2 righe/i)).toBeInTheDocument();
    const mappingSelects = screen.getAllByRole('combobox');
    expect(mappingSelects[0]).toHaveValue('city_name');
    expect(mappingSelects[1]).toHaveValue('zip_code');

    await user.click(screen.getByRole('button', { name: /^avanti/i }));
    expect(await screen.findByText(/scegli il risultato ed esegui/i)).toBeInTheDocument();

    const outputNameInput = screen.getByPlaceholderText(/enriched_addresses/i);
    await user.clear(outputNameInput);
    await user.type(outputNameInput, 'custom_export');

    await user.click(screen.getByRole('button', { name: /▶ esegui/i }));

    await waitFor(() => {
      expect(executeEseguibileMock).toHaveBeenCalledWith('exe-1', {
        file_id: 'file-1',
        column_mapping: [
          { chain_column: 'city', file_column: 'city_name' },
          { chain_column: 'cap', file_column: 'zip_code' },
        ],
        output_format: 'xlsx',
        output_name: 'custom_export',
      });
    });

    await waitFor(() => {
      expect(getTaskStatusMock).toHaveBeenCalledWith('task-1');
    });

    expect(await screen.findByText(/esecuzione completata/i)).toBeInTheDocument();
    expect(screen.getByText(/enriched_addresses\.xlsx/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /scarica risultato/i }));

    await waitFor(() => {
      expect(downloadEseguibileOutputMock).toHaveBeenCalledWith(
        'exe-1',
        'task-1',
        'custom_export.xlsx',
      );
      expect(onAfterDownload).toHaveBeenCalledWith('task-1');
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  it('shows the loader error when the executable metadata request fails', async () => {
    getEseguibileMock.mockRejectedValueOnce(new Error('metadata unavailable'));

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(/metadata unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/caricamento eseguibile/i)).not.toBeInTheDocument();
  });

  it('exposes dialog semantics and closes with Escape while idle', async () => {
    const onClose = vi.fn();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={onClose}
      />,
    );

    const dialog = await screen.findByRole('dialog', { name: /enrich addresses/i });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('button', { name: /chiudi/i })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close with Escape while execution is in progress', async () => {
    executeEseguibileMock.mockReturnValueOnce(new Promise(() => {}));
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={onClose}
      />,
    );

    expect(await screen.findByText(/Enrich Addresses/i)).toBeInTheDocument();

    const file = new File(['city_name,zip_code\nRome,00100'], 'addresses.csv', { type: 'text/csv' });
    const input = document.getElementById('esw-file-input');
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: /avanti/i }));
    expect(await screen.findByText(/abbina le colonne del tuo file/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^avanti/i }));
    expect(await screen.findByText(/scegli il risultato ed esegui/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /esegui/i }));

    await waitFor(() => {
      expect(executeEseguibileMock).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('button', { name: /chiudi/i })).toBeDisabled();
    });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stays on upload step and surfaces the analyze error when file analysis fails', async () => {
    analyzeEseguibileUploadMock.mockRejectedValueOnce(new Error('unsupported xlsx schema'));
    const user = userEvent.setup();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText('▶ Enrich Addresses')).toBeInTheDocument();

    const file = new File(['city_name,zip_code\nRome,00100'], 'addresses.csv', { type: 'text/csv' });
    const input = document.getElementById('esw-file-input');
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: /avanti/i }));

    expect(await screen.findByText(/unsupported xlsx schema/i)).toBeInTheDocument();
    expect(screen.getByText(/carica il file con i dati da processare/i)).toBeInTheDocument();
    expect(screen.queryByText(/abbina le colonne del tuo file/i)).not.toBeInTheDocument();
  });

  it('preloads file-based defaults from metadata and locks the output extension to the inferred one', async () => {
    getEseguibileMock.mockResolvedValueOnce({
      id: 'exe-zip',
      name: 'Build IMDF Bundle',
      description: 'Produces an archive from the final file processor',
      default_output_format: 'xlsx',
      default_output_name: 'imdf_bundle',
      output_is_file_based: true,
      inferred_output_extension: 'zip',
      expected_columns: [],
    });
    analyzeEseguibileUploadMock.mockResolvedValueOnce({
      file_id: 'file-zip',
      detected_columns: [],
      row_count: 1,
      auto_mapping: {},
      preview_rows: [],
    });
    const user = userEvent.setup();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-zip"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Build IMDF Bundle/i)).toBeInTheDocument();

    const file = new File(['seed\n1'], 'bundle.csv', { type: 'text/csv' });
    const input = document.getElementById('esw-file-input');
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: /avanti/i }));
    expect(await screen.findByText(/abbina le colonne del tuo file/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^avanti/i }));
    expect(await screen.findByText(/scegli il risultato ed esegui/i)).toBeInTheDocument();

    expect(screen.getByText('.zip')).toBeInTheDocument();
    expect(screen.getByText(/determinato dal connettore finale/i)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('imdf_bundle')).toBeInTheDocument();
  });

  it('surfaces a task failure during polling and does not expose the download action', async () => {
    getTaskStatusMock.mockResolvedValue({
      status: 'failure',
      progress: 100,
      error: 'processor timeout',
    });
    const user = userEvent.setup();

    render(
      <EseguibileExecuteWizard
        eseguibileId="exe-1"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Enrich Addresses/i)).toBeInTheDocument();

    const file = new File(['city_name,zip_code\nRome,00100'], 'addresses.csv', { type: 'text/csv' });
    const input = document.getElementById('esw-file-input');
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: /avanti/i }));
    expect(await screen.findByText(/abbina le colonne del tuo file/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^avanti/i }));
    expect(await screen.findByText(/scegli il risultato ed esegui/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /esegui/i }));

    await waitFor(() => {
      expect(getTaskStatusMock).toHaveBeenCalledWith('task-1');
      expect(screen.getByText(/processor timeout/i)).toBeInTheDocument();
    });

    expect(screen.queryByRole('button', { name: /scarica risultato/i })).not.toBeInTheDocument();
    expect(toastMock.success).not.toHaveBeenCalled();
  });
});
