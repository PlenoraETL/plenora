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
 * Regression spec per plenora-bug-frontend-pages P0:
 *   I 3 editor (Estrai/Manipola/Arricchisci) avevano
 *   `handleBack = () => navigate('/')` senza guard su `isDirty`. Cliccare
 *   "← Indietro" con modifiche non salvate buttava le modifiche silenziosamente.
 *
 * Post-fix: `useUnsavedChangesGuard` mostra `window.confirm` se isDirty.
 * Cancel → resta sull'editor. OK → navigate('/').
 *
 * Strategia di dirty: aggiungere un nodo via toolbar ("+ Transform" su Manipola)
 * triggera `setNodes` strutturale che marca isDirty=true nello store
 * (vedi `useCanvasStore.setNodes` heuristica oldIds + new id).
 */

async function openManipola(page, projectId) {
  await page.goto(`/editor/manipola/${projectId}`, { waitUntil: 'networkidle' });
  expect(new URL(page.url()).pathname).toBe(`/editor/manipola/${projectId}`);
  await expect(page.locator('.react-flow').first()).toBeAttached();
}

test('ManipolaEditor back guard: cancel mantiene editor, modifiche preservate', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_back_guard_cancel'),
      project_type: 'manipola',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);

      // Dirty trigger: aggiunge un transform node via toolbar.
      // setNodes con nuovo ID -> structural=true -> isDirty=true.
      await page.locator('.manipola-toolbar__btn--transform').click();
      await expect(page.locator('.transform-node')).toBeVisible();

      // Intercetta il window.confirm e cancella.
      const dialogPromise = page.waitForEvent('dialog');
      await page.locator('.manipola-toolbar__btn--back').click();
      const dialog = await dialogPromise;
      expect(dialog.message()).toMatch(/modifiche non salvate/i);
      await dialog.dismiss();

      // Cancel → URL non cambia, editor e nodo restano montati.
      expect(new URL(page.url()).pathname).toBe(`/editor/manipola/${projectId}`);
      await expect(page.locator('.transform-node')).toBeVisible();
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('ManipolaEditor back guard: accept naviga alla dashboard', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_back_guard_accept'),
      project_type: 'manipola',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);

      await page.locator('.manipola-toolbar__btn--transform').click();
      await expect(page.locator('.transform-node')).toBeVisible();

      const dialogPromise = page.waitForEvent('dialog');
      await page.locator('.manipola-toolbar__btn--back').click();
      const dialog = await dialogPromise;
      expect(dialog.message()).toMatch(/modifiche non salvate/i);
      await dialog.accept();

      // Accept → naviga a `/` (dashboard / project list).
      await expect.poll(() => new URL(page.url()).pathname, { timeout: 5000 }).toBe('/');
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});

test('ManipolaEditor back guard: clean state non mostra dialog', async ({ api, page }) => {
  const createdProjects = [];

  try {
    const projectId = await createProject(api, {
      name: uniqueName('pw_back_guard_clean'),
      project_type: 'manipola',
      canvas: { nodes: [], edges: [] },
    });
    createdProjects.push(projectId);

    await assertNoFatalBrowserErrors(page, async () => {
      await openManipola(page, projectId);
      // Nessuna modifica utente -> isDirty=false dopo markClean() del load.
      // Cliccare "Indietro" deve navigare senza prompt.

      let dialogFired = false;
      page.once('dialog', async (d) => { dialogFired = true; await d.dismiss(); });

      await page.locator('.manipola-toolbar__btn--back').click();
      await expect.poll(() => new URL(page.url()).pathname, { timeout: 5000 }).toBe('/');
      expect(dialogFired).toBe(false);
    });
  } finally {
    await deleteProjects(api, createdProjects);
  }
});
