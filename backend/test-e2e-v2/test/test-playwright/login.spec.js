const { test, expect } = require('../../../../frontend/node_modules/@playwright/test');
const { ADMIN_PASS, ADMIN_USER } = require('./fixtures');

test.use({ storageState: { cookies: [], origins: [] } });

test('login admin: entra nella UI reale', async ({ page }) => {
  await page.goto('/login');
  await expect(page.locator('#lp-username')).toBeVisible();
  await page.fill('#lp-username', ADMIN_USER);
  await page.fill('#lp-password', ADMIN_PASS);
  await page.click('.lp__form button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 });
  expect(new URL(page.url()).pathname).not.toBe('/login');
});

test('login errato: resta su /login con errore', async ({ page }) => {
  await page.goto('/login');
  await page.fill('#lp-username', ADMIN_USER);
  await page.fill('#lp-password', 'password-sicuramente-sbagliata');
  await page.click('.lp__form button[type="submit"]');
  await expect(page.locator('.lp__error')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/login');
});
