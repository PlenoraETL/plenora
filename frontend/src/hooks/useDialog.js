/**
 * useDialog — l'UNICO modo di aprire una finestra (Fase 5, T4).
 *
 * Registra la finestra nello stack unico (utils/dialogStack), che possiede
 * Esc (solo la finestra in cima), il giro del Tab e il blocco dello scroll.
 * L'hook aggiunge:
 *   - focus: all'apertura resta sul campo con `autoFocus` (o
 *     `data-autofocus`) se c'e', altrimenti va sul primo controllo che non sia
 *     la ×; alla chiusura torna a chi ha aperto la finestra;
 *   - overlay: chiude solo con un clic iniziato E finito sull'overlay vero
 *     (una selezione che parte da un campo e finisce fuori non chiude), solo
 *     se `closeOnOverlay` e solo se la finestra e' in cima;
 *   - `busy` (invio in corso): niente Esc, niente overlay, × disabilitata;
 *   - `beforeClose(reason)`: puo' chiedere (es. wizard con dati inseriti);
 *     `false` = resta aperta. Vale per Esc, overlay, × e `requestClose()`;
 *   - id unici per titolo (`titleId`) e finestra.
 *
 * Uso (finestra fatta a mano; ModalWrapper e Wizard lo usano gia'):
 *   const dlg = useDialog({ isOpen, onClose, busy: saving });
 *   {isOpen && (
 *     <div className="x__overlay" {...dlg.overlayProps}>
 *       <div ref={dlg.ref} className="x__modal" {...dlg.dialogProps}>
 *         <h2 id={dlg.titleId}>Titolo</h2>
 *         <button {...dlg.closeButtonProps}>✕</button>
 *       </div>
 *     </div>
 *   )}
 */
import { useCallback, useEffect, useId, useMemo, useRef } from 'react';
import {
  getFocusable, isTopDialog, pushDialog, removeDialog, topDialogElement,
} from '../utils/dialogStack';
import { endTyping } from '../store/typingAction';


export default function useDialog({
  isOpen,
  onClose,
  ref: externalRef = null,
  closeOnEsc = true,
  closeOnOverlay = true,
  busy = false,
  beforeClose = null,
  labelledBy = true,
} = {}) {
  const innerRef = useRef(null);
  const ref = externalRef || innerRef;
  const reactId = useId();
  const id = `dlg-${reactId.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const titleId = `${id}-title`;

  const optsRef = useRef(null);
  optsRef.current = { onClose, closeOnEsc, closeOnOverlay, busy, beforeClose };
  const closingRef = useRef(false);

  const requestClose = useCallback(async (reason = 'button') => {
    const o = optsRef.current;
    if (o.busy || closingRef.current || typeof o.onClose !== 'function') return false;
    if (reason === 'esc' && !o.closeOnEsc) return false;
    if (reason === 'overlay' && (!o.closeOnOverlay || !isTopDialog(id))) return false;
    if (typeof o.beforeClose === 'function') {
      closingRef.current = true;
      let ok;
      try { ok = await o.beforeClose(reason); } finally { closingRef.current = false; }
      if (ok === false) return false;
    }
    optsRef.current.onClose();
    return true;
  }, [id]);

  // Chi ha aperto la finestra: letto al render dell'apertura, PRIMA che un
  // `autoFocus` interno sposti il focus (React lo fa prima degli effetti).
  const wasOpenRef = useRef(false);
  const openerRef = useRef(null);
  if (isOpen && !wasOpenRef.current && typeof document !== 'undefined') {
    openerRef.current = document.activeElement;
  }
  wasOpenRef.current = Boolean(isOpen);

  useEffect(() => {
    if (!isOpen) return undefined;
    const opener = openerRef.current;
    // Apertura e chiusura di una finestra chiudono la digitazione in corso
    // (undo per azione, store/typingAction).
    endTyping();
    pushDialog({ id, getEl: () => ref.current, onEsc: () => { requestClose('esc'); } });
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) {
      const focusables = getFocusable(el);
      const target = el.querySelector('[data-autofocus]')
        || focusables.find(n => !n.hasAttribute('data-dialog-close'))
        || focusables[0]
        || el;
      try { target.focus?.(); } catch { /* noop */ }
    }
    return () => {
      endTyping();
      removeDialog(id);
      try {
        if (opener && opener !== document.body && document.contains(opener) && typeof opener.focus === 'function') {
          opener.focus();
        } else {
          const below = topDialogElement();
          if (below) below.focus?.();
          else document.body?.focus?.();
        }
      } catch { /* noop */ }
    };
    // `ref` e' stabile; le opzioni passano da optsRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, id, requestClose]);

  const downOnOverlayRef = useRef(false);
  const overlayProps = useMemo(() => ({
    onMouseDown: (e) => { downOnOverlayRef.current = e.target === e.currentTarget; },
    onClick: (e) => {
      const fromOverlay = downOnOverlayRef.current && e.target === e.currentTarget;
      downOnOverlayRef.current = false;
      if (fromOverlay) requestClose('overlay');
    },
  }), [requestClose]);

  const dialogProps = {
    id,
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': labelledBy ? titleId : undefined,
    'aria-busy': busy || undefined,
    tabIndex: -1,
    'data-dialog': '',
    // I clic dentro la finestra non arrivano all'overlay ne' ai genitori
    // React (una finestra aperta da un nodo non seleziona il nodo), come
    // faceva gia' ModalWrapper.
    onClick: (e) => e.stopPropagation(),
  };

  const closeButtonProps = {
    type: 'button',
    onClick: () => { requestClose('button'); },
    disabled: Boolean(busy),
    'aria-label': 'Chiudi',
    'data-dialog-close': '',
  };

  return { ref, id, titleId, requestClose, overlayProps, dialogProps, closeButtonProps, busy: Boolean(busy) };
}
