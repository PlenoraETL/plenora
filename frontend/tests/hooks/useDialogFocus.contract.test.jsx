import React, { useRef } from 'react';
import { render, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import useDialogFocus from '../../src/hooks/useDialogFocus';

/**
 * Contract test per useDialogFocus.
 *
 * Coperti:
 *   1. initial focus: al mount del dialog, il primo focusable interno
 *      riceve focus.
 *   2. Tab forward loop: Tab sull'ultimo elemento -> wrap al primo.
 *   3. Shift+Tab backward loop: Shift+Tab sul primo -> wrap all'ultimo.
 *   4. Escape calls onClose una volta sola e preventDefault.
 *   5. Restore focus on cleanup: dopo unmount del dialog, il focus
 *      torna sull'elemento che era focused prima dell'open.
 *
 * Dialog stub minimale: 3 bottoni focusable.
 */

function DialogStub({ isOpen, onClose, closeOnEsc = true }) {
  const ref = useRef(null);
  useDialogFocus(isOpen, onClose, ref, { closeOnEsc });
  if (!isOpen) return null;
  return (
    <div ref={ref} role="dialog" aria-modal="true" tabIndex={-1}>
      <button type="button" data-testid="first">First</button>
      <button type="button" data-testid="middle">Middle</button>
      <button type="button" data-testid="last">Last</button>
    </div>
  );
}

function Harness({ isOpen, onClose, closeOnEsc }) {
  return (
    <div>
      <button type="button" data-testid="trigger">Open</button>
      <DialogStub isOpen={isOpen} onClose={onClose} closeOnEsc={closeOnEsc} />
    </div>
  );
}

describe('useDialogFocus - contract', () => {
  it('moves focus to the first focusable on open', () => {
    const { getByTestId } = render(<Harness isOpen={true} onClose={vi.fn()} />);
    expect(document.activeElement).toBe(getByTestId('first'));
  });

  it('Tab from last element wraps to first', () => {
    const { getByTestId } = render(<Harness isOpen={true} onClose={vi.fn()} />);
    const last = getByTestId('last');
    last.focus();
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(getByTestId('first'));
  });

  it('Shift+Tab from first element wraps to last', () => {
    const { getByTestId } = render(<Harness isOpen={true} onClose={vi.fn()} />);
    const first = getByTestId('first');
    first.focus();
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(getByTestId('last'));
  });

  it('Escape calls onClose once and prevents default', () => {
    const onClose = vi.fn();
    render(<Harness isOpen={true} onClose={onClose} />);

    const event = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
    document.dispatchEvent(event);

    expect(onClose).toHaveBeenCalledTimes(1);
    // preventDefault was called sull'evento.
    expect(event.defaultPrevented).toBe(true);
  });

  it('does NOT close on Escape when closeOnEsc=false', () => {
    const onClose = vi.fn();
    render(<Harness isOpen={true} onClose={onClose} closeOnEsc={false} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('restores focus to the previously active element on close', () => {
    // Pre-focused element esterno.
    const trigger = document.createElement('button');
    trigger.setAttribute('data-testid', 'pre-focus');
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    // Apri il dialog.
    const { rerender } = render(<Harness isOpen={true} onClose={vi.fn()} />);
    // Il focus si e' spostato al first del dialog.
    expect(document.activeElement).not.toBe(trigger);

    // Chiudi il dialog: cleanup deve riportare focus al trigger.
    act(() => {
      rerender(<Harness isOpen={false} onClose={vi.fn()} />);
    });
    expect(document.activeElement).toBe(trigger);

    document.body.removeChild(trigger);
  });
});
