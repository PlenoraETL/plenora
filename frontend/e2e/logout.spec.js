// Logout flow end-to-end.
//
// Copertura:
//  1. Happy path: click sul bottone logout (AppSidebar), verifica
//     redirect a /login e sessionStorage svuotata.
//  2. Doppio logout: cliccare logout dopo che gia` l'app ha
//     reagito non deve causare crash (regression guard contro
//     race conditions su AuthContext + authBus, vedi audit
//     originale).
//
// Pre-requisito: stack `up` (vedi playwright.config.js).
//
// Auth model: la fixture `authedPage` di e2e/fixtures.js inietta
// JWT + user info in sessionStorage prima del primo goto, cosi`
// l'AppSidebar monta gia` "loggato" e mostra il bottone logout.

import { test, expect } from './fixtures.js';

const TOKEN_KEY = 'urby_token';
const USER_KEY = 'urby_user';

test('happy: click logout reindirizza a /login', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });

  // Sanity: la sidebar e' montata e il bottone logout esiste.
  // Selettore stabile per class (l'icona testuale ⎋ varia poco).
  const logoutBtn = page.locator('.app-sidebar__logout-btn');
  await expect(logoutBtn).toBeVisible({ timeout: 10_000 });

  await logoutBtn.click();

  // L'AuthContext deve fare clear sessionStorage + redirect a /login.
  await page.waitForURL((url) => url.pathname === '/login', {
    timeout: 10_000,
  });
  expect(new URL(page.url()).pathname).toBe('/login');

  // sessionStorage clearato: niente token residuo che possa essere
  // ri-usato da una nuova tab o da un fetch in flight.
  const token = await page.evaluate((k) => sessionStorage.getItem(k), TOKEN_KEY);
  const user = await page.evaluate((k) => sessionStorage.getItem(k), USER_KEY);
  expect(token, 'token residuo post logout').toBeNull();
  expect(user, 'user residuo post logout').toBeNull();
});

test('idempotent: secondo click logout post-redirect non crasha', async ({ page }) => {
  // Step 1: logout normale.
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.locator('.app-sidebar__logout-btn').click();
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 10_000 });

  // Step 2: catch JS error / page error mentre proviamo a tornare
  // sulla home (route guard deve rimbalzare di nuovo a /login senza
  // crash, niente "Cannot read undefined" su user/token mancante).
  /** @type {string[]} */
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  /** @type {string[]} */
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 10_000 });

  const fatal = consoleErrors.filter((e) =>
    /TypeError|ReferenceError|Cannot read|undefined is not/.test(e)
    && !/ResizeObserver|favicon/i.test(e),
  );
  expect(fatal, `console fatal post-logout: ${fatal.join(' | ')}`).toHaveLength(0);
  expect(pageErrors, `pageerror post-logout: ${pageErrors.join(' | ')}`).toHaveLength(0);
});
