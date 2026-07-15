import http from './http';

const DEFAULT_PAGE_SIZE = 200;
const MAX_PAGES = 10_000;

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
  const allItems = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await http.get(url, {
      params: { ...params, limit: pageSize, offset: page * pageSize },
    });
    const items = extractItems(response.data) || [];
    allItems.push(...items);
    if (items.length < pageSize) return mergeItems(allItems, response.data);
  }
  throw new Error(`Pagination limit exceeded for ${url}`);
}
