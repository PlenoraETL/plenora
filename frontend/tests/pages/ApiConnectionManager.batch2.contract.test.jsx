import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ApiConnectionManager from '../../src/pages/ApiConnectionManager';


const { httpMock, toastMock, navigateMock, authState } = vi.hoisted(() => ({
  httpMock: globalThis.__frontendTestHelpers.createHttpMock(vi),
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
  authState: {
    user: { role: 'admin', username: 'tester', group_id: 'grp-1' },
  },
}));

vi.mock('../../src/api/http', () => ({ default: httpMock }));

vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user, logout: vi.fn() }),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('../../src/components/arricchisci/TemplateLibraryModal', () => ({
  default: () => null,
}));
vi.mock('../../src/components/arricchisci/OpenAPIImportModal', () => ({
  default: () => null,
}));
vi.mock('../../src/components/arricchisci/DataSourceConfigModal', () => ({
  default: () => null,
}));
vi.mock('../../src/components/panels/ResponseConfigBuilder', () => ({
  default: () => null,
}));
vi.mock('../../src/utils/hexCanvas', () => ({ useHexCanvas: () => {} }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({
  default: () => null,
}));
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));

const sampleApi = {
  id: 'api-1',
  name: 'Geocode',
  description: 'desc',
  api_type: 'enricher',
  category: 'custom',
  connection_type: 'manual',
  endpoint: { url: 'https://example.test/g', method: 'GET' },
  url: 'https://example.test/g',
  method: 'GET',
  auth: { type: 'none' },
  custom_headers: {},
  parameters: [{ name: 'q', type: 'string', mode: 'mapped' }],
  static_parameters: {},
  output_mapping: [],
  schema_fields: [],
  pagination: { type: 'none' },
  options: { rate_limit: 5 },
  batch: { enabled: false },
  request: {},
  response: {},
  usage: { calls_total: 0, calls_error: 0 },
  can_edit: true,
};

describe('ApiConnectionManager contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'admin', username: 'tester', group_id: 'grp-1' };
    httpMock.get.mockResolvedValue({ data: { api_connections: [] } });
  });
  it('deletes a connector after confirm, then reloads the list', async () => {
    httpMock.get
      .mockResolvedValueOnce({ data: { api_connections: [sampleApi] } })
      .mockResolvedValueOnce({ data: { api_connections: [] } });
    httpMock.delete.mockResolvedValueOnce({ data: { ok: true } });

    render(<ApiConnectionManager />);
    await screen.findByText('Geocode');

    await userEvent.click(screen.getByTitle('Elimina'));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() =>
      expect(httpMock.delete).toHaveBeenCalledWith('/api-connections/api-1'),
    );
    expect(httpMock.get).toHaveBeenCalledTimes(2);
    expect(toastMock.error).not.toHaveBeenCalled();
  });
  it('shows formatted cascade dependencies on 409 delete and skips reload', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: { api_connections: [sampleApi] },
    });
    httpMock.delete.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: 'cascade_in_use',
            message: 'in use',
            dependencies: [
              { kind: 'project', id: 'p1', name: 'Demo Project' },
            ],
          },
        },
      },
    });

    render(<ApiConnectionManager />);
    await screen.findByText('Geocode');

    await userEvent.click(screen.getByTitle('Elimina'));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Elimina' }),
    );

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    const [msg, ttl] = toastMock.error.mock.calls[0];
    expect(msg).toContain('Geocode');
    expect(msg).toContain('Demo Project');
    expect(msg).toMatch(/progetto/i);
    expect(ttl).toBe(10000);
    // Niente reload dopo cascade (handleDelete fa early return).
    expect(httpMock.get).toHaveBeenCalledTimes(1);
  });
  it('hides the cross-group sharing button for non-superadmin (default admin)', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: { api_connections: [sampleApi] },
    });

    render(<ApiConnectionManager />);
    await screen.findByText('Geocode');

    expect(
      screen.queryByTitle('Gestisci condivisione cross-gruppo'),
    ).not.toBeInTheDocument();
    expect(screen.getByTitle('Modifica')).toBeInTheDocument();
    expect(screen.getByTitle('Elimina')).toBeInTheDocument();
  });
  it('shows the cross-group sharing button for superadmin', async () => {
    authState.user = {
      role: 'superadmin',
      username: 'sa',
      group_id: 'grp-1',
    };
    httpMock.get.mockResolvedValueOnce({
      data: { api_connections: [sampleApi] },
    });

    render(<ApiConnectionManager />);
    await screen.findByText('Geocode');

    expect(
      screen.getByTitle('Gestisci condivisione cross-gruppo'),
    ).toBeInTheDocument();
  });
  it('blocks Avanti at step Parametri when an enricher has no mapped params', async () => {
    httpMock.get.mockResolvedValueOnce({ data: { api_connections: [] } });

    render(<ApiConnectionManager />);

    // Empty state mostra "✏️ Manuale" che apre il wizard a step 0.
    const manualBtn = await screen.findByRole('button', { name: /Manuale/i });
    await userEvent.click(manualBtn);

    // Step 0 (Tipo) → Avanti (no validation gate qui, default api_type=enricher).
    await userEvent.click(
      await screen.findByRole('button', { name: /avanti/i }),
    );

    // Step 1 (Info): name + url richiesti per abilitare Avanti.
    const nameInput = await screen.findByPlaceholderText(/Nominatim Geocoding/i);
    await userEvent.type(nameInput, 'Manual Test API');
    const urlInput = screen.getByPlaceholderText(/api\.example\.com/i);
    await userEvent.type(urlInput, 'https://api.example.test/x');

    await userEvent.click(screen.getByRole('button', { name: /avanti/i }));

    // Step 2 (Parametri): enricher senza param mappato → click Avanti
    // setta wizardStepError e NON avanza.
    await userEvent.click(screen.getByRole('button', { name: /avanti/i }));

    expect(
      await screen.findByText(/almeno un parametro mappato/i),
    ).toBeInTheDocument();
    // Step non avanzato a Test (5): "Salva API" sarebbe visibile solo a step 5.
    expect(
      screen.queryByRole('button', { name: /salva api/i }),
    ).not.toBeInTheDocument();
  });
  it('strips masked auth secrets before PUT on save (no leak of "***" placeholder)', async () => {
    const apiWithMaskedAuth = {
      ...sampleApi,
      auth: {
        type: 'basic',
        username: 'svc',
        password: '***masked***',
      },
    };
    httpMock.get.mockResolvedValueOnce({
      data: { api_connections: [apiWithMaskedAuth] },
    });
    httpMock.put.mockResolvedValueOnce({ data: { id: 'api-1' } });

    render(<ApiConnectionManager />);
    await screen.findByText('Geocode');
    await userEvent.click(screen.getByTitle('Modifica'));

    // Salta a step 5 (Test) cliccando il label nello stepper, evitando
    // di passare per la validation di step 2.
    await userEvent.click(await screen.findByText('Test'));
    await userEvent.click(screen.getByRole('button', { name: /salva api/i }));

    await waitFor(() => expect(httpMock.put).toHaveBeenCalledTimes(1));
    const [, payload] = httpMock.put.mock.calls[0];
    // Il valore mascherato ('***...') NON deve essere inviato al BE,
    // altrimenti il backend lo persisterebbe come password reale.
    expect(payload.auth).toEqual({ type: 'basic', username: 'svc' });
    expect(payload.auth).not.toHaveProperty('password');
  });
});
