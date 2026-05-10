import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
          static_parameters: { dataset: 'poi' },
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
