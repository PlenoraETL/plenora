/**
 * shortcuts.js — UNICA tabella delle scorciatoie da tastiera (UI-4, U28).
 *
 * La stessa riga dice quali tasti la attivano (`match`, usata dai gestori
 * dei tasti: CommandPalette, guida, editor, canvas, finestre) e come si
 * presenta nella guida (components/common/KeyboardShortcutsHelp). Una
 * scorciatoia gestita e' quindi sempre nella guida e la guida non elenca
 * tasti che non fanno nulla: tests/shared/shortcuts.contract.test.js vieta
 * i confronti sui tasti scritti a mano nei gestori globali.
 *
 * Le regole su QUANDO una scorciatoia vale (non nei campi di testo, non
 * sotto una finestra aperta: utils/dialogStack) restano nei gestori.
 */
const mod = (e) => Boolean(e.ctrlKey || e.metaKey);
const key = (e, k) => String(e.key || '').toLowerCase() === k;

export const SHORTCUTS = Object.freeze([
  // ── Ovunque ──────────────────────────────────────────────────────────
  {
    id: 'palette', group: 'Ovunque', keys: ['⌘/Ctrl', 'K'],
    label: 'Apri la palette: vai a una pagina o a un progetto',
    match: (e) => mod(e) && key(e, 'k'),
  },
  {
    id: 'help', group: 'Ovunque', keys: ['?'],
    label: 'Mostra questa guida (anche dal pulsante «?»)',
    match: (e) => e.key === '?' || (e.key === '/' && e.shiftKey),
  },
  {
    id: 'close', group: 'Ovunque', keys: ['Esc'],
    label: 'Chiudi la finestra o il menu in primo piano',
    match: (e) => e.key === 'Escape',
  },

  // ── Editor ───────────────────────────────────────────────────────────
  {
    id: 'save', group: 'Editor', keys: ['⌘/Ctrl', 'S'],
    label: 'Salva il progetto',
    match: (e) => mod(e) && key(e, 's'),
  },
  {
    id: 'add_step', group: 'Editor', keys: ['/'], altKeys: ['A'],
    label: 'Aggiungi un passo (con il fuoco sul canvas)',
    match: (e) => !mod(e) && !e.altKey && (e.key === '/' || key(e, 'a')),
  },
  {
    id: 'undo', group: 'Editor', keys: ['⌘/Ctrl', 'Z'],
    label: 'Annulla l’ultima modifica',
    match: (e) => mod(e) && key(e, 'z') && !e.shiftKey,
  },
  {
    id: 'redo', group: 'Editor', keys: ['⌘/Ctrl', '⇧', 'Z'], altKeys: ['⌘/Ctrl', 'Y'],
    label: 'Ripristina la modifica annullata',
    match: (e) => mod(e) && ((key(e, 'z') && e.shiftKey) || key(e, 'y')),
  },
  {
    id: 'delete', group: 'Editor', keys: ['Canc'], altKeys: ['⌫'],
    label: 'Elimina il passo o il collegamento selezionato (chiede conferma)',
    match: (e) => e.key === 'Delete' || e.key === 'Backspace',
  },
]);

const BY_ID = new Map(SHORTCUTS.map(s => [s.id, s]));

/** L'evento corrisponde alla scorciatoia? Un id assente e' un errore di programmazione. */
export function matchesShortcut(id, event) {
  const s = BY_ID.get(id);
  if (!s) throw new Error(`[shortcuts] scorciatoia non dichiarata: ${id}`);
  return Boolean(event) && s.match(event);
}

/** Evento che apre la guida (palette, pulsante «?»). */
export const SHORTCUTS_EVENT = 'plenora:open-shortcuts';

export const openShortcutsHelp = () => window.dispatchEvent(new CustomEvent(SHORTCUTS_EVENT));
