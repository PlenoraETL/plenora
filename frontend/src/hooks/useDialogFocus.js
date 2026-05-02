/**
 * useDialogFocus - focus management per dialog/modali.
 *
 * Side effects (solo quando `isOpen === true`):
 *   1. Open: salva `document.activeElement` come elemento da
 *      ripristinare al close. Sposta focus al primo focusable interno
 *      al `dialogRef`, o sul container stesso come fallback.
 *   2. Tab trap: `Tab` / `Shift+Tab` ciclano tra i focusable interni
 *      al dialog senza uscire.
 *   3. Escape close (opt-in via `closeOnEsc`, default true): chiama
 *      `onClose()` una sola volta per pressione (preventDefault).
 *   4. Close: prova a ripristinare focus sull'elemento salvato. Se
 *      l'elemento non e' piu' nel DOM o focus() throwa, fallback a
 *      `document.body.focus()` silenzioso.
 *
 * IMPORTANTE: l'hook usa un ref interno per `onClose` cosi' il
 * keydown handler usa sempre la versione corrente senza che il
 * change di reference (callback inline nel parent) re-triggeri
 * setup/cleanup dell'effetto. Questo evita focus thrashing durante
 * il typing nei field del dialog.
 *
 * @param {boolean}                isOpen     - dialog visibility
 * @param {() => void}             onClose    - close handler (opzionale per Escape)
 * @param {React.RefObject}        dialogRef  - ref al container `role="dialog"`
 * @param {{ closeOnEsc?: boolean }} options
 */

import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]:not([disabled])',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(',');

function getFocusable(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
}

export function useDialogFocus(isOpen, onClose, dialogRef, options = {}) {
  const { closeOnEsc = true } = options;

  // Ref interno: il keydown handler legge sempre la callback corrente
  // senza che il cambio di reference (inline arrow in props) faccia
  // re-trigger dell'effetto. Senza questo, ogni render del parent
  // produrrebbe cleanup+setup -> focus restore in mezzo al typing.
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  useEffect(() => {
    if (!isOpen) return undefined;
    const dialogEl = dialogRef && dialogRef.current;
    if (!dialogEl) return undefined;

    // 1. Salva elemento focused da ripristinare al close.
    const previousActive = document.activeElement;

    // 2. Sposta focus al primo focusable interno (o al container).
    const focusables = getFocusable(dialogEl);
    if (focusables.length > 0) {
      focusables[0].focus();
    } else if (typeof dialogEl.focus === 'function') {
      // Fallback: container con tabIndex={-1} riceve focus.
      dialogEl.focus();
    }

    // 3. Keydown listener per Tab trap + Escape close.
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && closeOnEsc) {
        const cb = onCloseRef.current;
        if (typeof cb === 'function') {
          e.preventDefault();
          cb();
        }
        return;
      }
      if (e.key !== 'Tab') return;

      const items = getFocusable(dialogEl);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (e.shiftKey) {
        if (active === first || !dialogEl.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !dialogEl.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      try {
        if (
          previousActive
          && typeof previousActive.focus === 'function'
          && document.contains(previousActive)
        ) {
          previousActive.focus();
        } else if (document.body && typeof document.body.focus === 'function') {
          document.body.focus();
        }
      } catch (_) {
        try { document.body && document.body.focus(); } catch (_e) { /* noop */ }
      }
    };
    // NOTA: deps escludono `onClose` (proxato via ref) e `dialogRef`
    // (oggetto stable da useRef). Effetto si re-runna SOLO al toggle
    // di isOpen o al cambio di closeOnEsc.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, closeOnEsc]);
}

export default useDialogFocus;
