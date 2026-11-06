import http from './http';

const DEFAULT_PAGE_SIZE = 200;
const MAX_PAGES = 10_000;
// UI-1 (V10): dopo la prima pagina piena le successive si chiedono a gruppi
// in parallelo, non una dopo l'altra (la lista intera arriva in
// ceil(pagine / PARALLEL_PAGES) giri invece di uno per pagina).
const PARALLEL_PAGES = 4;

/** Fetch every bounded offset page while preserving legacy API shapes. */
export async function fetchAllOffsetPages(
  url,
  {
    params = {},
    pageSize = DEFAULT_PAGE_SIZE,
    extractItems = data => data,
    mergeItems = items => items,
  } = {},
) {
  const getPage = (page) => http.get(url, {
    params: { ...params, limit: pageSize, offset: page * pageSize },
  });
  const allItems = [];
  // Prima pagina da sola: con le liste piccole (il caso normale) basta.
  const first = await getPage(0);
  const firstItems = extractItems(first.data) || [];
  allItems.push(...firstItems);
  if (firstItems.length < pageSize) return mergeItems(allItems, first.data);
  for (let next = 1; next < MAX_PAGES; next += PARALLEL_PAGES) {
    const pages = Array.from({ length: Math.min(PARALLEL_PAGES, MAX_PAGES - next) }, (_, i) => next + i);
    const responses = await Promise.all(pages.map(getPage));
    for (const response of responses) {
      const items = extractItems(response.data) || [];
      allItems.push(...items);
      // Una pagina corta e' l'ultima: quelle chieste dopo sono vuote.
      if (items.length < pageSize) return mergeItems(allItems, response.data);
    }
  }
  throw new Error(`Pagination limit exceeded for ${url}`);
}

/**
 * Tutte le pagine di una lista a CURSORE (`next_cursor` nella risposta,
 * `cursor` nella richiesta dopo; conferma UI A-K10): una dopo l'altra, la
 * fine e' `next_cursor` assente (mai «pagina corta»), quindi una riga
 * saltata dal server non tronca la lista e un caricamento durante la
 * lettura non fa saltare ne' ripetere un elemento.
 */
export async function fetchAllCursorPages(
  url,
  {
    params = {},
    pageSize = DEFAULT_PAGE_SIZE,
    extractItems = data => data,
    mergeItems = items => items,
  } = {},
) {
  const allItems = [];
  let cursor = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await http.get(url, { params: { ...params, limit: pageSize, ...(cursor ? { cursor } : {}) } });
    allItems.push(...(extractItems(response.data) || []));
    cursor = response.data?.next_cursor || null;
    if (!cursor) return mergeItems(allItems, response.data);
  }
  throw new Error(`Pagination limit exceeded for ${url}`);
}
