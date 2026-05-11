// Login flow happy path + 1 error path.
//
// E' l'unico spec che NON usa lo storageState seeded da global.setup:
// vogliamo testare proprio il path di autenticazione.

import { test, expect } from '@playwright/test';

const ADMIN_USER = process.env.PLENORA_TEST_USER || 'admin_plenora';
const ADMIN_PASS = process.env.PLENORA_TEST_PASS || 'admin_plenora_2026';

// Disabilita lo storageState seeded — qui partiamo loggati fuori.
test.use({ storageState: { cookies: [], origins: [] } });

test('happy path: admin login redirects off /login', async ({ page }) => {
  await page.goto('/login');

  // Sanity render: il form esiste prima di interagirci. Catch precoce
  // se il bundle fallisce a montare (caso classico: errore JS).
  await expect(page.locator('#lp-username')).toBeVisible();
  await expect(page.locator('#lp-password')).toBeVisible();

  await page.fill('#lp-username', ADMIN_USER);
  await page.fill('#lp-password', ADMIN_PASS);
  await page.click('.lp__form button[type="submit"]');

  await page.waitForURL((url) => !url.pathname.startsWith('/login'), {
    timeout: 15_000,
  });
  expect(new URL(page.url()).pathname).not.toBe('/login');
});

test('wrong password: stays on /login with error', async ({ page }) => {
  await page.goto('/login');
  await page.fill('#lp-username', ADMIN_USER);
  await page.fill('#lp-password', 'definitely-wrong-password');
  await page.click('.lp__form button[type="submit"]');

  // .lp__error e' il banner di errore inline. Aspettiamo che compaia.
  await expect(page.locator('.lp__error')).toBeVisible({ timeout: 10_000 });
  // E che non ci abbia rediretti via:
  expect(new URL(page.url()).pathname).toBe('/login');
});
