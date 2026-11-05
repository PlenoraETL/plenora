/**
 * dialogStack — UNICO stack delle finestre aperte (Fase 5, T4).
 *
 * Ogni modale, wizard, conferma o palette si registra qui tramite
 * `hooks/useDialog` (test di enumerazione: tests/shared/dialogStack.contract).
 * Lo stack possiede:
 *   - Esc: un solo listener; chiude SOLO la finestra in cima (e solo se lo
 *     consente: `closeOnEsc`, non `busy`). Un componente interno che usa Esc
 *     per se' (es. un menu) fa `preventDefault` e la finestra resta aperta;
 *   - Tab: il focus gira solo dentro la finestra in cima;
 *   - scroll della pagina: bloccato finche' c'e' almeno una finestra, poi
 *     torna il valore di prima (non '' quando la modale interna si chiude);
 *   - la regola "c'e' una finestra aperta" (`isShortcutBlocked`) usata da
 *     TUTTE le scorciatoie globali (Ctrl+K, ?, Ctrl+Z/Y, Ctrl+S, Canc, "/").
 */

const FOCUSABLE_SELECTOR = [
  'a[href]:not([disabled])',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(',');

export function getFocusable(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
}

/** @type {{id: string, getEl: () => HTMLElement|null, onEsc: (e: KeyboardEvent) => void}[]} */
const stack = [];
let savedOverflow = null;

function trapTab(entry, e) {
  const el = entry.getEl();
  if (!el) return;
  const items = getFocusable(el);
  if (items.length === 0) { e.preventDefault(); el.focus?.(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (e.shiftKey) {
    if (active === first || !el.contains(active)) { e.preventDefault(); last.focus(); }
  } else if (active === last || !el.contains(active)) {
    e.preventDefault();
    first.focus();
  }
}

function onKeyDown(e) {
  const top = stack[stack.length - 1];
  if (!top) return;
  if (e.key === 'Escape') {
    // Gia' usato da un controllo dentro la finestra (menu, campo in modifica).
    if (e.defaultPrevented) return;
    e.preventDefault();
    top.onEsc(e);
    return;
  }
  if (e.key === 'Tab') trapTab(top, e);
}

export function pushDialog(entry) {
  if (stack.some(d => d.id === entry.id)) return;
  stack.push(entry);
  if (stack.length === 1 && typeof document !== 'undefined') {
    savedOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);
  }
}

export function removeDialog(id) {
  const i = stack.findIndex(d => d.id === id);
  if (i === -1) return;
  stack.splice(i, 1);
  if (stack.length === 0 && typeof document !== 'undefined') {
    document.body.style.overflow = savedOverflow || '';
    savedOverflow = null;
    document.removeEventListener('keydown', onKeyDown);
  }
}

export const isDialogOpen = () => stack.length > 0;

export const topDialogId = () => (stack.length ? stack[stack.length - 1].id : null);

export const isTopDialog = (id) => topDialogId() === id;

/** Elemento della finestra in cima (per il ritorno del focus). */
export const topDialogElement = () => (stack.length ? stack[stack.length - 1].getEl() : null);

/**
 * Regola UNICA delle scorciatoie globali: non agiscono sotto una finestra.
 * `ownDialogId`: la scorciatoia appartiene a quella finestra (es. Ctrl+K
 * che richiude la palette) e agisce se e' lei in cima.
 */
export function isShortcutBlocked(event, { ownDialogId = null } = {}) {
  if (event?.defaultPrevented) return true;
  if (!stack.length) return false;
  return !(ownDialogId && isTopDialog(ownDialogId));
}

/** Il bersaglio e' un campo di testo (le scorciatoie di editing lo lasciano stare). */
export function isTypingTarget(target) {
  if (!target) return false;
  return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || '');
}

/** Solo test: stato dello stack. */
export const __dialogStackSize = () => stack.length;
