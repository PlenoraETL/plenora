/**
 * ConfirmContext.test.jsx — Contract test (PR-B canvas confirm).
 *
 * Invariante:
 *   - useConfirm() outside <ConfirmProvider> -> throw helpful error
 *   - useConfirm() inside Provider -> ritorna funzione promise-based
 *   - confirm() apre <ConfirmDialog>, click "Conferma" risolve true
 *   - click "Annulla" risolve false
 *
 * Pattern usato dai 6 nodi canvas (TransformNode, MultiJoinNode,
 * FeatureLayerNode, TableNode, ResultNode, JoinModal).
 */

import React, { useState } from 'react';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

import { ConfirmProvider, useConfirm } from '../../src/context/ConfirmContext';


function ConsumerOk({ onResolved }) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await confirm({
          title: 'Test',
          message: 'Eliminare?',
          confirmText: 'Elimina',
          variant: 'danger',
        });
        onResolved(ok);
      }}
    >
      Trigger
    </button>
  );
}


describe('ConfirmContext', () => {
  it('useConfirm() fuori Provider lancia errore helpful', () => {
    // Sopprimo console.error nominale di React per error boundary noise
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    function BareConsumer() {
      useConfirm();
      return null;
    }
    expect(() => render(<BareConsumer />)).toThrow(/ConfirmProvider/);
    errSpy.mockRestore();
  });

  it('Provider rende children senza errore', () => {
    render(
      <ConfirmProvider>
        <div data-testid="child">hello</div>
      </ConfirmProvider>
    );
    expect(screen.getByTestId('child')).toHaveTextContent('hello');
  });

  it('confirm() risolve true quando si clicca il button conferma', async () => {
    const onResolved = vi.fn();
    const user = userEvent.setup();

    render(
      <ConfirmProvider>
        <ConsumerOk onResolved={onResolved} />
      </ConfirmProvider>
    );

    // Apri il dialog
    await user.click(screen.getByRole('button', { name: 'Trigger' }));

    // Click sul button "Elimina" del ConfirmDialog
    const confirmBtn = await screen.findByRole('button', { name: 'Elimina' });
    await user.click(confirmBtn);

    expect(onResolved).toHaveBeenCalledWith(true);
  });

  it('confirm() risolve false quando si clicca Annulla', async () => {
    const onResolved = vi.fn();
    const user = userEvent.setup();

    render(
      <ConfirmProvider>
        <ConsumerOk onResolved={onResolved} />
      </ConfirmProvider>
    );

    await user.click(screen.getByRole('button', { name: 'Trigger' }));

    const cancelBtn = await screen.findByRole('button', { name: 'Annulla' });
    await user.click(cancelBtn);

    expect(onResolved).toHaveBeenCalledWith(false);
  });
});
