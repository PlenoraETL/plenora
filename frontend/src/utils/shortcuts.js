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

/*
 * Regola dei tasti che scrivono un carattere (UI conferma C-K3): si
 * riconoscono dal CARATTERE prodotto (`event.key`), mai dalla combinazione
 * con cui lo si ottiene. Maiuscolo serve a produrre il carattere su un layout
 * e non su un altro (`?` = Maiusc+/ sulla tastiera US, Maiusc+' su quella
 * italiana; `/` = Maiusc+7 sulla italiana), quindi non conta; ⌘/Ctrl e Alt
 * (anche AltGr, che arriva come Ctrl+Alt) lo escludono. Prima «/» italiano
 * (Maiusc+7) apriva insieme la palette dei passi e la guida dei tasti.
 */
const typed = (...chars) => (e) => !mod(e) && !e.altKey && chars.includes(String(e.key || ''));

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
    match: typed('?'),
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
    match: typed('/', 'a', 'A'),
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
  // Comandi del canvas (UI-3, U27: components/editor/canvasCommands), con il
  // fuoco sul canvas e mai mentre si scrive o con testo selezionato.
  {
    id: 'select_all', group: 'Editor', keys: ['⌘/Ctrl', 'A'],
    label: 'Seleziona tutti i nodi del canvas',
    match: (e) => mod(e) && !e.shiftKey && !e.altKey && key(e, 'a'),
  },
  {
    id: 'copy', group: 'Editor', keys: ['⌘/Ctrl', 'C'],
    label: 'Copia i nodi selezionati (con i loro collegamenti)',
    match: (e) => mod(e) && !e.shiftKey && !e.altKey && key(e, 'c'),
  },
  {
    id: 'paste', group: 'Editor', keys: ['⌘/Ctrl', 'V'],
    label: 'Incolla i nodi copiati',
    match: (e) => mod(e) && !e.shiftKey && !e.altKey && key(e, 'v'),
  },
  {
    id: 'duplicate', group: 'Editor', keys: ['⌘/Ctrl', 'D'],
    label: 'Duplica il passo selezionato con gli stessi ingressi',
    match: (e) => mod(e) && !e.shiftKey && !e.altKey && key(e, 'd'),
  },
  {
    id: 'search_canvas', group: 'Editor', keys: ['⌘/Ctrl', 'F'],
    // Fuori dall'editor lo stesso tasto e' la ricerca del browser: le liste
    // lunghe (components/common/VirtualList) si disegnano intere perche' la
    // ricerca trovi anche le righe lontane (conferma A-K6).
    label: 'Cerca un nodo nel canvas e vai lì',
    match: (e) => mod(e) && !e.shiftKey && !e.altKey && key(e, 'f'),
  },
  // Scelta dichiarata (conferma finale G9): «C» vale anche con Maiusc o con
  // il Blocco maiuscole, come «A» di add_step. Le lettere delle scorciatoie
  // indicano il TASTO (la guida scrive «C»), e chi ha il Blocco maiuscole
  // attivo non deve trovare il comando spento; Maiusc+C non ha altri usi.
  {
    id: 'connect', group: 'Editor', keys: ['C'],
    label: 'Collega il nodo selezionato a un altro (da tastiera)',
    match: typed('c', 'C'),
  },
  {
    id: 'editor_guide', group: 'Editor', keys: ['F1'],
    label: 'Guida dell’editor, sulla parte del nodo selezionato',
    match: (e) => e.key === 'F1',
  },
]);

const BY_ID = new Map(SHORTCUTS.map(s => [s.id, s]));

/** L'evento corrisponde alla scorciatoia? Un id assente e' un errore di programmazione. */
export function matchesShortcut(id, event) {
  const s = BY_ID.get(id);
  if (!s) throw new Error(`[shortcuts] scorciatoia non dichiarata: ${id}`);
  return Boolean(event) && s.match(event);
}

/**
 * Tasti di una scorciatoia come si scrivono nei testi (titoli dei pulsanti,
 * descrizioni del glossario, guide, messaggi): uno per combinazione,
 * «Ctrl+S», «/», «Ctrl+⇧+Z». Unica fonte dei tasti nei testi (C-K11): nessun
 * testo dell'app scrive i tasti a mano (tests/shared/shortcuts.contract).
 * Accetta la riga o il suo id.
 */
export function shortcutParts(idOrRow) {
  const s = typeof idOrRow === 'string' ? BY_ID.get(idOrRow) : idOrRow;
  if (!s) throw new Error(`[shortcuts] scorciatoia non dichiarata: ${idOrRow}`);
  return [s.keys, s.altKeys].filter(Boolean).map(k => k.join('+').replace('⌘/Ctrl', 'Ctrl'));
}

/** Tasti di una scorciatoia in un testo: «Ctrl+S», «/ o A». */
export const shortcutText = (idOrRow) => shortcutParts(idOrRow).join(' o ');

/** Evento che apre la guida (palette, pulsante «?»). */
export const SHORTCUTS_EVENT = 'plenora:open-shortcuts';

export const openShortcutsHelp = () => window.dispatchEvent(new CustomEvent(SHORTCUTS_EVENT));
