import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import PublishEseguibileModal from '../../src/components/eseguibili/PublishEseguibileModal';

/**
 * Contract test per PublishEseguibileModal.
 *
 * Aree coperte:
 *   1. render: prefill nome dal project + info text + ICON_PRESETS
 *   2. validation UX: bottone Pubblica disabilitato a nome vuoto
 *   3. publish success: payload shape + toast.success + onSuccess + onClose
 *   4. publish error: backend detail inline, onSuccess/onClose NON chiamati
 *   5. output format change -> default_output_format propagato nel payload
 *   6. icon preset click -> icon propagato nel payload
 */

const { createEseguibileMock, toastMock } = vi.hoisted(() => ({
  createEseguibileMock: vi.fn(),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
}));

vi.mock('../../src/api', () => ({
  createEseguibile: createEseguibileMock,
}));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));

const sampleProject = { id: 'proj-1', name: 'Enrich Roma' };

describe('PublishEseguibileModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });


  it('renders prefilled with the project name and shows the info text + icon presets', () => {
    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    // Nome precompilato dal project.name
    expect(screen.getByDisplayValue('Enrich Roma')).toBeInTheDocument();
    // pem-info menziona il nome progetto
    expect(screen.getByText(/Enrich Roma/)).toBeInTheDocument();
    expect(
      screen.getByText(/verrà pubblicato come eseguibile on-demand/i),
    ).toBeInTheDocument();
    // Default selected icon ▶ (state init)
    expect(
      screen.getByRole('button', { name: /Pubblica/i }),
    ).toBeInTheDocument();
  });


  it('disables the publish button when the name is empty', async () => {
    const user = userEvent.setup();
    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    const nameInput = screen.getByDisplayValue('Enrich Roma');
    await user.clear(nameInput);

    const publishBtn = screen.getByRole('button', { name: /Pubblica/i });
    expect(publishBtn).toBeDisabled();
    expect(createEseguibileMock).not.toHaveBeenCalled();
  });


  it('publishes with the expected payload and triggers success callbacks', async () => {
    createEseguibileMock.mockResolvedValueOnce({
      id: 'es-1',
      name: 'Enrich Roma',
    });
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    const user = userEvent.setup();

    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={onClose}
        onSuccess={onSuccess}
      />,
    );

    // Compila descrizione + nome file output
    const desc = screen.getByPlaceholderText(/Descrivi brevemente/i);
    await user.type(desc, 'Catena per geocoding ROMA');
    const outputName = screen.getByPlaceholderText(/risultato \(senza estensione\)/i);
    await user.type(outputName, 'roma_geocoded');

    await user.click(screen.getByRole('button', { name: /Pubblica/i }));

    await waitFor(() =>
      expect(createEseguibileMock).toHaveBeenCalledTimes(1),
    );
    expect(createEseguibileMock).toHaveBeenCalledWith({
      source_project_id: 'proj-1',
      name: 'Enrich Roma',
      description: 'Catena per geocoding ROMA',
      default_output_format: 'xlsx',
      default_output_name: 'roma_geocoded',
      icon: '▶',
    });
    expect(toastMock.success).toHaveBeenCalledWith(
      'Eseguibile "Enrich Roma" pubblicato',
    );
    expect(onSuccess).toHaveBeenCalledWith({ id: 'es-1', name: 'Enrich Roma' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });


  it('shows the backend detail inline on publish error and skips success callbacks', async () => {
    createEseguibileMock.mockRejectedValueOnce({
      response: { data: { detail: 'project_already_published' } },
      message: 'project_already_published',
    });
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    const user = userEvent.setup();

    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={onClose}
        onSuccess={onSuccess}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Pubblica/i }));

    await waitFor(() =>
      expect(createEseguibileMock).toHaveBeenCalledTimes(1),
    );
    // Errore inline (pem-error block)
    expect(
      await screen.findByText(/project_already_published/),
    ).toBeInTheDocument();
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });


  it('propagates the chosen output format to the create payload', async () => {
    createEseguibileMock.mockResolvedValueOnce({ id: 'es-2', name: 'Enrich Roma' });
    const user = userEvent.setup();

    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    const formatSelect = screen.getByRole('combobox');
    await user.selectOptions(formatSelect, 'csv');

    await user.click(screen.getByRole('button', { name: /Pubblica/i }));

    await waitFor(() =>
      expect(createEseguibileMock).toHaveBeenCalledWith(
        expect.objectContaining({ default_output_format: 'csv' }),
      ),
    );
  });


  it('propagates the selected icon preset to the create payload', async () => {
    createEseguibileMock.mockResolvedValueOnce({ id: 'es-3', name: 'Enrich Roma' });
    const user = userEvent.setup();

    render(
      <PublishEseguibileModal
        project={sampleProject}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    // Click sull'icona preset 🗺️ (button accessible name = visible emoji,
    // title="Usa 🗺️" e' attribute hint).
    await user.click(screen.getByTitle('Usa 🗺️'));

    await user.click(screen.getByRole('button', { name: /Pubblica/i }));

    await waitFor(() =>
      expect(createEseguibileMock).toHaveBeenCalledWith(
        expect.objectContaining({ icon: '🗺️' }),
      ),
    );
  });
});
