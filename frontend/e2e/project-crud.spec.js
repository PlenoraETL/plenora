// CRUD progetti via UI: create + delete.
//
// Copertura:
//  1. Apertura modal "Nuovo Progetto" da bottone in header.
//  2. Validazione lato client: submit con nome vuoto NON crea
//     (modal resta aperto, errore inline).
//  3. Happy path: crea progetto manipola, verifica appare nella
//     lista (testo cercabile per nome unico).
//  4. Cleanup via API (afterEach) — non dipendiamo dal flow UI di
//     delete per avere DB pulito tra run, e' fragile.
//
// Scelgo `manipola` come tipo: non richiede `connection_id` —
// ESTRAI sarebbe bloccato senza una connessione seeded e
// renderebbe il test inutile in ambienti dove le connessioni sono
// gestite manualmente.
//
// Pre-requisito: stack `up`.

import { request as pwRequest } from '@playwright/test';
import { test, expect, API } from './fixtures.js';

/** @type {string[]} */
const createdProjectIds = [];
/** @type {string} */
let bearer;

test.beforeAll(async ({ adminSession }) => {
  bearer = adminSession.access_token;
});

test.afterAll(async () => {
  if (!bearer || createdProjectIds.length === 0) return;
  const api = await pwRequest.newContext({ baseURL: API });
  for (const id of createdProjectIds) {
    await api.delete(`/api/projects/${id}`, {
      headers: { Authorization: `Bearer ${bearer}` },
    }).catch(() => {});
  }
  await api.dispose();
});

/**
 * Helper: cerca tra i progetti via API e cleanup quelli che matchano
 * il prefisso (i.e. orphan da run precedenti).
 */
async function cleanupOrphans(prefix) {
  const api = await pwRequest.newContext({ baseURL: API });
  try {
    const resp = await api.get('/api/projects', {
      headers: { Authorization: `Bearer ${bearer}` },
    });
    if (!resp.ok()) return;
    const list = await resp.json();
    for (const proj of (Array.isArray(list) ? list : list?.projects || [])) {
      const name = proj?.name || '';
      if (name.startsWith(prefix)) {
        await api.delete(`/api/projects/${proj.id || proj.metadata?.id}`, {
          headers: { Authorization: `Bearer ${bearer}` },
        }).catch(() => {});
      }
    }
  } finally {
    await api.dispose();
  }
}

test('apertura modal Nuovo Progetto dall\'header', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });

  // Bottone "+ Nuovo Progetto" in header (visibile solo se user ha
  // permesso di create, che admin_plenora ha).
  const newBtn = page.getByRole('button', { name: /Nuovo Progetto/i });
  await expect(newBtn).toBeVisible({ timeout: 10_000 });

  await newBtn.click();

  // ModalWrapper rendera` un dialog con titolo "Nuovo progetto Estrai"
  // (default type=estrai). Match parziale per non legarsi al type.
  await expect(page.getByText(/Nuovo progetto/i).first()).toBeVisible({ timeout: 5_000 });

  // Il campo Nome ha placeholder "Es. 'Catasto Campania Q2'".
  await expect(page.locator('input[placeholder*="Catasto"]')).toBeVisible();
});

test('validation: submit con nome vuoto non chiude modal', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuovo Progetto/i }).click();

  // Seleziona manipola (no connection richiesta).
  await page.getByRole('button', { name: /Manipola/i }).click();

  // Click "Crea Progetto" senza compilare nome.
  const createBtn = page.getByRole('button', { name: /Crea Progetto/i });
  await createBtn.click();

  // Modal deve ancora essere visibile: errore inline o input rosso
  // o semplicemente niente effetto. Il check piu` robusto e' che
  // il titolo del modal e' ancora a schermo dopo 1s.
  await page.waitForTimeout(800);
  await expect(page.getByText(/Nuovo progetto/i).first()).toBeVisible();
});

test('happy: crea progetto manipola via UI, appare in lista', async ({ page }) => {
  const stamp = `pw-crud-${Date.now()}`;
  await cleanupOrphans('pw-crud-');

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /Nuovo Progetto/i }).click();

  // Seleziona Manipola (no connection).
  await page.getByRole('button', { name: /Manipola/i }).click();

  // Compila nome.
  await page.locator('input[placeholder*="Catasto"]').fill(stamp);

  // Submit.
  await page.getByRole('button', { name: /Crea Progetto/i }).click();

  // Modal si chiude e il progetto compare nella lista. Match per testo.
  await expect(page.getByText(stamp, { exact: false })).toBeVisible({
    timeout: 15_000,
  });

  // Recupera l'id via API per cleanup (la lista UI non espone l'id
  // direttamente come data attribute stabile).
  const api = await pwRequest.newContext({ baseURL: API });
  try {
    const resp = await api.get('/api/projects', {
      headers: { Authorization: `Bearer ${bearer}` },
    });
    const list = await resp.json();
    const arr = Array.isArray(list) ? list : list?.projects || [];
    const created = arr.find((p) => (p?.name || '') === stamp);
    if (created) {
      createdProjectIds.push(created.id || created.metadata?.id);
    }
  } finally {
    await api.dispose();
  }
});
