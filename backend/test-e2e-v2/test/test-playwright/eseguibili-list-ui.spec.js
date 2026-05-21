const {
  assertNoFatalBrowserErrors,
  createProject,
  deleteProjects,
  expect,
  test,
  uniqueName,
} = require('./fixtures');

test.describe.configure({ timeout: 60000 });

/**
 * Regression spec per plenora-bug-contracts-flows P0:
 *   `frontend/src/pages/EseguibiliList.jsx:70` chiamava `all.filter(...)`
 *   sul risultato di `listProjects()` che e' `{projects: [...]}` (oggetto),
 *   non un array. Crash garantito: `TypeError: all.filter is not a function`.
 *
 * Pre-fix: clicking "➕ Nuovo Eseguibile" crashava il dialog (lista vuota +
 * console TypeError). assertNoFatalBrowserErrors avrebbe failed.
 *
 * Post-fix: il selector mostra correttamente i progetti arricchisci.
 */
test('Eseguibili UI: "Nuovo Eseguibile" mostra selector con progetti arricchisci', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const arricchisciName = uniqueName('pw_eseguibili_arr');
    const arricchisciId = await createProject(api, {
      name: arricchisciName,
      project_type: 'arricchisci',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(arricchisciId);

    // Progetto manipola, NON deve apparire nel selector arricchisci-only
    const manipolaName = uniqueName('pw_eseguibili_man');
    const manipolaId = await createProject(api, {
      name: manipolaName,
      project_type: 'manipola',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(manipolaId);

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto('/eseguibili', { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).toBe('/eseguibili');

      const newButton = page.locator('.esl__btn--new');
      await expect(newButton).toBeVisible();
      await newButton.click();

      const selector = page.getByRole('dialog', { name: /progetto Arricchisci/i });
      await expect(selector).toBeVisible();

      // Pre-fix sarebbe stata mostrata sempre "Nessun progetto Arricchisci"
      // (filter crashava prima di popolare la lista). Post-fix la lista
      // contiene almeno il progetto arricchisci appena creato.
      await expect(
        selector.locator('.esl__selector-item-name', { hasText: arricchisciName })
      ).toBeVisible();

      // Il manipola non deve apparire (filter su project_type='arricchisci').
      await expect(
        selector.locator('.esl__selector-item-name', { hasText: manipolaName })
      ).toHaveCount(0);
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});
