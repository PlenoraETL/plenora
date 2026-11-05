/**
 * useSearch — LA ricerca delle pagine e dei pannelli (Fase 5, voce P28
 * «ricerca Invio contro digitazione»).
 *
 * Regola unica, una sola componente (components/common/SearchField):
 *  - si filtra MENTRE si scrive, dopo una pausa dichiarata (SEARCH_PAUSE_MS);
 *    Invio applica subito, svuotare (×, Esc, campo vuoto) applica subito;
 *  - lato client: `search.filter(items, toValues)` (o `search.matches`);
 *  - lato server: `search.query` nella chiave di hooks/useResource, con la
 *    stessa pausa; useResource tiene una richiesta alla volta (la nuova
 *    annulla la vecchia, vince l'ultima);
 *  - confronto senza maiuscole/minuscole e senza accenti; ogni parola
 *    della ricerca deve comparire (in qualunque ordine).
 *
 * Il test tests/shared/searchField.contract.test.jsx vieta gli input di
 * ricerca fatti a mano fuori da SearchField.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

/** Pausa dopo l'ultimo tasto prima di applicare la ricerca (ms). */
const SEARCH_PAUSE_MS = 250;

const DIACRITICS = /[̀-ͯ]/g;

/** Testo confrontabile: minuscolo, senza accenti. */
function normalizeSearchText(value) {
  return String(value ?? '').normalize('NFD').replace(DIACRITICS, '').toLowerCase();
}

/**
 * true se ogni parola di `query` compare in almeno uno dei valori.
 * @param {string} query
 * @param {...any} values
 */
function matchesSearch(query, ...values) {
  const terms = normalizeSearchText(query).split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = values.flat(Infinity).filter((v) => v !== null && v !== undefined).map(normalizeSearchText).join('\n');
  return terms.every((t) => haystack.includes(t));
}

/**
 * @param {{initial?: string}} [opts]
 * @returns {{
 *   text: string, setText: (v: string) => void, query: string,
 *   active: boolean, pending: boolean, clear: () => void, flush: () => void,
 *   matches: (...values: any[]) => boolean,
 *   filter: <T>(items: T[], toValues: (item: T) => any) => T[],
 *   pause: number,
 * }}
 */
export function useSearch({ initial = '' } = {}) {
  const [text, setTextState] = useState(initial);
  const [query, setQuery] = useState(() => String(initial).trim());
  const trimmed = text.trim();

  useEffect(() => {
    if (trimmed === query) return undefined;
    // Svuotare si applica subito: nessuna lista filtrata da un testo che non c'e' piu'.
    if (trimmed === '') { setQuery(''); return undefined; }
    const timer = setTimeout(() => setQuery(trimmed), SEARCH_PAUSE_MS);
    return () => clearTimeout(timer);
  }, [trimmed, query]);

  const setText = useCallback((value) => setTextState(String(value ?? '')), []);
  const clear = useCallback(() => { setTextState(''); setQuery(''); }, []);
  const flush = useCallback(() => setQuery(trimmed), [trimmed]);
  const matches = useCallback((...values) => matchesSearch(query, ...values), [query]);
  const filter = useCallback(
    (items, toValues) => (query ? (items || []).filter((item) => matchesSearch(query, toValues(item))) : (items || [])),
    [query],
  );

  return useMemo(() => ({
    text, setText, query, active: query !== '', pending: trimmed !== query, clear, flush, matches, filter,
    pause: SEARCH_PAUSE_MS,
  }), [text, setText, query, trimmed, clear, flush, matches, filter]);
}

/**
 * Pagina (o quantita' mostrata) legata alla ricerca: ogni ricerca nuova,
 * anche il ritorno a una precedente, riparte da `first`.
 * @param {{query: string}} search
 * @param {number} first
 * @returns {[number, (value: number | ((prev: number) => number)) => void]}
 */
export function useSearchPage(search, first) {
  const [state, setState] = useState({ query: search.query, page: first });
  // Stato derivato aggiornato durante il render (nessun giro con la pagina vecchia).
  if (state.query !== search.query) setState({ query: search.query, page: first });
  const page = state.query === search.query ? state.page : first;
  const setPage = useCallback(
    (value) => setState((s) => ({ query: s.query, page: typeof value === 'function' ? value(s.page) : value })),
    [],
  );
  return [page, setPage];
}

export default useSearch;
