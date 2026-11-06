/**
 * paletteMemory — preferiti e recenti della palette «Cosa vuoi fare?»
 * (UI-3, U29). Prima «Più usate» era una lista fissa uguale per tutti.
 *
 * Per editor (`scope`: estrai, manipola, arricchisci, ...) e per browser:
 * comodita' della persona, non dati del progetto. Ogni lettura e scrittura
 * e' protetta (finestra privata, dati del sito bloccati): senza memoria la
 * palette funziona come prima.
 *
 * Una voce si riconosce da `kind:id`; preferiti e recenti si risolvono
 * sulle voci ATTUALI dei gruppi, cosi' un'operazione o una tabella che non
 * esiste piu' sparisce da sola.
 */
const KEY = (scope) => `plenora.palette.${scope}`;
const RECENT_MAX = 6;

export const itemKey = (item) => `${item.kind || ''}:${item.id}`;

function read(scope) {
  try {
    const raw = window.localStorage.getItem(KEY(scope));
    const parsed = raw ? JSON.parse(raw) : null;
    return {
      favorites: Array.isArray(parsed?.favorites) ? parsed.favorites.map(String) : [],
      recent: Array.isArray(parsed?.recent) ? parsed.recent.map(String) : [],
    };
  } catch {
    return { favorites: [], recent: [] };
  }
}

function write(scope, memory) {
  try { window.localStorage.setItem(KEY(scope), JSON.stringify(memory)); } catch { /* memoria non disponibile */ }
}

export const readPaletteMemory = (scope) => (scope ? read(scope) : { favorites: [], recent: [] });

/** Registra una scelta: in cima ai recenti, senza doppioni. */
export function rememberPick(scope, item) {
  if (!scope) return readPaletteMemory(scope);
  const memory = read(scope);
  const key = itemKey(item);
  memory.recent = [key, ...memory.recent.filter(k => k !== key)].slice(0, RECENT_MAX);
  write(scope, memory);
  return memory;
}

/** Aggiunge o toglie una voce dai preferiti. */
export function toggleFavorite(scope, item) {
  const memory = read(scope);
  const key = itemKey(item);
  memory.favorites = memory.favorites.includes(key)
    ? memory.favorites.filter(k => k !== key)
    : [...memory.favorites, key];
  if (scope) write(scope, memory);
  return memory;
}

/** Gruppi «Preferiti» e «Recenti» dalle voci attuali (solo senza ricerca). */
export function memoryGroups(groups, memory) {
  const byKey = new Map();
  groups.forEach(g => g.items.forEach(item => { if (!byKey.has(itemKey(item))) byKey.set(itemKey(item), item); }));
  const resolve = (keys) => keys.map(k => byKey.get(k)).filter(Boolean);
  const out = [];
  const favorites = resolve(memory.favorites);
  const recent = resolve(memory.recent).filter(i => !memory.favorites.includes(itemKey(i)));
  if (favorites.length) out.push({ label: 'Preferiti', items: favorites, onlyWhenEmpty: true, memory: true });
  if (recent.length) out.push({ label: 'Recenti', items: recent, onlyWhenEmpty: true, memory: true });
  return out;
}
