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
 * Regression spec per i drift FE↔BE chiusi nel cluster contracts-schemas
 * (sessione 9): smoke E2E sul mount dell'ArricchisciEditor con BE stub
 * per `validate`/`estimate` che ritornano la nuova shape
 * (`null_param_rows_count`). Verifica che la pagina monti senza
 * console errors e che le chiamate intercette ricevano la shape attesa.
 *
 * Per la verifica del SHAPE del payload (static_parameters incluso) e
 * dell'UI render del null_param_rows_count, vedere lo unit test
 * vitest `frontend/tests/api/enrichment.validate-payload-shape.test.js`
 * — quello e' il livello giusto, e' deterministico e veloce.
 */

test('ArricchisciEditor monta senza crash con stub BE shape post-fix', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const arricchisciId = await createProject(api, {
      name: uniqueName('pw_drift_smoke'),
      project_type: 'arricchisci',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(arricchisciId);

    // Stub: BE risponde con la nuova shape che include
    // `null_param_rows_count`. Il FE post-fix deve gestirla senza
    // errori (pre-fix il campo non era mai letto, ma una shape extra
    // non doveva rompere niente — questo test cattura regressioni
    // future di parsing strict).
    await page.route('**/api/enrichment/validate', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          valid: true,
          total_rows: 100,
          valid_rows: 75,
          invalid_rows: 0,
          missing_columns: [],
          null_param_rows_count: 25,
          warnings: [],
        }),
      });
    });

    await assertNoFatalBrowserErrors(page, async () => {
      await page.goto(`/arricchisci/${arricchisciId}`, { waitUntil: 'networkidle' });
      expect(new URL(page.url()).pathname).toBe(`/arricchisci/${arricchisciId}`);
      await expect(page.locator('.react-flow').first()).toBeAttached();
    });
  } finally {
    await page.unroute('**/api/enrichment/validate').catch(() => {});
    await deleteProjects(api, createdProjects);
  }
});
