import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EseguibiliList from '../../src/pages/EseguibiliList';


const { apiMock, toastMock, navigateMock } = vi.hoisted(() => ({
  apiMock: {
    listEseguibili: vi.fn(),
    updateEseguibile: vi.fn(),
    deleteEseguibile: vi.fn(),
    listProjects: vi.fn(),
  },
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  navigateMock: globalThis.__frontendTestHelpers.createNavigateMock(vi),
}));

vi.mock('../../src/api', () => apiMock);
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({
    user: { role: 'admin', username: 'admin', group_id: 'g1' },
  }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/EseguibileExecuteWizard', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/PublishEseguibileModal', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/EseguibileVersionsModal', () => ({ default: () => null }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({ default: () => null }));

const executable = {
  id: 'exec-1',
  name: 'Nome precedente',
  description: '',
  default_output_format: 'xlsx',
  project_type: 'manipola',
  active_version: 2,
  inputs_count: 1,
  can_edit: true,
};

describe('EseguibiliList rename contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listEseguibili.mockResolvedValue([executable]);
    apiMock.updateEseguibile.mockResolvedValue({
      ...executable,
      name: 'Nuovo nome',
    });
  });

  it('renames an editable executable without republishing it', async () => {
    const user = userEvent.setup();
    render(<EseguibiliList />);

    await screen.findByText('Nome precedente');
    await user.click(screen.getByRole('button', { name: 'Azioni per Nome precedente' }));
    await user.click(screen.getByRole('menuitem', { name: /Rinomina/i }));
    const input = screen.getByRole('textbox', {
      name: 'Nuovo nome per Nome precedente',
    });
    await user.clear(input);
    await user.type(input, 'Nuovo nome');
    await user.click(screen.getByRole('button', { name: 'Salva nome' }));

    await waitFor(() => expect(apiMock.updateEseguibile).toHaveBeenCalledWith(
      'exec-1',
      { name: 'Nuovo nome' },
    ));
    expect(await screen.findByText('Nuovo nome')).toBeInTheDocument();
    expect(toastMock.success).toHaveBeenCalledWith('Eseguibile rinominato');
  });
});
