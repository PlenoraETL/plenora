/**
 * OpenAPIImportModal (Fase 5, P10): URL, file e testo passano dallo stesso
 * importatore; il file YAML propone l'autenticazione come l'URL; la casella
 * dell'endpoint seleziona; la posizione dell'API key arriva nel payload; un
 * errore del documento è mostrato con la riga.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const fetchOpenApiSchema = vi.fn();
vi.mock('../../src/api/apiConnections', () => ({ fetchOpenApiSchema: (...a) => fetchOpenApiSchema(...a) }));

import OpenAPIImportModal from '../../src/components/arricchisci/OpenAPIImportModal';

const fixture = (name) => fs.readFileSync(path.resolve(import.meta.dirname, '../fixtures/openapi-import', name), 'utf-8');

beforeEach(() => fetchOpenApiSchema.mockReset());

describe('OpenAPIImportModal', () => {
  it('file YAML: auth proposta dalla specifica, casella attiva, posizione della chiave nel payload', async () => {
    const onImport = vi.fn();
    render(<OpenAPIImportModal onClose={vi.fn()} onImport={onImport} />);
    await userEvent.click(screen.getByRole('button', { name: /da file/i }));
    const input = document.body.querySelector('input[type="file"]');
    const file = new File([fixture('apikey.yaml')], 'apikey.yaml', { type: 'application/yaml' });
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByText('Geocoder con API key');
    expect(screen.getByText(/Proposta dallo schema di sicurezza «QueryKey»/)).toBeInTheDocument();
    expect(screen.getByLabelText('Nome chiave')).toHaveValue('key');
    expect(screen.getByRole('radio', { name: /query param/i })).toBeChecked();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Seleziona GET /geocode' }));
    expect(screen.getByRole('checkbox', { name: 'Seleziona GET /geocode' })).toBeChecked();
    await userEvent.type(screen.getByLabelText('API Key'), 'segreta');
    await userEvent.click(screen.getByRole('button', { name: /importa 1 endpoint/i }));

    expect(onImport).toHaveBeenCalledTimes(1);
    const [payload] = onImport.mock.calls[0][0];
    expect(payload.url).toBe('https://geo.example.com/v1/geocode');
    expect(payload.auth).toEqual({ type: 'api_key', key_name: 'key', key_value: 'segreta', location: 'query' });
    expect(payload.output_mapping.map(m => m.column)).toEqual(
      ['results_id', 'results_location_id', 'lat', 'lng', 'results_address_id', 'city']);
  });

  it('URL con YAML dal proxy: server relativo risolto sull\'URL della specifica', async () => {
    fetchOpenApiSchema.mockResolvedValueOnce(fixture('petstore-v3.yaml'));
    render(<OpenAPIImportModal onClose={vi.fn()} onImport={vi.fn()} />);
    await userEvent.type(screen.getByPlaceholderText(/openapi\.json/), 'https://petstore3.swagger.io/api/v3/openapi.yaml');
    await userEvent.click(screen.getByRole('button', { name: /analizza schema/i }));
    await screen.findByText('Swagger Petstore - OpenAPI 3.0');
    expect(screen.getByText('https://petstore3.swagger.io/api/v3')).toBeInTheDocument();
    expect(screen.queryByLabelText(/base url non rilevata/i)).toBeNull();
    expect(screen.getByText('PATCH')).toBeInTheDocument();
  });

  it('testo senza server: Base URL obbligatoria prima di importare', async () => {
    render(<OpenAPIImportModal onClose={vi.fn()} onImport={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /incolla json\/yaml/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: fixture('petstore-v3.yaml') } });
    await userEvent.click(screen.getByRole('button', { name: /analizza schema/i }));
    await screen.findByLabelText(/base url non rilevata/i);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Seleziona PUT /pet' }));
    expect(screen.getByRole('button', { name: /importa 1 endpoint/i })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/base url non rilevata/i), 'https://petstore3.swagger.io');
    expect(screen.getByRole('button', { name: /importa 1 endpoint/i })).toBeEnabled();
  });

  it('endpoint non importabili elencati con il motivo; avviso del tokenUrl; body_type nel payload', async () => {
    const onImport = vi.fn();
    const doc = fixture('oauth2.yaml').replace(/^paths:\r?\n/m, [
      'paths:',
      '  /ping:',
      '    head:',
      '      responses: {"200": {description: ok}}',
      '  /cerca:',
      '    post:',
      '      requestBody: {content: {application/x-www-form-urlencoded: {schema: {type: object, properties: {q: {type: string}}}}}}',
      '      responses: {"200": {description: ok}}',
      '',
    ].join('\n'));
    render(<OpenAPIImportModal onClose={vi.fn()} onImport={onImport} />);
    await userEvent.click(screen.getByRole('button', { name: /incolla json\/yaml/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: doc } });
    await userEvent.click(screen.getByRole('button', { name: /analizza schema/i }));
    const excluded = await screen.findByRole('list', { name: 'Endpoint non importabili' });
    expect(within(excluded).getByText(/metodo HEAD non eseguito dai connettori/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Seleziona HEAD /ping' })).toBeNull();
    const warnings = screen.getByRole('list', { name: 'Avvisi sulla specifica' });
    expect(within(warnings).getByText(/tokenUrl «\/oauth\/token» di «oauth»: risolto in https:\/\/anagrafe\.example\.org\/oauth\/token/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Seleziona POST /cerca' }));
    await userEvent.click(screen.getByRole('button', { name: /importa 1 endpoint/i }));
    expect(onImport.mock.calls[0][0][0].request).toEqual({ body_type: 'form-urlencoded' });
  });

  it('errore del documento con la riga', async () => {
    render(<OpenAPIImportModal onClose={vi.fn()} onImport={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /incolla json\/yaml/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'openapi: 3.0.0\ninfo:\n  title: x\n   bad: indent\n' } });
    await userEvent.click(screen.getByRole('button', { name: /analizza schema/i }));
    await waitFor(() => expect(screen.getByText(/YAML non valido alla riga 4/)).toBeInTheDocument());
  });
});
