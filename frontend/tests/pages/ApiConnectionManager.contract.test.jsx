import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ApiConnectionManager from '../../src/pages/ApiConnectionManager';

const { httpMock, toastMock, navigateMock } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
}));

vi.mock('../../src/api/http', () => ({
  default: httpMock,
}));

vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => globalThis.__frontendTestHelpers.createAuthContextValue(vi),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../src/components/arricchisci/TemplateLibraryModal', () => ({
  default: ({ onSelectTemplate }) => (
    <button
      type="button"
      onClick={() => onSelectTemplate({
        name: 'poi-search',
        display: {
          name: 'POI Search',
          short_description: 'template payload',
        },
        config: {
          api_type: 'generator',
          endpoint: { url: 'https://example.test/poi', method: 'GET' },
          auth: { type: 'none' },
          custom_headers: { 'X-Client': 'plenora' },
          static_parameters: { dataset: 'poi' },
          request: { body_type: 'json' },
          async: {
            enabled: true,
            job_id_path: 'id',
            status_url: '{base}/jobs/{job_id}',
            result_path: 'result',
          },
          options: { rate_limit: 7, timeout: 45, retry_on_error: false, retry_max: 1 },
        },
        parameters: [
          { name: 'city', type: 'string', default: 'Rome', hidden: false },
        ],
        output_mapping: [
          { path: 'items[*].name', column: 'poi_name', type: 'string' },
        ],
        _meta: { category: 'geo' },
      })}
    >
      Usa Template Test
    </button>
  ),
}));
vi.mock('../../src/components/arricchisci/OpenAPIImportModal', () => ({
  default: ({ onImport }) => (
    <button
      type="button"
      onClick={() => onImport([
        {
          name: 'OpenAPI Geocode',
          api_type: 'enricher',
          category: 'geocoding',
          connection_type: 'openapi',
          url: 'https://example.test/openapi/geocode',
          method: 'POST',
        },
        {
          name: 'OpenAPI Search',
          api_type: 'generator',
          category: 'custom',
          connection_type: 'openapi',
          url: 'https://example.test/openapi/search',
          method: 'GET',
        },
      ])}
    >
      Importa OpenAPI Test
    </button>
  ),
}));
vi.mock('../../src/components/arricchisci/DataSourceConfigModal', () => ({
  default: ({ onSave }) => (
    <button
      type="button"
      onClick={() => onSave({
        name: 'FTP Import',
        source_type: 'ftp',
        datasource: {
          type: 'ftp',
          host: 'ftp.example.test',
          path: '/incoming',
        },
      })}
    >
      Salva DataSource Test
    </button>
  ),
}));
vi.mock('../../src/components/panels/ResponseConfigBuilder', () => ({
  default: () => null,
}));
vi.mock('../../src/utils/hexCanvas', () => ({
  useHexCanvas: () => {},
}));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => null,
}));
vi.mock('../../src/components/AppSidebar', () => ({
  default: () => null,
}));

describe('ApiConnectionManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    httpMock.get.mockResolvedValue({
      data: {
        api_connections: [],
      },
    });
  });

  it('preserves rest_file defaults when editing a partial backend payload', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        api_connections: [
          {
            id: 'api-1',
            name: 'Partial REST File',
            description: 'partial payload from backend',
            api_type: 'enricher',
            category: 'custom',
            connection_type: 'rest_file',
            endpoint: { url: 'https://example.test/upload', method: 'POST' },
            auth: { type: 'none' },
            custom_headers: {},
            parameters: [{ name: 'city', type: 'string', mode: 'mapped' }],
            static_parameters: {},
            output_mapping: [],
            schema_fields: [],
            pagination: { type: 'none' },
            options: { rate_limit: 5 },
            batch: { enabled: false },
            request: { body_type: 'multipart', file_fields: [{ name: 'file' }] },
            response: {},
            usage: { calls_total: 0, calls_error: 0 },
            can_edit: true,
          },
        ],
      },
    });
    httpMock.put.mockResolvedValueOnce({ data: { id: 'api-1' } });

    render(<ApiConnectionManager />);

    await screen.findByText('Partial REST File');
    await userEvent.click(screen.getByTitle('Modifica'));

    const timeoutInput = await screen.findByDisplayValue('600');
    expect(timeoutInput).toBeInTheDocument();

    await userEvent.click(screen.getByText('Test'));
    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => {
      expect(httpMock.put).toHaveBeenCalledTimes(1);
    });

    const [, payload] = httpMock.put.mock.calls[0];
    expect(payload.connection_type).toBe('rest_file');
    expect(payload.method).toBe('POST');
    expect(payload.request).toEqual(
      expect.objectContaining({
        body_type: 'multipart',
        timeout: 600,
      }),
    );
    expect(payload.response).toEqual(
      expect.objectContaining({
        format: 'binary_file',
        output_extension: '.zip',
      }),
    );
  });

  it('preserves template defaults and generator parameter normalization on save', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        api_connections: [],
      },
    });
    httpMock.post.mockResolvedValueOnce({ data: { id: 'api-template-1' } });

    render(<ApiConnectionManager />);

    await screen.findByRole('button', { name: /da template/i });
    await userEvent.click(screen.getByRole('button', { name: /da template/i }));
    await userEvent.click(screen.getByRole('button', { name: /usa template test/i }));

    expect(await screen.findByDisplayValue('POI Search')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://example.test/poi')).toBeInTheDocument();

    for (let i = 0; i < 4; i += 1) {
      await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    }
    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(1);
    });

    const [, payload] = httpMock.post.mock.calls[0];
    expect(payload).toEqual(
      expect.objectContaining({
        name: 'POI Search',
        description: 'template payload',
        api_type: 'generator',
        category: 'geo',
        connection_type: 'template',
        url: 'https://example.test/poi',
        method: 'GET',
        custom_headers: { 'X-Client': 'plenora' },
        request: { body_type: 'json' },
        async: {
          enabled: true,
          job_id_path: 'id',
          status_url: '{base}/jobs/{job_id}',
          result_path: 'result',
        },
        static_parameters: {
          dataset: 'poi',
          city: 'Rome',
        },
        output_mapping: [
          { path: 'items[*].name', column: 'poi_name', type: 'string' },
        ],
        options: expect.objectContaining({
          rate_limit: 7,
          timeout: 45,
          retry_on_error: false,
          retry_max: 1,
          parallel_workers: 1,
        }),
      }),
    );
    expect(payload.parameters).toEqual([
      expect.objectContaining({
        name: 'city',
        type: 'string',
        mode: 'fixed',
        required: false,
      }),
    ]);
  });

  it('persists sampled schema fields and suggested mapping after a successful wizard test', async () => {
    const availableFields = [
      { path: 'items[0].id', name: 'id', type: 'integer', sample_value: '1' },
      { path: 'items[0].name', name: 'name', type: 'string', sample_value: 'Roma' },
      { path: 'items', name: 'items', type: 'array', sample_value: '[1 items]' },
    ];
    const suggestedMapping = [
      { path: 'items[0].id', column: 'id', type: 'integer', selected: true },
      { path: 'items[0].name', column: 'name', type: 'string', selected: true },
    ];

    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });
    httpMock.post
      .mockResolvedValueOnce({
        data: {
          success: true,
          available_fields: availableFields,
          suggested_output_mapping: suggestedMapping,
          raw_response: { items: [{ id: 1, name: 'Roma' }] },
        },
      })
      .mockResolvedValueOnce({ data: { id: 'api-sampled' } });

    render(<ApiConnectionManager />);

    await userEvent.click(await screen.findByRole('button', { name: /manuale/i }));
    await userEvent.click(screen.getByText('Generatore'));
    await userEvent.click(screen.getByRole('button', { name: /avanti/i }));

    await userEvent.type(await screen.findByPlaceholderText(/Nominatim Geocoding/i), 'Sampled API');
    await userEvent.type(screen.getByPlaceholderText(/api\.example\.com/i), 'https://api.example.test/items');

    for (let i = 0; i < 4; i += 1) {
      await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    }

    await userEvent.click(screen.getByRole('button', { name: /esegui test/i }));
    expect(await screen.findByText(/Campi selezionati \(2\)/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(2);
    });

    expect(httpMock.post.mock.calls[0][0]).toBe('/api-connections/test');
    const [, payload] = httpMock.post.mock.calls[1];
    expect(payload.schema_fields).toEqual(availableFields);
    expect(payload.output_mapping).toEqual(suggestedMapping);
  });

  it('imports a cURL command into the wizard connection payload', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });
    httpMock.post.mockResolvedValueOnce({ data: { id: 'api-curl' } });

    render(<ApiConnectionManager />);

    await userEvent.click(await screen.findByRole('button', { name: /manuale/i }));
    await userEvent.click(screen.getByText('Generatore'));
    await userEvent.click(screen.getByRole('button', { name: /avanti/i }));

    await userEvent.click(await screen.findByText('Importa da cURL'));
    fireEvent.change(screen.getByPlaceholderText(/curl -X POST/i), {
      target: {
        value: "curl -X POST 'https://api.example.test/items?tenant=it' " +
          "-H 'Authorization: Bearer SECRET' " +
          "-H 'Content-Type: application/json' " +
          "--data-raw '{\"city\":\"Roma\",\"limit\":2}'",
      },
    });
    await userEvent.click(screen.getByRole('button', { name: /importa curl/i }));

    expect(await screen.findByDisplayValue('api.example.test items')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://api.example.test/items?tenant=it')).toBeInTheDocument();
    expect(screen.getByDisplayValue('POST')).toBeInTheDocument();

    for (let i = 0; i < 4; i += 1) {
      await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    }
    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(1);
    });

    const [, payload] = httpMock.post.mock.calls[0];
    expect(payload).toEqual(expect.objectContaining({
      name: 'api.example.test items',
      api_type: 'generator',
      url: 'https://api.example.test/items?tenant=it',
      method: 'POST',
      auth: { type: 'bearer', token: 'SECRET' },
      custom_headers: { 'Content-Type': 'application/json' },
      static_parameters: { city: 'Roma', limit: 2 },
      request: { body_type: 'json' },
    }));
    expect(payload.parameters).toEqual([
      { name: 'city', type: 'string', required: false, mode: 'fixed' },
      { name: 'limit', type: 'number', required: false, mode: 'fixed' },
    ]);
  });

  it('clears sampled output state when importing cURL over an existing connection', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        api_connections: [
          {
            id: 'api-stale',
            name: 'Existing Sampled API',
            description: 'has old sampled fields',
            api_type: 'generator',
            category: 'custom',
            endpoint: { url: 'https://old.example.test/items', method: 'GET' },
            auth: { type: 'none' },
            custom_headers: {},
            parameters: [],
            static_parameters: {},
            output_mapping: [
              { path: 'old.items[0].name', column: 'old_name', type: 'string', selected: true },
            ],
            schema_fields: [
              { path: 'old.items[0].name', name: 'name', type: 'string', sample_value: 'old' },
            ],
            response_preset: 'old-preset',
            response_config: {
              root_path: 'old.items',
              mappings: [{ path: 'old.items[0].name', column: 'old_name', type: 'string' }],
            },
            usage: { calls_total: 0, calls_error: 0 },
            can_edit: true,
          },
        ],
      },
    });
    httpMock.put.mockResolvedValueOnce({ data: { id: 'api-stale' } });

    render(<ApiConnectionManager />);

    await screen.findByText('Existing Sampled API');
    await userEvent.click(screen.getByTitle('Modifica'));

    await userEvent.click(await screen.findByText('Importa da cURL'));
    fireEvent.change(screen.getByPlaceholderText(/curl -X POST/i), {
      target: {
        value: "curl 'https://new.example.test/search?q=Roma' -H 'Accept: application/json'",
      },
    });
    await userEvent.click(screen.getByRole('button', { name: /importa curl/i }));

    expect(await screen.findByDisplayValue('https://new.example.test/search')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Test'));
    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => {
      expect(httpMock.put).toHaveBeenCalledTimes(1);
    });

    const [, payload] = httpMock.put.mock.calls[0];
    expect(payload.url).toBe('https://new.example.test/search');
    expect(payload.static_parameters).toEqual({ q: 'Roma' });
    expect(payload.output_mapping).toEqual([]);
    expect(payload.schema_fields).toEqual([]);
    expect(payload.response_preset).toBeUndefined();
    expect(payload.response_config).toBeUndefined();
  });

  it('sends draft auth metadata overrides when testing an edited connection with masked secret', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        api_connections: [
          {
            id: 'api-auth',
            name: 'Auth API',
            description: 'masked key',
            api_type: 'generator',
            category: 'custom',
            endpoint: { url: 'https://api.example.test/search', method: 'GET' },
            auth: {
              type: 'api_key',
              location: 'header',
              key_name: 'X-API-Key',
              key_value: '***',
            },
            custom_headers: {},
            parameters: [],
            static_parameters: {},
            output_mapping: [],
            schema_fields: [],
            usage: { calls_total: 0, calls_error: 0 },
            can_edit: true,
          },
        ],
      },
    });
    httpMock.post.mockResolvedValueOnce({ data: { success: true, available_fields: [] } });

    render(<ApiConnectionManager />);

    await screen.findByText('Auth API');
    await userEvent.click(screen.getByTitle('Modifica'));
    await userEvent.click(screen.getByText('Auth'));
    await userEvent.click(screen.getByLabelText('Query param'));
    await userEvent.click(screen.getByText('Test'));
    await userEvent.click(screen.getByRole('button', { name: /esegui test/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(1);
    });

    const [url, payload] = httpMock.post.mock.calls[0];
    expect(url).toBe('/api-connections/api-auth/test');
    expect(payload).toEqual(expect.objectContaining({
      test_params: {},
      include_raw: true,
      config_overrides: expect.objectContaining({
        url: 'https://api.example.test/search',
        method: 'GET',
        auth: {
          type: 'api_key',
          location: 'query',
          key_name: 'X-API-Key',
        },
      }),
    }));
    expect(payload.config_overrides.auth).not.toHaveProperty('key_value');
  });

  it('blocks save when required auth fields are missing', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });

    render(<ApiConnectionManager />);

    await userEvent.click(await screen.findByRole('button', { name: /manuale/i }));
    await userEvent.click(screen.getByText('Generatore'));
    await userEvent.click(screen.getByRole('button', { name: /avanti/i }));
    await userEvent.type(await screen.findByPlaceholderText(/Nominatim Geocoding/i), 'Bearer API');
    await userEvent.type(screen.getByPlaceholderText(/api\.example\.com/i), 'https://api.example.test/items');

    await userEvent.click(screen.getByText('Auth'));
    await userEvent.click(screen.getByLabelText('Bearer'));
    // R6.2: il salto dalla barra degli step non supera lo step Auth non valido.
    await userEvent.click(screen.getByText('Test'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Token richiesto per bearer');
    expect(screen.queryByRole('button', { name: /salva api/i })).toBeNull();
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it('maps datasource modal output to the backend datasource payload shape', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        api_connections: [
          {
            id: 'api-existing',
            name: 'Existing API',
            description: 'keeps add-card visible',
            api_type: 'enricher',
            category: 'custom',
            endpoint: { url: 'https://example.test/existing', method: 'GET' },
            usage: { calls_total: 0, calls_error: 0 },
            can_edit: true,
          },
        ],
      },
    });
    httpMock.post.mockResolvedValueOnce({ data: { id: 'ds-1' } });

    render(<ApiConnectionManager />);

    await screen.findByText('Aggiungi Connettore');
    const dataSourceButtons = screen.getAllByRole('button', { name: /data\s*source/i });
    expect(dataSourceButtons.length).toBeGreaterThan(1);
    await userEvent.click(dataSourceButtons[1]);
    await userEvent.click(screen.getByRole('button', { name: /salva datasource test/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(1);
    });

    expect(httpMock.post).toHaveBeenCalledWith('/datasources', {
      name: 'FTP Import',
      description: '',
      type: 'ftp',
      root_path: '/incoming',
      host: 'ftp.example.test',
    });
  });

  it('imports every OpenAPI connector and reloads the list after completion', async () => {
    httpMock.get
      .mockResolvedValueOnce({
        data: {
          api_connections: [
            {
              id: 'api-existing',
              name: 'Existing API',
              description: 'keeps add-card visible',
              api_type: 'enricher',
              category: 'custom',
              endpoint: { url: 'https://example.test/existing', method: 'GET' },
              usage: { calls_total: 0, calls_error: 0 },
              can_edit: true,
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          api_connections: [
            { id: 'api-import-1', name: 'OpenAPI Geocode' },
            { id: 'api-import-2', name: 'OpenAPI Search' },
          ],
        },
      });
    httpMock.post
      .mockResolvedValueOnce({ data: { id: 'api-import-1' } })
      .mockResolvedValueOnce({ data: { id: 'api-import-2' } });

    render(<ApiConnectionManager />);

    await screen.findByText('Aggiungi Connettore');
    await userEvent.click(screen.getByRole('button', { name: /openapi/i }));
    await userEvent.click(screen.getByRole('button', { name: /importa openapi test/i }));

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(2);
    });

    expect(httpMock.post.mock.calls).toEqual([
      [
        '/api-connections',
        expect.objectContaining({
          name: 'OpenAPI Geocode',
          connection_type: 'openapi',
          method: 'POST',
        }),
      ],
      [
        '/api-connections',
        expect.objectContaining({
          name: 'OpenAPI Search',
          connection_type: 'openapi',
          method: 'GET',
        }),
      ],
    ]);
    expect(httpMock.get).toHaveBeenCalledTimes(2);
    expect(toastMock.success).toHaveBeenCalledWith('✅ 2 endpoint importati');
  });

  it('warns with the partial counter when one of the imports fails', async () => {
    httpMock.get
      .mockResolvedValueOnce({
        data: {
          api_connections: [
            {
              id: 'api-existing',
              name: 'Existing API',
              description: 'keeps add-card visible',
              api_type: 'enricher',
              category: 'custom',
              endpoint: { url: 'https://example.test/existing', method: 'GET' },
              usage: { calls_total: 0, calls_error: 0 },
              can_edit: true,
            },
          ],
        },
      })
      .mockResolvedValueOnce({ data: { api_connections: [] } });
    // Primo import OK, secondo fail (BE rifiuta la connection): il loop
    // di applyOpenAPIImport continua e finalizza con toast.warning.
    httpMock.post
      .mockResolvedValueOnce({ data: { id: 'api-import-1' } })
      .mockRejectedValueOnce(new Error('schema mismatch'));

    render(<ApiConnectionManager />);

    await screen.findByText('Aggiungi Connettore');
    await userEvent.click(screen.getByRole('button', { name: /openapi/i }));
    await userEvent.click(
      screen.getByRole('button', { name: /importa openapi test/i }),
    );

    await waitFor(() => {
      expect(httpMock.post).toHaveBeenCalledTimes(2);
    });

    // 1 ok, 1 fail → toast.warning con conteggio.
    expect(toastMock.warning).toHaveBeenCalledWith(
      '⚠ 1 importati, 1 falliti',
    );
    expect(toastMock.success).not.toHaveBeenCalled();
    // Reload comunque triggered.
    expect(httpMock.get).toHaveBeenCalledTimes(2);
  });

});
