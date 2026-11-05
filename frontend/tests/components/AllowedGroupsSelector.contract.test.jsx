import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AllowedGroupsSelector from '../../src/components/shared/AllowedGroupsSelector';

/**
 * Contract audit 03/11/2026:
 *  - R10.6: se le condivisioni attuali non si caricano, il pannello resta in
 *    sola lettura (prima una selezione vuota salvabile le sovrascriveva).
 *  - R5.8: il 404 di getShares ("nessuna condivisione") si riconosce dallo
 *    status HTTP, non dal testo del messaggio.
 *  - R5.6: il controllo di permesso sta fuori dagli hook del pannello, quindi
 *    lo stesso montaggio puo' passare da "senza permesso" a "con permesso"
 *    senza l'errore React "Rendered more hooks".
 */

const { sharesMock, usersMock, toastMock, authState } = vi.hoisted(() => ({
  sharesMock: { getShares: vi.fn(), setShares: vi.fn() },
  usersMock: { listGroups: vi.fn() },
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
  authState: { user: { role: 'superadmin', username: 'root', group_id: 'grp-owner' } },
}));

vi.mock('../../src/api/shares', () => sharesMock);
vi.mock('../../src/api/users', () => usersMock);
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', async () => {
  const ReactActual = await vi.importActual('react');
  const Ctx = ReactActual.createContext(null);
  return { useAuth: () => { ReactActual.useContext(Ctx); return { user: authState.user }; } };
});

const groups = [
  { group_id: 'grp-owner', name: 'Owner' },
  { group_id: 'grp-a', name: 'Comune A' },
];

function httpError(status, message) {
  const err = new Error(message);
  err.response = { status, data: { message } };
  return err;
}

describe('AllowedGroupsSelector contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'superadmin', username: 'root', group_id: 'grp-owner' };
    usersMock.listGroups.mockResolvedValue(groups);
  });

  it('stays read-only when current shares fail to load', async () => {
    sharesMock.getShares.mockRejectedValueOnce(httpError(500, 'Errore interno'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<AllowedGroupsSelector resourceType="connection" resourceId="c-1" />);

    const item = await screen.findByText('Comune A');
    const checkbox = item.closest('label').querySelector('input');
    expect(checkbox.disabled).toBe(true);
    expect(screen.getByText(/caricamento condivisioni fallito/i)).toBeTruthy();
    consoleSpy.mockRestore();
  });

  it('treats a 404 from getShares as "no shares" and stays editable', async () => {
    // Il messaggio non contiene "404": conta solo lo status.
    sharesMock.getShares.mockRejectedValueOnce(httpError(404, 'Risorsa senza condivisioni'));
    render(<AllowedGroupsSelector resourceType="connection" resourceId="c-1" />);

    const item = await screen.findByText('Comune A');
    await waitFor(() => expect(item.closest('label').querySelector('input').disabled).toBe(false));
    expect(screen.queryByText(/caricamento condivisioni fallito/i)).toBeNull();
  });

  it('does not crash when the same mounted panel gains the permission', async () => {
    // Prima il `return null` stava prima degli useState: un render senza
    // permesso seguito da uno con permesso (stesso montaggio, es. utente
    // caricato in ritardo o ruolo aggiornato) dava "Rendered more hooks".
    sharesMock.getShares.mockResolvedValue({ group_ids: [] });
    authState.user = null;
    const { rerender, container } = render(
      <AllowedGroupsSelector resourceType="connection" resourceId="c-1" />,
    );
    expect(container.innerHTML).toBe('');

    authState.user = { role: 'superadmin', username: 'root', group_id: 'grp-owner' };
    rerender(<AllowedGroupsSelector resourceType="connection" resourceId="c-1" />);
    expect(await screen.findByText('Comune A')).toBeTruthy();
  });

  it('excludes the owner group of the RESOURCE (from the server), not the user group (R10.7)', async () => {
    // Superadmin del gruppo grp-owner condivide una risorsa del gruppo grp-a.
    sharesMock.getShares.mockResolvedValue({ group_ids: [], owner_group_id: 'grp-a' });
    render(<AllowedGroupsSelector resourceType="connection" resourceId="c-1" />);

    expect(await screen.findByText('Owner')).toBeTruthy();
    expect(screen.queryByText('Comune A')).toBeNull();
  });
});
