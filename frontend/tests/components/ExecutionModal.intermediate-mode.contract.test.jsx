/**
 * Contract tests for ExecutionModal staff-only pre-launch toggles.
 *
 * Invarianti:
 *  1. Senza awaitingLaunch+onLaunch, il modal NON mostra il config step
 *     (back-compat: tutti i caller esistenti che non passano i nuovi prop
 *     vedono il vecchio comportamento).
 *  2. Con awaitingLaunch=true + onLaunch + user superadmin: mostra il
 *     <select> con 5 opzioni (default + 4 mode BE) e il bottone Avvia.
 *  3. Con awaitingLaunch=true + onLaunch + user non-super: NON mostra il
 *     select, ma mostra comunque il bottone Avvia (stato config minimale).
 *  4. Click "Avvia" con default selezionato -> onLaunch(null).
 *  5. Click "Avvia" dopo aver scelto un mode -> onLaunch('full_artifact').
 *  6. isExecuting o result presenti hanno priorita' sul config step
 *     (anche se awaitingLaunch=true).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ExecutionModal from '../../src/components/modals/ExecutionModal';

const SUPER = { username: 'admin1', role: 'superadmin' };
const VIEWER = { username: 'viewer1', role: 'viewer' };

function renderModal(props = {}) {
  const defaults = {
    isOpen: true,
    onClose: vi.fn(),
    isExecuting: false,
    result: null,
  };
  return render(<ExecutionModal {...defaults} {...props} />);
}


describe('ExecutionModal config step pre-launch', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('back-compat: senza awaitingLaunch+onLaunch nessun config step', () => {
    renderModal();
    expect(screen.queryByLabelText(/intermediate_mode/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /avvia esecuzione/i })).toBeNull();
  });

  it('superadmin in stato config: mostra select con 5 opzioni + bottone Avvia', () => {
    renderModal({ awaitingLaunch: true, onLaunch: vi.fn(), user: SUPER });

    const select = screen.getByLabelText(/intermediate_mode/i);
    expect(select).toBeInTheDocument();
    const options = Array.from(select.querySelectorAll('option')).map(o => o.value);
    expect(options).toEqual(['', 'metadata_only', 'preview_only', 'full_pg', 'full_artifact']);

    expect(screen.getByRole('button', { name: /avvia esecuzione/i })).toBeInTheDocument();
  });

  it('non-superadmin in stato config: NIENTE select, solo bottone Avvia', () => {
    renderModal({ awaitingLaunch: true, onLaunch: vi.fn(), user: VIEWER });

    expect(screen.queryByLabelText(/intermediate_mode/i)).toBeNull();
    expect(screen.getByRole('button', { name: /avvia esecuzione/i })).toBeInTheDocument();
  });

  it('user=null (loading state) trattato come non-super: nessun select', () => {
    renderModal({ awaitingLaunch: true, onLaunch: vi.fn(), user: null });
    expect(screen.queryByLabelText(/intermediate_mode/i)).toBeNull();
  });

  it('click Avvia con default -> onLaunch(null)', () => {
    const onLaunch = vi.fn();
    renderModal({ awaitingLaunch: true, onLaunch, user: SUPER });

    fireEvent.click(screen.getByRole('button', { name: /avvia esecuzione/i }));
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith(null);
  });

  it('click Avvia dopo selezione mode -> onLaunch(mode)', async () => {
    const onLaunch = vi.fn();
    const user = userEvent.setup();
    renderModal({ awaitingLaunch: true, onLaunch, user: SUPER });

    await user.selectOptions(
      screen.getByLabelText(/intermediate_mode/i),
      'full_artifact',
    );
    await user.click(screen.getByRole('button', { name: /avvia esecuzione/i }));

    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('full_artifact');
  });

  it('isExecuting=true scavalca il config step anche con awaitingLaunch=true', () => {
    renderModal({
      awaitingLaunch: true,
      onLaunch: vi.fn(),
      user: SUPER,
      isExecuting: true,
      progress: 42,
      progressMessage: 'In corso...',
    });
    // Niente bottone Avvia: il modal e' nello stato di esecuzione.
    expect(screen.queryByRole('button', { name: /avvia esecuzione/i })).toBeNull();
  });

  it('result presente scavalca il config step', () => {
    renderModal({
      awaitingLaunch: true,
      onLaunch: vi.fn(),
      user: SUPER,
      result: { success: true, message: 'OK' },
    });
    expect(screen.queryByRole('button', { name: /avvia esecuzione/i })).toBeNull();
    // Mostra il pannello di success
    expect(screen.getByText(/esecuzione completata/i)).toBeInTheDocument();
  });

  it('onLaunch non-funzione: non mostra il config step (guard)', () => {
    renderModal({ awaitingLaunch: true, onLaunch: null, user: SUPER });
    expect(screen.queryByRole('button', { name: /avvia esecuzione/i })).toBeNull();
  });
});
