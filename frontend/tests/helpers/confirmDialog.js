/**
 * Risponde al dialog di conferma del progetto (ConfirmDialog nello stack
 * unico delle finestre): niente piu' window.confirm da simulare (R6.10).
 */
import { act, fireEvent, screen, within } from '@testing-library/react';

/** Il dialog di conferma aperto (l'ultimo nello stack). */
export function openConfirmDialog() {
  const dialogs = screen.queryAllByRole('dialog');
  return dialogs.length ? dialogs[dialogs.length - 1] : null;
}

/**
 * @param {boolean} ok  true = pulsante di conferma, false = Annulla
 * @param {{confirmText?: RegExp|string, cancelText?: RegExp|string}} [names]
 */
export async function answerConfirm(ok, { confirmText = null, cancelText = /annulla|resta|continua/i } = {}) {
  const dialog = openConfirmDialog();
  if (!dialog) throw new Error('Nessun dialog di conferma aperto');
  const buttons = within(dialog).getAllByRole('button');
  let target;
  if (ok) {
    target = confirmText
      ? within(dialog).getByRole('button', { name: confirmText })
      : buttons[buttons.length - 1];
  } else {
    target = within(dialog).getByRole('button', { name: cancelText });
  }
  await act(async () => { fireEvent.click(target); });
}
